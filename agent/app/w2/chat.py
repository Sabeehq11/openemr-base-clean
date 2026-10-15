"""POST /v1/w2/chat: the Week 2 flow. Same signed request and SSE shape as /v1/chat, with a supervisor in front.

Stage A (chart facts) is the unchanged Week 1 AgentLoop plus Verifier; its verified answer is carried into the final
answer as is. Stage B (document facts and guideline evidence) can only add claims beside it. Events: `conversation`,
then one of `answer | declined | degraded | denied | error`, then `end`. Authorization runs before the supervisor
reads anything stored for the patient, so a denied caller triggers no w2.db read.
"""
from __future__ import annotations

import json
import logging
from typing import AsyncIterator, Callable

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, StreamingResponse

from app import tracing
from app.chat import (GENERIC_DENIAL, GENERIC_ERROR, MAX_QUESTION_CHARS, OVERLOADED, SHED_RETRY_AFTER_S, Finalizer,
                      LoadGauge, declined_payload, sse)
from app.conversation import ConversationStore
from app.gateway import AuditSink, Gateway
from app.llm import LLMClient
from app.logging_setup import log_event
from app.loop import AgentLoop, Caller
from app.sources import all_cards
from app.tools import DeniedError, ToolRunner
from app.tracing import TraceStore
from contracts import ToolError, ToolName
from contracts.w2 import Node

from .assemble import build_answer
from .facts import RunContext
from .rag.retriever import Retriever
from .stage_a import ChartRun, chart_node, raw_records
from .store import W2Store
from .supervisor import Supervisor
from .workers import EvidenceRetrieverWorker, IntakeExtractorWorker, wants_evidence

logger = logging.getLogger("agent.w2.chat")
WORKER_TIMEOUT_S = 30.0  # outer bound per node; the Week 1 loop keeps its own 20 s deadline inside it


def build_w2_chat_router(gateway: Gateway, runner: ToolRunner, llm_factory: Callable[[], LLMClient],
                         finalizer: Finalizer, store: ConversationStore, w2store: W2Store, retriever: Retriever,
                         audit: AuditSink, traces: TraceStore | None = None, model: str = "", max_inflight: int = 0,
                         gauge: LoadGauge | None = None, prefix: str = "/v1/w2") -> APIRouter:
    router = APIRouter(prefix=prefix)  # wiring passes "" because the parent W2 router already carries /v1/w2
    gauge = gauge or LoadGauge(max_inflight)  # shared with /v1/chat when wired, so the cap covers both

    @router.post("/chat")
    async def chat(request: Request):
        http = await gateway.verify(request)
        try:
            body = json.loads(http.body)
            patient, question, conv_id = body["patient_uuid"], body["question"], body.get("conversation_id")
            if not (isinstance(patient, str) and isinstance(question, str) and 0 < len(question) <= MAX_QUESTION_CHARS
                    and (conv_id is None or isinstance(conv_id, str))):
                raise TypeError
        except (ValueError, KeyError, TypeError):
            return JSONResponse({"error": "invalid_request", "message": "Malformed request."}, status_code=400)
        if max_inflight and gauge.inflight >= max_inflight:  # after verification, so only signed callers take a slot
            gauge.shed += 1
            audit.record("chat_shed", user_uuid=http.user_uuid, correlation_id=http.correlation_id)
            return JSONResponse(OVERLOADED, status_code=503, headers={"Retry-After": str(SHED_RETRY_AFTER_S)})
        conv = store.open(conv_id, http.user_uuid, patient)
        caller = Caller(http.user_uuid, http.token)
        box = ["error"]

        async def stream() -> AsyncIterator[str]:
            trace, tok = tracing.start(http.correlation_id, model)
            gauge.inflight += 1
            try:
                async for chunk in _run():
                    yield chunk
            finally:  # every request leaves a trace, including client disconnects
                gauge.inflight -= 1
                trace.outcome = box[0]
                if traces is not None:
                    traces.save(trace)
                try:
                    tracing.end(tok)
                except ValueError:
                    pass

        async def _run() -> AsyncIterator[str]:
            yield sse("conversation", {"id": conv.id})
            try:
                await runner.check(ToolName.GET_PATIENT_SUMMARY, caller.token, patient, caller.user_uuid)
            except (DeniedError, ToolError):  # an unverifiable decision is a denial
                box[0] = "denied"
                yield sse("denied", GENERIC_DENIAL)
                audit.record("w2_chat_finished", outcome="denied", user_uuid=http.user_uuid, patient_uuid=patient,
                             correlation_id=http.correlation_id)
                yield sse("end", {})
                return
            ctx, chart = RunContext(patient, question), ChartRun()
            llm = llm_factory()
            nodes = {
                Node.INTAKE_EXTRACTOR: IntakeExtractorWorker(w2store, ctx).run,
                Node.EVIDENCE_RETRIEVER: EvidenceRetrieverWorker(retriever, ctx).run,
                Node.CHART_FACTS: chart_node(AgentLoop(runner, llm), conv, caller, question, finalizer, chart),
            }
            out = await Supervisor(nodes, lambda s: wants_evidence(ctx, s), worker_timeout_s=WORKER_TIMEOUT_S).run()
            terminal = "error"
            if chart.terminal == "declined" and chart.event is not None:
                yield sse("declined", declined_payload(chart.event, finalizer))
                terminal = "declined"
            elif chart.terminal == "denied":
                yield sse("denied", GENERIC_DENIAL)
                terminal = "denied"
            elif chart.terminal == "degraded":  # model down: chart data only, no second model call
                records = raw_records(chart)
                yield sse("degraded", {"message": "The language model is unavailable. Showing chart data only.",
                                       "records": records,
                                       "sources": [json.loads(c.model_dump_json())
                                                   for c in all_cards(chart.event["envelopes"])]})
                terminal = "degraded"
            else:  # done, a failed chart step, or a cap: an answer whose notes name whatever is missing
                try:
                    answer = await build_answer(out, ctx, chart, llm)
                except Exception as e:  # the type is logged, never the message or a traceback
                    log_event(logger, "w2_answer_failed", error_code=type(e).__name__)
                    yield sse("error", GENERIC_ERROR)
                else:
                    if (tr := tracing.current()) is not None:
                        tr.claims_kept, tr.claims_dropped = chart.claims_kept, chart.claims_dropped
                    yield sse("answer", json.loads(answer.model_dump_json()))
                    terminal = "answer"
            box[0] = terminal
            audit.record("w2_chat_finished", outcome=terminal, user_uuid=http.user_uuid, patient_uuid=patient,
                         correlation_id=http.correlation_id, handoffs=len(out.handoffs), facts=len(ctx.facts),
                         snippets=len(ctx.snippets))
            yield sse("end", {})

        return StreamingResponse(stream(), media_type="text/event-stream",
                                 headers={"cache-control": "no-store", "x-accel-buffering": "no"})

    return router
