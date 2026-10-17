"""The two workers. Each returns a `WorkerResult` carrying ids and counts only; facts and snippets go into `RunContext`.

intake-extractor: reads facts already stored in w2.db (extraction happens at upload, C1). Read-only, one patient.
evidence-retriever: runs the hybrid retriever on the question plus test names only, never an identifier.
"""
from __future__ import annotations

import asyncio
import re
import time

from contracts.w2 import DocStatus, GraphState, Node, RetrievalStatus, WorkerResult, WorkerStatus

from .facts import RunContext, facts_from_record
from .rag.retriever import Retriever
from .store import W2Store

MAX_DOCUMENTS = 5

# Questions that ask for guidance, evidence or a target, as opposed to "what is in the chart".
_EVIDENCE_WORDS = re.compile(
    r"\b(guideline\w*|evidence|recommend\w*|how often|frequen\w*|screen\w*|goal|target|monitor\w*|follow[- ]?up|"
    r"what changed|pay attention|should|supports?)\b", re.I)


# Only lab reports and intake forms are ever stored (DocType). A question that names another kind of report is asking
# for something that cannot be on file, so the answer says so instead of offering the documents that do exist.
_OTHER_KINDS = re.compile(
    r"\b(genetic|genomic|genome|dna|pathology|biopsy|radiology|imaging|x-?ray|mri|ct scan|ultrasound|ecg|ekg|"
    r"discharge summary|operative|consult(?:ation)? (?:note|report))\b", re.I)
_STORED_KINDS = re.compile(r"\b(labs?|laboratory|lab results?|intake|forms?|a1c|hemoglobin|creatinine|glucose|"
                           r"cholesterol|potassium|medications?|allerg\w+)\b", re.I)


def missing_document_kind(question: str) -> str | None:
    """The unsupported kind of report the question asks about, as the user wrote it, or None."""
    m = _OTHER_KINDS.search(question)
    return m.group(1).lower() if m else None


def wants_evidence(ctx: RunContext, state: GraphState) -> bool:
    """Routing input: evidence is wanted when the question asks for guidance, or extracted lab values need support."""
    return bool(_EVIDENCE_WORDS.search(ctx.question)) or bool(ctx.test_names())


class IntakeExtractorWorker:
    node = Node.INTAKE_EXTRACTOR

    def __init__(self, store: W2Store, ctx: RunContext) -> None:
        self._store, self._ctx = store, ctx

    async def run(self) -> WorkerResult:
        t0 = time.perf_counter()
        ctx = self._ctx
        ctx.missing_kind = missing_document_kind(ctx.question)
        if ctx.missing_kind and not _STORED_KINDS.search(ctx.question):
            # The question is only about a kind of report that is never stored: read nothing, so no unrelated lab or
            # intake fact can stand in for it.
            return WorkerResult(worker=self.node, status=WorkerStatus.EMPTY, error_code="document_kind_not_stored",
                                latency_ms=round((time.perf_counter() - t0) * 1000, 1))
        records = await asyncio.to_thread(self._read)
        refs = [f.fact_id for f in self._ctx.facts]
        if not records:
            status, code = WorkerStatus.EMPTY, "no_documents"
        elif self._ctx.failed_docs and not refs:
            status, code = WorkerStatus.FAILED, "extraction_failed"  # the document exists but holds no usable facts
        else:
            status, code = (WorkerStatus.OK if refs else WorkerStatus.EMPTY), None
        return WorkerResult(worker=self.node, status=status, refs=refs[:200], error_code=code,
                            latency_ms=round((time.perf_counter() - t0) * 1000, 1))

    def _read(self) -> list:
        ctx = self._ctx
        records = []
        for doc_id in self._store.list_document_ids(ctx.patient_uuid, MAX_DOCUMENTS):
            rec = self._store.get(ctx.patient_uuid, doc_id)  # scoped by patient: another patient's id finds nothing
            if rec is None:
                continue
            records.append(rec)
            if rec.status is not DocStatus.EXTRACTED:
                ctx.failed_docs += 1
                continue
            facts, unsupported = facts_from_record(rec)
            ctx.facts.extend(facts)
            ctx.unsupported += unsupported
        return records


class EvidenceRetrieverWorker:
    node = Node.EVIDENCE_RETRIEVER

    def __init__(self, retriever: Retriever, ctx: RunContext) -> None:
        self._retriever, self._ctx = retriever, ctx

    async def run(self) -> WorkerResult:
        t0 = time.perf_counter()
        ctx = self._ctx
        query = " ".join([ctx.question, *ctx.test_names()])
        result = await asyncio.to_thread(self._retriever.retrieve, query)
        ctx.snippets, ctx.rerank_skipped = list(result.snippets), result.rerank_skipped
        status = WorkerStatus.OK if result.status is RetrievalStatus.OK else WorkerStatus.EMPTY
        return WorkerResult(worker=self.node, status=status, refs=[s.chunk_id for s in result.snippets],
                            error_code=None if status is WorkerStatus.OK else "no_evidence",
                            latency_ms=round((time.perf_counter() - t0) * 1000, 1))
