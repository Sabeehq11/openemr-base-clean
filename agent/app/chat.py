"""POST /v1/chat: signed request in, Server-Sent Events out.

Events: `step` (a tool ran), then exactly one of `answer` | `declined` (refusal text, plus cited recorded medications for dosing) | `degraded` | `denied` | `error`, then `end`.
The model's draft goes through a `Finalizer` (the verifier) before anything is emitted as `answer`.
"""
from __future__ import annotations

import json
from typing import AsyncIterator, Callable, Protocol

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, StreamingResponse

from contracts import AnswerDraft, ToolEnvelope, VerifiedAnswer

from . import tracing
from .conversation import ConversationStore
from .coverage import coverage_notes
from .advice import AdviceKind, decline_text
from .decline import (MEDICATIONS_UNAVAILABLE, NO_MEDICATIONS_NOTE, draft_for, drug_names, select)
from .gateway import AuditSink, Gateway
from .llm import LLMClient
from .loop import AgentLoop, Caller
from .prompts import restore_patient_reference
from .sources import all_cards, attach_sources
from .tools import ToolRunner
from .tracing import TraceStore

GENERIC_DENIAL = {"message": "You do not have access to this patient."}
GENERIC_ERROR = {"message": "The assistant could not produce a verified answer. Please try again."}
MAX_QUESTION_CHARS = 1000
OVERLOADED = {"error": "overloaded", "message": "The assistant is busy. Please try again in a few seconds."}
SHED_RETRY_AFTER_S = 5


class Finalizer(Protocol):
    def __call__(self, draft: AnswerDraft, envelopes: list[ToolEnvelope]) -> VerifiedAnswer: ...


def fail_closed_finalizer(draft: AnswerDraft, envelopes: list[ToolEnvelope]) -> VerifiedAnswer:
    """Releases nothing. Used when no verifier is wired in, so an unverified claim can never be shown."""
    return VerifiedAnswer(claims=[], dropped_count=len(draft.claims), notes=["No verifier configured."])


def sse(event: str, data: object) -> str:
    return f"event: {event}\ndata: {json.dumps(data, separators=(',', ':'))}\n\n"


def declined_payload(ev: dict, finalizer: Finalizer) -> dict:
    """The refusal, plus the recorded medications when an authorized tool returned them.

    The facts are built by the server from record fields (never by the model) and pass through the same verifier as a
    model answer, so each is cited and only cited, recorded content is shown. A failed read is said to be unavailable
    and is never presented as an empty list.
    """
    meds = select(ev["question"], ev.get("medications"))
    payload: dict = {"kind": ev["kind"].value, "message": decline_text(ev["kind"], drug_names(meds)),
                     "claims": [], "sources": [], "notes": []}
    if ev["kind"] is not AdviceKind.TREATMENT:
        return payload
    envelope = ev.get("medications")
    if ev.get("unavailable") or envelope is None:
        payload["notes"].append(MEDICATIONS_UNAVAILABLE)
        return payload
    if not meds:
        payload["notes"].append(NO_MEDICATIONS_NOTE if envelope.empty else "No active medications are recorded.")
        return payload
    answer = finalizer(draft_for(meds), [envelope])
    answer = attach_sources(answer, [envelope])
    shown = json.loads(answer.model_dump_json())
    payload["claims"], payload["sources"] = shown["claims"], shown["sources"]
    payload["notes"] = shown["notes"]
    return payload


def _raw(envelopes: list[ToolEnvelope]) -> list[dict]:
    return [json.loads(e.model_dump_json()) for e in envelopes]


class LoadGauge:
    """Chats running now and chats refused with 503 since the process started. Counts only; no identifiers.

    There is no queue: a chat either starts at once or, when MAX_INFLIGHT_CHATS is set and reached, is shed. So the
    honest load numbers are "in flight" and "shed", not a queue depth.
    """

    def __init__(self, cap: int = 0) -> None:
        self.cap, self.inflight, self.shed = cap, 0, 0  # single event loop, so plain integers are safe

    def snapshot(self) -> dict[str, int]:
        return {"inflight": self.inflight, "shed": self.shed, "cap": self.cap}


