"""Source cards: a read-only summary of each cited record, so a citation chip can show what it points at.

Cards are built only from records fetched in this request (the same ones the verifier resolved the citation against),
and only for ids a claim actually cites. Direct identifiers are never included: a patient card shows age and sex only.
"""
from __future__ import annotations

from contracts import (Allergy, ChangeItem, Encounter, LabResult, Medication, PatientSummary, Problem, SourceCard,
                       SourceField, ToolEnvelope, VerifiedAnswer)


def _fields(*pairs: tuple[str, object]) -> list[SourceField]:
    return [SourceField(label=k, value=str(v)) for k, v in pairs if v is not None and str(v).strip() != ""]


def _card(rec: object) -> SourceCard | None:
    sid = getattr(rec, "source_id", "")
    if isinstance(rec, Medication):
        return SourceCard(source_id=sid, kind="Medication", title=rec.name, fields=_fields(
            ("Dose", rec.dose_text), ("Status", rec.status), ("Prescribed", rec.authored_on), ("Store", rec.store)))
    if isinstance(rec, Problem):
        return SourceCard(source_id=sid, kind="Problem", title=rec.title, fields=_fields(
            ("Code", f"{rec.code} ({rec.code_system})" if rec.code and rec.code_system else rec.code),
            ("Status", rec.status), ("Onset", rec.onset)))
    if isinstance(rec, Allergy):
        return SourceCard(source_id=sid, kind="Allergy", title=rec.substance or "(unnamed substance)", fields=_fields(
            ("Reaction", rec.reaction), ("Status", rec.status), ("Criticality", rec.criticality)))
    if isinstance(rec, LabResult):
        value = None if rec.value is None else f"{rec.value:g}{' ' + rec.unit if rec.unit else ''}"
        rng = None if rec.ref_low is None or rec.ref_high is None else f"{rec.ref_low:g} to {rec.ref_high:g}"
        return SourceCard(source_id=sid, kind="Lab result", title=rec.test, fields=_fields(
            ("Value", value), ("Reference range", rng), ("Flag stored in chart", rec.flag_reported or "none"),
            ("Collected", rec.collected), ("LOINC", rec.loinc)))
    if isinstance(rec, Encounter):
        return SourceCard(source_id=sid, kind="Encounter", title=rec.reason or "Encounter", fields=_fields(
            ("Date", rec.date), ("Type", rec.kind)))
    if isinstance(rec, ChangeItem):
        return SourceCard(source_id=sid, kind=rec.resource_type, title=rec.summary, fields=_fields(
            ("Date", rec.changed_on)))
    if isinstance(rec, PatientSummary):  # age and sex only: no name, no birth date, no uuid
        return SourceCard(source_id=sid, kind="Patient", title="Patient demographics", fields=_fields(
            ("Age", rec.age_years), ("Sex", rec.sex)))
    return None


def cards_for(source_ids: list[str], envelopes: list[ToolEnvelope]) -> list[SourceCard]:
    """One card per id, in the order first cited. Ids with no fetched record get no card."""
    records: dict[str, object] = {}
    for e in envelopes:
        for r in e.records:
            # A changes-since item shares its id with the full record; keep the fuller record when both were fetched.
            if r.source_id not in records or isinstance(records[r.source_id], ChangeItem):
                records[r.source_id] = r
    out: list[SourceCard] = []
    for sid in dict.fromkeys(source_ids):
        card = _card(records[sid]) if sid in records else None
        if card is not None:
            out.append(card)
    return out


def all_cards(envelopes: list[ToolEnvelope]) -> list[SourceCard]:
    """Cards for every fetched record (used by the degraded view, where the chart data itself is the answer)."""
    return cards_for([r.source_id for e in envelopes for r in e.records], envelopes)


def attach_sources(answer: VerifiedAnswer, envelopes: list[ToolEnvelope]) -> VerifiedAnswer:
    cited = [s for c in answer.claims for s in c.source_ids]
    return answer.model_copy(update={"sources": cards_for(cited, envelopes)})
