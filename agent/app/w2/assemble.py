"""Final answer assembly. The chart group is Stage A's frozen answer, passed through without a copy or an edit."""
from __future__ import annotations

from app.llm import LLMClient
from contracts import VerifiedAnswer
from contracts.w2 import Node, W2VerifiedAnswer

from .facts import RunContext
from .stage_a import ChartRun
from .supervisor import Outcome
from .synthesis import synthesise

NOTES = {
    "no_docs": "No uploaded documents are on file for this patient.",
    "kind_not_stored": "No {kind} report is on file for this patient. Only lab reports and intake forms can be uploaded.",
    "docs_failed": "An uploaded document could not be read, so its values are not shown.",
    "no_evidence": "No guideline evidence was found for this question.",
    "chart_missing": "Chart data could not be retrieved, so no chart statements are shown.",
    "unsynthesised": "The language model was unavailable, so document facts and guideline text are shown as recorded.",
    "cap": "A step or time limit was reached; some sources may not have been retrieved.",
    "unsupported": "Some document values could not be tied to a location in the source and are not used.",
    "rerank": "Guideline ranking used the fallback order because the reranker was unavailable.",
    "worker": "A source could not be retrieved in time: {name}.",
}


def build_notes(out: Outcome, ctx: RunContext, chart: ChartRun, unsynth: bool) -> list[str]:
    notes: list[str] = []
    docs = out.state.result_for(Node.INTAKE_EXTRACTOR)
    if ctx.missing_kind:
        notes.append(NOTES["kind_not_stored"].format(kind=ctx.missing_kind))
    elif docs is not None and docs.error_code == "no_documents":
        notes.append(NOTES["no_docs"])
    if ctx.failed_docs:
        notes.append(NOTES["docs_failed"])
    if ctx.unsupported:
        notes.append(NOTES["unsupported"])
    ev = out.state.result_for(Node.EVIDENCE_RETRIEVER)
    if ev is not None and ev.error_code == "no_evidence":
        notes.append(NOTES["no_evidence"])
    if ctx.rerank_skipped:
        notes.append(NOTES["rerank"])
    if chart.answer is None:
        notes.append(NOTES["chart_missing"])
    if out.end in ("step_cap", "deadline"):
        notes.append(NOTES["cap"])
    notes.extend(NOTES["worker"].format(name=n.value.replace("_", " ")) for n in out.missing
                 if n in (Node.EVIDENCE_RETRIEVER, Node.INTAKE_EXTRACTOR))
    if unsynth:
        notes.append(NOTES["unsynthesised"])
    return list(dict.fromkeys(notes))


async def build_answer(out: Outcome, ctx: RunContext, chart: ChartRun, llm: LLMClient) -> W2VerifiedAnswer:
    """Stage B plus assembly. Stage B sees only document facts and guideline snippets, never the chart answer."""
    docs, guides, rejected, unsynth = await synthesise(llm, ctx.facts, ctx.snippets)
    return W2VerifiedAnswer(
        chart=chart.answer if chart.answer is not None else VerifiedAnswer(claims=[]),
        document_claims=docs, guideline_claims=guides, dropped_count=sum(rejected.values()), rejections=rejected,
        notes=build_notes(out, ctx, chart, unsynth), unsynthesised=unsynth)
