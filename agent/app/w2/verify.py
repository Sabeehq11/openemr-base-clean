"""W2 verifier for Stage B. Runs after the second model call and before anything is shown.

A claim is kept only when every id it cites was offered to the model in that request, belongs to the claim's own group
(patient document or guideline), and every number and date in the claim appears in the cited quote or text. Chart ids
are never accepted here: chart claims come from Stage A and are not Stage B's to write. Advice-shaped wording is dropped
by the Week 1 output rules. Only fixed reason codes and counts leave this module, never claim text.
"""
from __future__ import annotations

import logging

from app.logging_setup import log_event
from app.rules import violates_output_rules
from app.verifier import _dates, _norm, _numbers  # reused, not edited
from contracts.w2 import Citation, RetrievedSnippet, W2Claim, W2Draft

from .facts import DocFact

logger = logging.getLogger("agent.w2.verify")

R_UNCITED, R_UNKNOWN_SOURCE, R_MIXED_GROUPS = "uncited", "unknown_source", "mixed_groups"
R_NUMBER, R_DATE, R_OUTPUT_RULES = "number_mismatch", "date_mismatch", "output_rules"


def verify_stage_b(draft: W2Draft, facts: list[DocFact], snippets: list[RetrievedSnippet],
                   ) -> tuple[list[W2Claim], list[W2Claim], dict[str, int]]:
    """Returns (document claims, guideline claims, rejection counts)."""
    by_fact = {f.fact_id: f for f in facts}
    by_chunk = {s.chunk_id: s for s in snippets}
    docs: list[W2Claim] = []
    guides: list[W2Claim] = []
    rejected: dict[str, int] = {}

    def reject(code: str) -> None:
        rejected[code] = rejected.get(code, 0) + 1

    for claim in draft.claims:
        ids = list(dict.fromkeys(claim.source_ids))
        if not ids:
            reject(R_UNCITED)
            continue
        if any(i not in by_fact and i not in by_chunk for i in ids):  # includes every chart id and every made-up id
            reject(R_UNKNOWN_SOURCE)
            continue
        is_doc = [i in by_fact for i in ids]
        if any(is_doc) and not all(is_doc):
            reject(R_MIXED_GROUPS)  # one claim, one kind of source: a record fact is never blended with a guideline
            continue
        cites: list[Citation] = [by_fact[i].citation for i in ids] if all(is_doc) else [by_chunk[i].citation for i in ids]
        evidence = _norm(" ".join(c.quote_or_value for c in cites) + " " +
                         " ".join(by_fact[i].text for i in ids if i in by_fact) + " " +
                         " ".join(by_chunk[i].text for i in ids if i in by_chunk))
        if not _numbers(claim.text) <= _numbers(evidence):
            reject(R_NUMBER)
            continue
        if not _dates(claim.text) <= _dates(evidence):
            reject(R_DATE)
            continue
        # Recorded text is exempt from the advice rules: both the quote and the stored value itself ("Metformin 500 mg
        # twice daily" is the value; the quote reads "Medication: Metformin 500 mg twice daily"). A claim that only
        # restates what a document says is not a recommendation. Anything the model adds around it is still checked.
        recorded = tuple(c.quote_or_value for c in cites) + tuple(by_fact[i].text for i in ids if i in by_fact)
        if violates_output_rules(claim.text, recorded):
            reject(R_OUTPUT_RULES)
            continue
        kept = W2Claim(text=claim.text, citations=cites)
        (docs if all(is_doc) else guides).append(kept)
    if rejected:
        log_event(logger, "w2_claims_rejected", rejections=rejected)
    return docs, guides, rejected
