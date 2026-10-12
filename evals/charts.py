"""Synthetic charts shaped like OpenEMR's FHIR output, including its known defects (audit D1 to D11)."""
from __future__ import annotations

from .harness import Chart


def lab(id: str, name: str, value: float, low: float | None, high: float | None, flag: str | None,
        day: str, loinc: str = "0000-0", unit: str = "mg/dL") -> dict:
    r: dict = {"id": id, "code": {"coding": [{"system": "http://loinc.org", "code": loinc, "display": name}]},
               "valueQuantity": {"value": value, "unit": unit}, "effectiveDateTime": f"{day}T00:00:00+00:00"}
    if low is not None or high is not None:
        r["referenceRange"] = [{**({"low": {"value": low}} if low is not None else {}),
                                **({"high": {"value": high}} if high is not None else {})}]
    if flag:
        r["interpretation"] = [{"coding": [{"code": flag}]}]
    return r


def med(id: str, name: str, status: str = "active", dose: str | None = None, day: str = "2026-01-10") -> dict:
    r: dict = {"id": id, "status": status, "medicationCodeableConcept": {"text": name}, "authoredOn": day}
    if dose:
        r["dosageInstruction"] = [{"text": dose}]
    return r


def problem(id: str, title: str, code: str | None = None, system: str | None = None, onset: str | None = None) -> dict:
    r: dict = {"id": id, "code": {"text": title}, "clinicalStatus": {"coding": [{"code": "active"}]}}
    if code:
        r["code"]["coding"] = [{"code": code, **({"system": system} if system else {})}]
    if onset:
        r["onsetDateTime"] = onset
    return r


def allergy(id: str, substance: str, reaction: str | None = None) -> dict:
    r: dict = {"id": id, "code": {"text": substance}, "clinicalStatus": {"coding": [{"code": "active"}]}}
    if reaction:
        r["reaction"] = [{"manifestation": [{"text": reaction}]}]
    return r


def full_chart() -> Chart:
    return Chart(resources={
        "Condition": [problem("c1", "Type 2 diabetes mellitus", "E11.9", "http://hl7.org/fhir/sid/icd-10-cm", "2019-03-02")],
        "MedicationRequest": [med("m1", "Metformin", dose="500 mg twice daily")],
        "AllergyIntolerance": [allergy("a1", "Penicillin", "rash")],
        "Observation": [lab("o1", "Hemoglobin A1c", 8.2, 4.0, 5.6, "H", "2026-09-01", "4548-4", "%")],
        "Encounter": [{"id": "e1", "period": {"start": "2026-09-01T00:00:00"}, "reasonCode": [{"text": "Follow-up"}]}],
    })


def empty_chart() -> Chart:
    return Chart(resources={})