def build_chat_router(gateway: Gateway, runner: ToolRunner, llm_factory: Callable[[], LLMClient],
                      finalizer: Finalizer, store: ConversationStore, audit: AuditSink,
                      traces: TraceStore | None = None, model: str = "", max_inflight: int = 0,
                      gauge: LoadGauge | None = None) -> APIRouter:
    router = APIRouter(prefix="/v1")
    gauge = gauge or LoadGauge(max_inflight)

    @router.post("/chat")
    async def chat(request: Request):
        ctx = await gateway.verify(request)
        try:
            body = json.loads(ctx.body)
            patient, question = body["patient_uuid"], body["question"]
            conv_id = body.get("conversation_id")
            if not (isinstance(patient, str) and isinstance(question, str) and 0 < len(question) <= MAX_QUESTION_CHARS
                    and (conv_id is None or isinstance(conv_id, str))):
                raise TypeError
        except (ValueError, KeyError, TypeError):
            return JSONResponse({"error": "invalid_request", "message": "Malformed request."}, status_code=400)
        if max_inflight and gauge.inflight >= max_inflight:  # after verification, so only signed callers can take a slot
            gauge.shed += 1
            audit.record("chat_shed", user_uuid=ctx.user_uuid, correlation_id=ctx.correlation_id)
            return JSONResponse(OVERLOADED, status_code=503, headers={"Retry-After": str(SHED_RETRY_AFTER_S)})
        conv = store.open(conv_id, ctx.user_uuid, patient)
        loop = AgentLoop(runner, llm_factory())

        async def stream() -> AsyncIterator[str]:
            trace, tok = tracing.start(ctx.correlation_id, model, flow="chat")
            gauge.inflight += 1
            try:
                async for chunk in _run(trace):
                    yield chunk
            finally:  # runs on completion, error and client disconnect, so every request leaves a trace
                gauge.inflight -= 1
                trace.outcome = terminal_box[0]
                if traces is not None:
                    traces.save(trace)
                try:
                    tracing.end(tok)
                except ValueError:
                    pass

        terminal_box = ["error"]

        async def _run(trace: tracing.Trace) -> AsyncIterator[str]:
            yield sse("conversation", {"id": conv.id})
            terminal = "error"
            async for ev in loop.run(conv, Caller(ctx.user_uuid, ctx.token), question):
                kind = ev["type"]
                if kind == "step":
                    yield sse("step", {"tool": ev["tool"]})
                elif kind == "done":
                    with tracing.span("verifier", "verify"):
                        answer = finalizer(ev["draft"], ev["envelopes"])
                    trace.claims_kept = sum(1 for c in answer.claims if not c.rule_based)
                    trace.claims_dropped = answer.dropped_count
                    answer = restore_patient_reference(answer, ev["envelopes"])  # after verification, at the output edge
                    answer = attach_sources(answer, ev["envelopes"])  # cards only for ids a kept claim cites
                    payload = json.loads(answer.model_dump_json())
                    payload["notes"] = list(dict.fromkeys(
                        [*payload["notes"], *ev["notes"], *coverage_notes(ev["envelopes"], ev.get("failed", frozenset()))]))
                    yield sse("answer", payload)
                    terminal = "answer"
                elif kind == "degraded":
                    yield sse("degraded", {"message": "The language model is unavailable. Showing chart data only.",
                                           "records": _raw(ev["envelopes"]),
                                           "sources": [json.loads(c.model_dump_json()) for c in all_cards(ev["envelopes"])]})
                    terminal = "degraded"
                elif kind == "declined":
                    yield sse("declined", declined_payload(ev, finalizer))
                    terminal = "declined"
                elif kind == "denied":
                    yield sse("denied", GENERIC_DENIAL)
                    terminal = "denied"
            if terminal == "error":  # "failed" (or no terminal event at all) ends here, exactly one error event
                yield sse("error", GENERIC_ERROR)
            terminal_box[0] = terminal
            audit.record("chat_finished", outcome=terminal, user_uuid=ctx.user_uuid, patient_uuid=patient,
                         correlation_id=ctx.correlation_id)
            yield sse("end", {})

        return StreamingResponse(stream(), media_type="text/event-stream",
                                 headers={"cache-control": "no-store", "x-accel-buffering": "no"})

    return router
