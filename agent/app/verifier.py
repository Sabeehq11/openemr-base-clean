"""Claim verifier. Runs after generation and before anything is shown; there is no path around it.

For every model claim:
  fact     must cite >=1 source id returned by a tool in this request, and every number/date it states must appear
           in the cited records' fields (exact match after normalisation).
  absence  must be worded "not documented" (never "no X"/"none") and must match an empty result from the right tool.
  flag     the model may not originate flags; only the rules layer can. Model flags are dropped.
Then output rules reject diagnosis/treatment direction, and rule findings are appended as rule-based flag claims.
"""
from __future__ import annotations

import logging
import re
from collections import Counter
from datetime import date

from contracts import (AnswerDraft, Claim, ClaimKind, ToolEnvelope, ToolName, VerifiedAnswer, VerifiedClaim)

from .rules import RuleFinding, run_rules, violates_output_rules

logger = logging.getLogger("agent.verifier")

# Fixed rejection reason codes. Only these strings and their counts ever leave the verifier.
R_MODEL_FLAG, R_ABSENCE, R_UNCITED, R_UNKNOWN_SOURCE = "model_flag", "absence_unsupported", "uncited", "unknown_source"
R_NUMBER, R_DATE, R_OUTPUT_RULES, R_UNSUPPORTED_ASSERTION = "number_mismatch", "date_mismatch", "output_rules", "unsupported_assertion"

ADVICE_WITHHELD_NOTE = ("Some statements were withheld because they read as a diagnosis or a treatment "
                        "recommendation. This assistant only reports what is recorded in the chart.")
# Wording that asserts a clinical relationship. No record field states one, so a model claim that uses it is kept only when a
# cited record contains the same word; interaction and risk statements otherwise come from the deterministic rules layer.
_CLINICAL_ASSERTION = re.compile(
    r"\b(interact\w*|contraindicat\w*|potentiat\w*|adverse|toxic\w*|bleed\w*|risk of|increased risk|higher risk|"
    r"drug-drug|ototox\w*|nephrotox\w*|"
    # Memory-based pharmacology: what a drug "requires", how risky it is, what should be monitored. Records state none of it.
    r"monitor\w*|surveillance|high[- ]risk|low[- ]risk|narrow therapeutic|therapeutic (?:index|window|range)|"
    r"requires?|required|needs? (?:to be|regular|routine|close)|should be (?:checked|tested|monitored)|"
    r"dangerous|unsafe|side effects?|warning)", re.I)
UNSUPPORTED_ASSERTION_NOTE = ("Statements about interactions or risks were withheld because no record states them. "
                              "Concerns shown are only those the records or the rule checks support.")
_NUM = re.compile(r"(?<![\w.])\d+(?:\.\d+)?(?![\w])")
_DATE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")
_ABSENCE_OK = re.compile(r"\b(documented|recorded|found in the record)\b", re.I)
# Absence must be worded as "not documented". Assertions about the patient ("no known", "has no", "none") are refused.
_ABSENCE_BAD = re.compile(r"\b(no known|has no|have no|denies|nka|nkda)\b|\bnone\b(?! documented)", re.I)
# Words in an absence claim that point at a tool.
_ABSENCE_TOOL = {
    "allerg": ToolName.GET_ALLERGIES, "medication": ToolName.GET_MEDICATIONS, "problem": ToolName.GET_PROBLEMS,
    "lab": ToolName.GET_LABS, "change": ToolName.GET_CHANGES_SINCE,
}


def _fields(rec: object) -> str:
    """All the record's scalar values as one string; numbers/dates in a claim must be found here."""
    data = rec.model_dump()  # type: ignore[attr-defined]
    return " ".join(str(v) for k, v in data.items() if v is not None and k not in ("source_id", "patient_uuid"))


def _numbers(s: str) -> set[str]:
    stripped = _DATE.sub(" ", s)
    return {n.rstrip("0").rstrip(".") if "." in n else n for n in _NUM.findall(stripped)}


def _dates(s: str) -> set[str]:
    return set(_DATE.findall(s))


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s.lower()).strip()


