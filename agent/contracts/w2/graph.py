"""Supervisor-worker contracts. Handoffs carry ids and enums only, never question text, values or document text."""
from __future__ import annotations

from enum import Enum
from typing import Annotated

from pydantic import Field

from .common import W2Model


class Node(str, Enum):
    SUPERVISOR = "supervisor"
    INTAKE_EXTRACTOR = "intake_extractor"
    EVIDENCE_RETRIEVER = "evidence_retriever"
    CHART_FACTS = "chart_facts"  # the Week 1 AgentLoop, called as a capability
    SYNTHESIS = "synthesis"


class HandoffReason(str, Enum):
    DOCUMENT_FACTS_NEEDED = "document_facts_needed"
    EVIDENCE_NEEDED = "evidence_needed"
    CHART_FACTS_NEEDED = "chart_facts_needed"
    READY = "ready"
    WORKER_RESULT = "worker_result"
    STEP_CAP = "step_cap"
    DEADLINE = "deadline"


class WorkerStatus(str, Enum):
    OK = "ok"
    EMPTY = "empty"
    FAILED = "failed"
    TIMEOUT = "timeout"


class Handoff(W2Model):
    """One routing decision. `inputs_ref` holds ids (document ids, chunk ids), never raw text."""

    from_node: Node
    to_node: Node
    reason: HandoffReason
    inputs_ref: Annotated[list[str], Field(max_length=50)] = []
    correlation_id: Annotated[str, Field(min_length=1, max_length=64)]
    step: Annotated[int, Field(ge=0, le=50)]


class WorkerResult(W2Model):
    worker: Node
    status: WorkerStatus
    refs: Annotated[list[str], Field(max_length=200)] = []  # ids of produced facts or chunks
    latency_ms: Annotated[float, Field(ge=0)] = 0.0
    error_code: Annotated[str, Field(max_length=64)] | None = None


class GraphState(W2Model):
    """Everything the routing rule may look at. Replaced (never mutated) on each step."""

    step: Annotated[int, Field(ge=0)] = 0
    docs_done: bool = False
    chart_done: bool = False
    evidence_done: bool = False
    wants_evidence: bool | None = None  # unknown until the document read has happened
    results: tuple[WorkerResult, ...] = ()

    def result_for(self, node: Node) -> WorkerResult | None:
        return next((r for r in self.results if r.worker is node), None)
