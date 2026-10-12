"""POST /v1/chat: signed request in, Server-Sent Events out.

Events: `step` (a tool ran), then exactly one of `answer` | `degraded` | `denied` | `error`, then `end`.
The model's draft goes through a `Finalizer` (the verifier) before anything is emitted as `answer`.
"""
from __future__ import annotations

import json
from typing import AsyncIterator, Callable, Protocol

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, StreamingResponse

from contracts import AnswerDraft, ToolEnvelope, VerifiedAnswer

from .conversation import ConversationStore
from .gateway import AuditSink, Gateway
from .llm import LLMClient
from .loop import AgentLoop, Caller
from .tools import ToolRunner

GENERIC_DENIAL = {"message": "You do not have access to this patient."}
GENERIC_ERROR = {"message": "The assistant could not produce a verified answer. Please try again."}
MAX_QUESTION_CHARS = 1000


class Finalizer(Protocol):
    def __call__(self, draft: AnswerDraft, envelopes: list[ToolEnvelope]) -> VerifiedAnswer: ...


def fail_closed_finalizer(draft: AnswerDraft, envelopes: list[ToolEnvelope]) -> VerifiedAnswer:
    """Releases nothing. Used when no verifier is wired in, so an unverified claim can never be shown."""
    return VerifiedAnswer(claims=[], dropped_count=len(draft.claims), notes=["No verifier configured."])


def sse(event: str, data: object) -> str:
    return f"event: {event}\ndata: {json.dumps(data, separators=(',', ':'))}\n\n"


def _raw(envelopes: list[ToolEnvelope]) -> list[dict]:
    return [json.loads(e.model_dump_json()) for e in envelopes]


def build_chat_router(gateway: Gateway, runner: ToolRunner, llm_factory: Callable[[], LLMClient],
                      finalizer: Finalizer, store: ConversationStore, audit: AuditSink) -> APIRouter:
    router = APIRouter(prefix="/v1")

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
        conv = store.open(conv_id, ctx.user_uuid, patient)
        loop = AgentLoop(runner, llm_factory())

        async def stream() -> AsyncIterator[str]:
            yield sse("conversation", {"id": conv.id})
            terminal = "error"
            async for ev in loop.run(conv, Caller(ctx.user_uuid, ctx.token), question):
                kind = ev["type"]
                if kind == "step":
                    yield sse("step", {"tool": ev["tool"]})
                elif kind == "done":
                    answer = finalizer(ev["draft"], ev["envelopes"])
                    payload = json.loads(answer.model_dump_json())
                    payload["notes"] = [*payload["notes"], *ev["notes"]]
                    yield sse("answer", payload)
                    terminal = "answer"
                elif kind == "degraded":
                    yield sse("degraded", {"message": "The language model is unavailable. Showing chart data only.",
                                           "records": _raw(ev["envelopes"])})
                    terminal = "degraded"
                elif kind == "denied":
                    yield sse("denied", GENERIC_DENIAL)
                    terminal = "denied"
            if terminal == "error":  # "failed" (or no terminal event at all) ends here, exactly one error event
                yield sse("error", GENERIC_ERROR)
            audit.record("chat_finished", outcome=terminal, user_uuid=ctx.user_uuid, patient_uuid=patient,
                         correlation_id=ctx.correlation_id)
            yield sse("end", {})

        return StreamingResponse(stream(), media_type="text/event-stream",
                                 headers={"cache-control": "no-store", "x-accel-buffering": "no"})

    return router
