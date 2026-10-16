from datetime import date

from app import mappers

TODAY = date(2026, 10, 12)


def test_age_is_computed_from_birth_date_and_today():
    assert mappers.age_years(date(1980, 10, 12), TODAY) == 46
    assert mappers.age_years(date(1980, 10, 13), TODAY) == 45


def test_age_is_none_when_birth_missing_or_in_future():
    assert mappers.age_years(None, TODAY) is None
    assert mappers.age_years(date(2030, 1, 1), TODAY) is None


def test_patient_summary_keeps_missing_birth_date_as_none():
    res = {"resourceType": "Patient", "id": "abc", "name": [{"given": ["Ann"], "family": "Lee"}], "gender": "female"}
    p = mappers.patient_summary(res, TODAY)
    assert p.source_id == "Patient/abc"
    assert p.name == "Ann Lee"
    assert p.birth_date is None and p.age_years is None


def test_problem_without_code_is_uncoded_not_invented():
    res = {"resourceType": "Condition", "id": "c1", "code": {"text": "Chest pain"}}
    p = mappers.problem(res)
    assert p.title == "Chest pain" and p.code is None and p.onset is None


def test_problem_with_coding_and_onset():
    res = {"resourceType": "Condition", "id": "c2", "onsetDateTime": "2024-03-01T00:00:00+00:00",
           "code": {"coding": [{"system": "http://snomed.info/sct", "code": "44054006", "display": "Type 2 diabetes"}]},
           "clinicalStatus": {"coding": [{"code": "active"}]}}
    p = mappers.problem(res)
    assert (p.title, p.code, p.status, p.onset) == ("Type 2 diabetes", "44054006", "active", date(2024, 3, 1))


def test_medication_dose_text_is_verbatim():
    res = {"resourceType": "MedicationRequest", "id": "m1", "status": "active", "authoredOn": "2025-01-02",
           "medicationCodeableConcept": {"text": "Metformin"},
           "dosageInstruction": [{"text": "500 mg twice daily"}]}
    m = mappers.medication(res)
    assert (m.name, m.dose_text, m.authored_on) == ("Metformin", "500 mg twice daily", date(2025, 1, 2))


def test_medication_without_dose_is_none():
    m = mappers.medication({"resourceType": "MedicationRequest", "id": "m2", "medicationCodeableConcept": {"text": "X"}})
    assert m.dose_text is None


def test_allergy_unknown_code_falls_back_to_narrative():
    res = {"resourceType": "Allergy" "Intolerance", "id": "a1",
           "code": {"coding": [{"code": "unknown"}]},
           "text": {"div": "<div xmlns='http://www.w3.org/1999/xhtml'>Penicillin</div>"}}
    assert mappers.allergy(res).substance == "Penicillin"


def test_allergy_narrative_html_entities_are_decoded_once():
    res = {"resourceType": "AllergyIntolerance", "id": "a3",
           "code": {"coding": [{"code": "unknown"}]},
           "text": {"div": "<div xmlns='http://www.w3.org/1999/xhtml'>Penicillin & sulfa <rash></div>"}}
    assert mappers.allergy(res).substance == "Penicillin & sulfa <rash>"


def test_display_text_html_entities_are_decoded_once():
    res = {"resourceType": "Condition", "id": "c1", "code": {"text": "Type 2 diabetes & CKD"}}
    assert mappers.problem(res).title == "Type 2 diabetes & CKD"


def test_allergy_reaction_is_none_when_absent():
    res = {"resourceType": "AllergyIntolerance", "id": "a2", "code": {"text": "Sulfa"}}
    a = mappers.allergy(res)
    assert a.substance == "Sulfa" and a.reaction is None


def test_lab_maps_value_range_and_reported_flag_without_recomputing():
    res = {"resourceType": "Observation", "id": "o1", "effectiveDateTime": "2026-09-01T08:00:00+00:00",
           "code": {"coding": [{"system": "http://loinc.org", "code": "4548-4", "display": "HbA1c"}]},
           "valueQuantity": {"value": 9.1, "unit": "%"},
           "referenceRange": [{"low": {"value": 4.0}, "high": {"value": 5.6}}],
           "interpretation": [{"coding": [{"code": "N"}]}]}
    lab = mappers.lab(res)
    assert (lab.test, lab.loinc, lab.value, lab.unit) == ("HbA1c", "4548-4", 9.1, "%")
    assert (lab.ref_low, lab.ref_high) == (4.0, 5.6)
    assert lab.flag_reported == "N"  # stored flag preserved even though 9.1 > 5.6; the verifier recomputes
    assert lab.collected == date(2026, 9, 1)


def test_lab_without_value_is_none():
    lab = mappers.lab({"resourceType": "Observation", "id": "o2", "code": {"text": "Panel"}})
    assert lab.value is None and lab.loinc is None


def test_encounter_mapping():
    res = {"resourceType": "Encounter", "id": "e1", "period": {"start": "2026-08-30T10:00:00+00:00"},
           "reasonCode": [{"text": "Follow-up"}]}
    e = mappers.encounter(res)
    assert (e.date, e.reason) == (date(2026, 8, 30), "Follow-up")
