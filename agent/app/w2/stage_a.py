"""Stage A adapter: the unchanged Week 1 AgentLoop plus Verifier, run as the supervisor's chart-facts capability.

The result is a frozen `VerifiedAnswer` built with the same steps `/v1/chat` uses (verify, restore the patient
reference, attach source cards, merge notes). After that it is never touched: Week 2 only adds groups beside it.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any

from app import tracing
from app.chat import Finalizer
from app.conversation import Conversation
from app.coverage import coverage_notes
from app.loop import AgentLoop, Caller
from app.prompts import restore_patient_reference
from app.sources import attach_sources
from contracts import VerifiedAnswer
from contracts.w2 import Node, WorkerResult, WorkerStatus

_CODES = {"denied": "denied", "declined": "declined", "degraded": "llm_unavailable", "failed": "no_valid_answer"}


@dataclass
class ChartRun:
    """What the Week 1 loop produced for this request. `answer` is set once and never edited."""

    steps: list[str] = field(default_factory=list)
    terminal: str = "none"  # done | degraded | denied | declined | failed | none
    answer: VerifiedAnswer | None = None
    event: dict[str, Any] | None = None  # the raw terminal event for declined and degraded
    claims_kept: int = 0
    claims_dropped: int = 0


def chart_node(loop: AgentLoop, conv: Conversation, caller: Caller, question: str, finalizer: Finalizer, run: ChartRun):
    async def fn() -> WorkerResult:
        t0 = time.perf_counter()
        async for ev in loop.run(conv, caller, question):
            kind = ev["type"]
            if kind == "step":
                run.steps.append(ev["tool"])
            elif kind == "done":
                with tracing.span("verifier", "verify"):
                    answer = finalizer(ev["draft"], ev["envelopes"])
                run.claims_kept = sum(1 for c in answer.claims if not c.rule_based)
                run.claims_dropped = answer.dropped_count
                answer = restore_patient_reference(answer, ev["envelopes"])
                answer = attach_sources(answer, ev["envelopes"])
                notes = list(dict.fromkeys([*answer.notes, *ev["notes"],
                                            *coverage_notes(ev["envelopes"], ev.get("failed", frozenset()))]))
                run.answer = answer.model_copy(update={"notes": notes})
                run.terminal = "done"
            else:  # degraded | denied | declined | failed
                run.terminal, run.event = kind, ev
        ok = run.terminal == "done"
        return WorkerResult(worker=Node.CHART_FACTS, status=WorkerStatus.OK if ok else WorkerStatus.FAILED,
                            error_code=None if ok else _CODES.get(run.terminal, "no_result"),
                            latency_ms=round((time.perf_counter() - t0) * 1000, 1))
    return fn


def raw_records(run: ChartRun) -> list[dict]:
    return [json.loads(e.model_dump_json()) for e in (run.event or {}).get("envelopes", [])]
