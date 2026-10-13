"""Recorded medication facts shown next to a dosing refusal.

The model is not involved. The facts are built from fields of medication records that an authorized tool call returned
for this patient, each cites its record, and the result goes through the same verifier as a model answer before it is
shown. Nothing here states, suggests or computes a dose: only what the chart already says.
"""
from __future__ import annotations

import re

from contracts import AnswerDraft, Claim, ClaimKind, Medication, ToolEnvelope, ToolName

MEDICATIONS_UNAVAILABLE = "The recorded medication list could not be retrieved, so no recorded doses are shown."
NO_MEDICATIONS_NOTE = "No medications are documented in the retrieved medication records."
_INACTIVE = {"stopped", "completed", "cancelled", "entered-in-error", "inactive"}


def _mentioned(question: str, name: str) -> bool:
    return bool(name.strip()) and re.search(rf"\b{re.escape(name.strip().lower())}\b", question.lower()) is not None


def medications_of(envelope: ToolEnvelope | None) -> list[Medication]:
    if envelope is None or envelope.tool is not ToolName.GET_MEDICATIONS:
        return []
    return [r for r in envelope.records if isinstance(r, Medication)]


def select(question: str, envelope: ToolEnvelope | None) -> list[Medication]:
    """The medications the question names, else every active one. Names are matched against retrieved records only."""
    meds = medications_of(envelope)
    named = [m for m in meds if _mentioned(question, m.name)]
    return named or [m for m in meds if (m.status or "").lower() not in _INACTIVE]


def drug_names(meds: list[Medication]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(m.name.strip() for m in meds if m.name.strip()))


def fact_text(m: Medication) -> str:
    """Only recorded fields, in the chart's own words. The recorded dose text is quoted exactly, never altered."""
    parts = [m.name.strip()]
    if m.dose_text:
        parts.append(f"recorded as {m.dose_text.strip()}")
    else:
        parts.append("no dose text recorded")
    if m.status:
        parts.append(f"status {m.status}")
    return ", ".join(parts)


def draft_for(meds: list[Medication]) -> AnswerDraft:
    return AnswerDraft(claims=[Claim(text=fact_text(m), source_ids=[m.source_id], kind=ClaimKind.FACT) for m in meds][:40])