class Verifier:
    def __init__(self, today=lambda: date.today()) -> None:
        self._today = today

    def verify(self, draft: AnswerDraft, envelopes: list[ToolEnvelope]) -> VerifiedAnswer:
        records = {r.source_id: r for e in envelopes for r in e.records}
        empties = {e.tool for e in envelopes if e.empty}
        kept: list[VerifiedClaim] = []
        rejections: Counter[str] = Counter()
        for claim in draft.claims:
            reason = self._reject_reason(claim, records, empties)
            if reason:
                rejections[reason] += 1
            else:
                kept.append(VerifiedClaim(text=claim.text, source_ids=claim.source_ids, kind=claim.kind))
        dropped = sum(rejections.values())
        advice_dropped, unsupported = rejections[R_OUTPUT_RULES], rejections[R_UNSUPPORTED_ASSERTION]
        findings = run_rules(envelopes, self._today())
        flags = [self._flag(f) for f in findings]
        notes = []
        if dropped:
            notes.append(f"{dropped} statement{'s' if dropped != 1 else ''} could not be verified and "
                         f"{'were' if dropped != 1 else 'was'} removed.")
        if advice_dropped:  # say why, so the physician is not left guessing what was withheld
            notes.append(ADVICE_WITHHELD_NOTE)
        if unsupported:
            notes.append(UNSUPPORTED_ASSERTION_NOTE)
        if draft.claims and not kept:
            notes.append("Limited verified information is available; see the raw sources.")
        for e in envelopes:
            notes.extend(e.notes)
        if rejections:  # counts only: never the claim text, which may carry PHI
            logger.info("claims rejected", extra={"fields": {"rejections": dict(rejections)}})
        return VerifiedAnswer(claims=[*kept, *flags], dropped_count=dropped, notes=list(dict.fromkeys(notes)),
                              rejections=dict(rejections))

    @staticmethod
    def _flag(f: RuleFinding) -> VerifiedClaim:
        return VerifiedClaim(text=f.text, source_ids=list(f.source_ids), kind=ClaimKind.FLAG, rule_based=True)

    def _reject_reason(self, claim: Claim, records: dict, empties: set) -> str | None:
        """None when the claim passes; otherwise a fixed reason code (never claim text)."""
        if claim.kind is ClaimKind.FLAG:
            return R_MODEL_FLAG  # flags are produced by the rules layer only
        if claim.kind is ClaimKind.ABSENCE:
            return None if self._absence_ok(claim, empties) else R_ABSENCE
        if not claim.source_ids:
            return R_UNCITED
        if any(s not in records for s in claim.source_ids):
            return R_UNKNOWN_SOURCE
        haystack = _norm(" ".join(_fields(records[s]) for s in claim.source_ids))
        if not _numbers(claim.text) <= _numbers(haystack):
            return R_NUMBER
        if not _dates(claim.text) <= _dates(haystack):
            return R_DATE
        if self._violates(claim, records):
            return R_OUTPUT_RULES
        if self._unsupported_assertion(claim, records):
            return R_UNSUPPORTED_ASSERTION
        return None

    @staticmethod
    def _absence_ok(claim: Claim, empties: set) -> bool:
        t = claim.text
        if claim.source_ids or not _ABSENCE_OK.search(t) or _ABSENCE_BAD.search(t):
            return False
        wanted = {tool for key, tool in _ABSENCE_TOOL.items() if key in t.lower()}
        return bool(wanted) and wanted <= empties

    @staticmethod
    def _unsupported_assertion(claim: Claim, records: dict) -> bool:
        """True when a fact asserts an interaction or risk that none of its cited records states."""
        words = {m.group(0).lower() for m in _CLINICAL_ASSERTION.finditer(claim.text)}
        if not words:
            return False
        haystack = _norm(" ".join(_fields(records[s]) for s in claim.source_ids if s in records))
        return not all(w in haystack for w in words)

    @staticmethod
    def _violates(claim: Claim, records: dict) -> bool:
        verbatim = tuple(str(getattr(records[s], "dose_text", "") or "") for s in claim.source_ids if s in records)
        return violates_output_rules(claim.text, verbatim)
