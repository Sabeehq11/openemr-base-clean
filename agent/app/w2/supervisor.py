"""Supervisor: a small state machine, not a model. It routes from typed state, so every decision is inspectable.

Routing, evaluated on every step (first match wins):
  1. stored document facts not read yet        -> intake_extractor
  2. chart facts not fetched yet               -> chart_facts (the unchanged Week 1 AgentLoop)
  3. evidence wanted and not retrieved yet     -> evidence_retriever
  4. otherwise                                 -> synthesis (ready)
Chart runs before evidence, so a denied or declined request stops before any retrieval or second model call.
Caps: max_steps node runs and a total deadline. A worker that fails or times out is recorded once and never retried;
the run goes on and the answer names the missing source. Every handoff is one structured log event (ids and enums
only) and one trace span, so the route is rebuilt from the correlation ID alone.
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Awaitable, Callable

from app import tracing
from app.logging_setup import correlation_id_var, log_event
from contracts.w2 import GraphState, Handoff, HandoffReason, Node, WorkerResult, WorkerStatus

logger = logging.getLogger("agent.w2.supervisor")

MAX_STEPS = 6
DEADLINE_S = 40.0
WORKER_TIMEOUT_S = 15.0
# A node reporting one of these ends the run at once: a refusal, or no model for the second call either.
HALT_CODES = frozenset({"denied", "declined", "llm_unavailable"})

NodeFn = Callable[[], Awaitable[WorkerResult]]


@dataclass(frozen=True)
class Decision:
    node: Node
    reason: HandoffReason


def next_node(state: GraphState) -> Decision:
    """Pure routing rule. No clock and no I/O, so it is tested as a table."""
    if not state.docs_done:
        return Decision(Node.INTAKE_EXTRACTOR, HandoffReason.DOCUMENT_FACTS_NEEDED)
    if not state.chart_done:
        return Decision(Node.CHART_FACTS, HandoffReason.CHART_FACTS_NEEDED)
    if state.wants_evidence and not state.evidence_done:
        return Decision(Node.EVIDENCE_RETRIEVER, HandoffReason.EVIDENCE_NEEDED)
    return Decision(Node.SYNTHESIS, HandoffReason.READY)


@dataclass
class Outcome:
    end: str  # ready | step_cap | deadline | halted
    state: GraphState
    handoffs: list[Handoff] = field(default_factory=list)

    @property
    def missing(self) -> list[Node]:
        """Nodes that failed or timed out, so the answer can name the missing source."""
        return [r.worker for r in self.state.results if r.status in (WorkerStatus.FAILED, WorkerStatus.TIMEOUT)]


class Supervisor:
    def __init__(self, nodes: dict[Node, NodeFn], wants_evidence: Callable[[GraphState], bool],
                 clock: Callable[[], float] = time.monotonic, max_steps: int = MAX_STEPS,
                 deadline_s: float = DEADLINE_S, worker_timeout_s: float = WORKER_TIMEOUT_S) -> None:
        self._nodes, self._wants, self._clock = nodes, wants_evidence, clock
        self._max_steps, self._deadline, self._worker_timeout = max_steps, deadline_s, worker_timeout_s

    def _handoff(self, out: Outcome, src: Node, dst: Node, reason: HandoffReason) -> None:
        h = Handoff(from_node=src, to_node=dst, reason=reason, correlation_id=correlation_id_var.get() or "-",
                    step=out.state.step)
        out.handoffs.append(h)
        log_event(logger, "w2_handoff", from_node=src.value, to_node=dst.value, reason=reason.value, step=h.step,
                  span_id=tracing.current_span_id())

    async def run(self) -> Outcome:
        """The whole graph run is one `w2/supervisor` span; each worker span below is its child."""
        with tracing.span("w2", "supervisor") as attrs:
            out = await self._run()
            attrs.update(steps=out.state.step, handoffs=len(out.handoffs))
            for h in out.handoffs:  # routing decisions: how often the supervisor chose each reason
                if h.from_node is Node.SUPERVISOR:
                    key = f"route_{h.reason.value}"
                    attrs[key] = int(attrs.get(key, 0)) + 1
            return out

    async def _run(self) -> Outcome:
        start = self._clock()
        out = Outcome(end="ready", state=GraphState())
        while True:
            remaining = self._deadline - (self._clock() - start)
            if out.state.step >= self._max_steps or remaining <= 0:
                capped = out.state.step >= self._max_steps
                out.end = "step_cap" if capped else "deadline"
                self._handoff(out, Node.SUPERVISOR, Node.SYNTHESIS,
                              HandoffReason.STEP_CAP if capped else HandoffReason.DEADLINE)
                return out
            d = next_node(out.state)
            self._handoff(out, Node.SUPERVISOR, d.node, d.reason)
            if d.node is Node.SYNTHESIS:
                return out
            result = await self._run_node(d.node, min(self._worker_timeout, remaining))
            self._handoff(out, d.node, Node.SUPERVISOR, HandoffReason.WORKER_RESULT)
            log_event(logger, "w2_worker_result", worker=d.node.value, status=result.status.value,
                      refs=len(result.refs), latency_ms=result.latency_ms, error_code=result.error_code)
            out.state = self._advance(out.state, d.node, result)
            if result.error_code in HALT_CODES:
                out.end = "halted"
                return out

    async def _run_node(self, node: Node, timeout_s: float) -> WorkerResult:
        t0 = time.perf_counter()
        try:
            with tracing.span("w2", node.value):  # a child of the supervisor span
                return await asyncio.wait_for(self._nodes[node](), timeout=timeout_s)
        except asyncio.TimeoutError:
            status, code = WorkerStatus.TIMEOUT, "timeout"
        except Exception as e:  # a worker must not take the request down; the type is logged, never the message
            log_event(logger, "w2_worker_raised", worker=node.value, error_code=type(e).__name__)
            status, code = WorkerStatus.FAILED, "worker_error"
        return WorkerResult(worker=node, status=status, error_code=code,
                            latency_ms=round((time.perf_counter() - t0) * 1000, 1))

    def _advance(self, s: GraphState, node: Node, result: WorkerResult) -> GraphState:
        upd: dict = {"step": s.step + 1, "results": (*s.results, result)}
        if node is Node.INTAKE_EXTRACTOR:
            upd["docs_done"] = True
        elif node is Node.CHART_FACTS:
            upd["chart_done"] = True
        elif node is Node.EVIDENCE_RETRIEVER:
            upd["evidence_done"] = True
        nxt = s.model_copy(update=upd)
        return nxt.model_copy(update={"wants_evidence": self._wants(nxt)})
