"""Pure FHIR -> contract mappers. No network. Missing data stays None; nothing is inferred or normalised."""
from __future__ import annotations

import html
from datetime import date, datetime

from contracts import Allergy, Encounter, LabResult, Medication, PatientSummary, Problem


def _date(value: object) -> date | None:
    if not isinstance(value, str) or len(value) < 10:
        return None
    try:
        return datetime.fromisoformat(value[:10]).date()
    except ValueError:
        return None


def _sid(res: dict) -> str:
    return f"{res.get('resourceType', 'Unknown')}/{res.get('id', '')}"


def _first_coding(cc: object) -> dict:
    if isinstance(cc, dict):
        codings = cc.get("coding") or []
        if codings and isinstance(codings[0], dict):
            return codings[0]
    return {}


def _plain(value: object) -> str | None:
    """FHIR narrative and display strings can carry HTML entities. Decode them once at this boundary."""
    if not isinstance(value, str):
        return None
    text = html.unescape(value).strip()
    return text or None


def _text(cc: object) -> str | None:
    if not isinstance(cc, dict):
        return None
    return _plain(cc.get("text")) or _plain(_first_coding(cc).get("display"))


def age_years(birth: date | None, today: date) -> int | None:
    if birth is None or birth > today:
        return None
    return today.year - birth.year - ((today.month, today.day) < (birth.month, birth.day))


def patient_summary(res: dict, today: date) -> PatientSummary:
    names = res.get("name") or []
    n = names[0] if names and isinstance(names[0], dict) else {}
    full = " ".join([*(n.get("given") or []), n.get("family") or ""]).strip() or None
    birth = _date(res.get("birthDate"))
    return PatientSummary(
        source_id=_sid(res), patient_uuid=str(res.get("id", "")), name=full, birth_date=birth,
        sex=res.get("gender") or None, age_years=age_years(birth, today),
    )


def problem(res: dict) -> Problem:
    coding = _first_coding(res.get("code"))
    return Problem(
        source_id=_sid(res), title=_text(res.get("code")) or "(untitled problem)",
        code=coding.get("code"), code_system=coding.get("system"),
        status=_first_coding(res.get("clinicalStatus")).get("code"), onset=_date(res.get("onsetDateTime")),
    )


def medication(res: dict) -> Medication:
    dosage = res.get("dosageInstruction") or []
    dose = dosage[0].get("text") if dosage and isinstance(dosage[0], dict) else None
    return Medication(
        source_id=_sid(res), name=_text(res.get("medicationCodeableConcept")) or "(unnamed medication)",
        dose_text=dose or None, status=res.get("status"), authored_on=_date(res.get("authoredOn")),
    )


def allergy(res: dict) -> Allergy:
    code = res.get("code")
    coding = _first_coding(code)
    substance = None if coding.get("code") == "unknown" and not (code or {}).get("text") else _text(code)
    if substance is None:  # OpenEMR puts the title in the narrative when the coded substance is unknown
        div = (res.get("text") or {}).get("div", "")
        substance = _plain(div.replace("<div xmlns='http://www.w3.org/1999/xhtml'>", "").replace("</div>", ""))
    reactions = res.get("reaction") or []
    reaction = None
    if reactions and isinstance(reactions[0], dict):
        manifestations = reactions[0].get("manifestation") or []
        reaction = (_text(manifestations[0]) if manifestations else None) or None
    return Allergy(
        source_id=_sid(res), substance=substance,
        status=_first_coding(res.get("clinicalStatus")).get("code"), reaction=reaction,
        criticality=res.get("criticality"),
    )


def _qty(q: object) -> float | None:
    if isinstance(q, dict) and isinstance(q.get("value"), (int, float)):
        return float(q["value"])
    return None


def lab(res: dict) -> LabResult:
    coding = _first_coding(res.get("code"))
    vq = res.get("valueQuantity") if isinstance(res.get("valueQuantity"), dict) else {}
    rng = (res.get("referenceRange") or [{}])[0]
    interp = _first_coding((res.get("interpretation") or [None])[0]).get("code")
    return LabResult(
        source_id=_sid(res), test=coding.get("display") or _text(res.get("code")) or "(unnamed test)",
        loinc=coding.get("code") if coding.get("system", "").endswith("loinc.org") else None,
        value=_qty(vq), unit=vq.get("unit"), ref_low=_qty(rng.get("low")), ref_high=_qty(rng.get("high")),
        flag_reported=interp, collected=_date(res.get("effectiveDateTime")),
    )


def encounter(res: dict) -> Encounter:
    reasons = res.get("reasonCode") or []
    return Encounter(
        source_id=_sid(res), date=_date((res.get("period") or {}).get("start")),
        reason=_text(reasons[0]) if reasons else None, kind=_text((res.get("type") or [None])[0]),
    )
