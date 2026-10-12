from datetime import date, datetime, timezone

import pytest

from app.verifier import Verifier
from contracts import (AnswerDraft, Claim, ClaimKind, LabResult, Medication, Allergy, Problem, ToolEnvelope, ToolName)

TODAY = date(2026, 10, 12)
NOW = datetime(2026, 10, 12, tzinfo=timezone.utc)


def env(tool, records):
    return ToolEnvelope(tool=tool, patient_uuid="p", retrieved_at=NOW, records=records, empty=not records,
                        source_ids=[r.source_id for r in records])


LAB = LabResult(source_id="Observation/o1", test="HbA1c", loinc="4548-4", value=9.1, unit="%", ref_low=4.0,
                ref_high=5.6, flag_reported="N", collected=date(2026, 9, 1))
MED = Medication(source_id="MedicationRequest/m1", name="Metformin", dose_text="500 mg twice daily", status="active",
                 authored_on=date(2026, 1, 5))
PROB = Problem(source_id="Condition/c1", title="Hypertension", code=None, code_system=None, status="active", onset=None)
ENVS = [env(ToolName.GET_LABS, [LAB]), env(ToolName.GET_MEDICATIONS, [MED]), env(ToolName.GET_PROBLEMS, [PROB]),
        env(ToolName.GET_ALLERGIES, [])]


def fact(text, ids, kind=ClaimKind.FACT):
    return Claim(text=text, source_ids=ids, kind=kind)


def verify(*claims, envs=ENVS):
    return Verifier(today=lambda: TODAY).verify(AnswerDraft(claims=list(claims)), envs)


def texts(a):
    return [c.text for c in a.claims if not c.rule_based]


def test_grounded_fact_is_kept():
    a = verify(fact("HbA1c was 9.1 on 2026-09-01.", ["Observation/o1"]))
    assert texts(a) == ["HbA1c was 9.1 on 2026-09-01."] and a.dropped_count == 0


def test_fact_with_no_source_is_dropped():
    a = verify(fact("HbA1c was 9.1.", []))
    assert texts(a) == [] and a.dropped_count == 1


def test_fact_citing_unknown_source_is_dropped():
    assert verify(fact("HbA1c was 9.1.", ["Observation/zzz"])).dropped_count == 1


def test_one_bad_source_among_good_drops_the_claim():
    assert verify(fact("HbA1c was 9.1.", ["Observation/o1", "Observation/zzz"])).dropped_count == 1


@pytest.mark.parametrize("text", ["HbA1c was 7.1.", "HbA1c was 9.1 on 2026-09-02.", "HbA1c was 9.1 on 2025-09-01."])
def test_wrong_value_or_date_is_dropped(text):
    assert verify(fact(text, ["Observation/o1"])).dropped_count == 1


def test_number_formatting_is_normalised():
    assert verify(fact("HbA1c was 9.10.", ["Observation/o1"])).dropped_count == 0


def test_invented_extra_number_is_dropped():
    assert verify(fact("HbA1c was 9.1 and the dose was 20.", ["Observation/o1"])).dropped_count == 1


def test_claim_citing_a_real_but_unrelated_record_for_its_numbers_is_dropped():
    assert verify(fact("Metformin 9.1", ["MedicationRequest/m1"])).dropped_count == 1


def test_recorded_dose_is_quotable_but_directive_wording_is_not():
    assert verify(fact("Metformin 500 mg twice daily is on the medication list.", ["MedicationRequest/m1"])).dropped_count == 0
    assert verify(fact("Start metformin 500 mg twice daily.", ["MedicationRequest/m1"])).dropped_count == 1


def test_diagnosis_wording_is_dropped_even_when_cited():
    assert verify(fact("Findings are consistent with uncontrolled diabetes.", ["Observation/o1"])).dropped_count == 1


def test_model_cannot_originate_flags():
    a = verify(fact("Critical lab", ["Observation/o1"], ClaimKind.FLAG))
    assert a.dropped_count == 1 and all(c.rule_based for c in a.claims)


def test_absence_matching_empty_tool_is_kept():
    a = verify(fact("No allergies documented in the record.", [], ClaimKind.ABSENCE))
    assert a.dropped_count == 0 and "No allergies documented" in texts(a)[0]


@pytest.mark.parametrize("text", ["Patient has no known allergies.", "Allergies: none.", "NKDA.", "No allergies."])
def test_absence_worded_as_assertion_is_dropped(text):
    assert verify(fact(text, [], ClaimKind.ABSENCE)).dropped_count == 1


def test_absence_for_nonempty_tool_is_dropped():
    assert verify(fact("No medications documented.", [], ClaimKind.ABSENCE)).dropped_count == 1


def test_absence_with_no_identifiable_tool_is_dropped():
    assert verify(fact("Nothing is documented.", [], ClaimKind.ABSENCE)).dropped_count == 1


def test_absence_claim_with_sources_is_dropped():
    assert verify(fact("No allergies documented.", ["Observation/o1"], ClaimKind.ABSENCE)).dropped_count == 1


def test_rule_flags_are_appended_with_sources_and_marked_rule_based():
    a = verify()
    flags = [c for c in a.claims if c.rule_based]
    assert any("HbA1c" in c.text and "high" in c.text for c in flags)
    assert all(c.kind is ClaimKind.FLAG and c.source_ids for c in flags)


def test_rules_run_even_when_every_model_claim_is_dropped():
    a = verify(fact("Totally invented 42.", ["Observation/o1"]))
    assert a.dropped_count == 1 and any(c.rule_based for c in a.claims)
    assert any("could not be verified" in n for n in a.notes) and any("Limited verified" in n for n in a.notes)


def test_dropped_count_note_is_pluralised():
    a = verify(fact("x 1", []), fact("y 2", []))
    assert "2 statements could not be verified and were removed." in a.notes


def test_tool_coverage_notes_are_passed_through_once():
    e = ToolEnvelope(tool=ToolName.GET_MEDICATIONS, patient_uuid="p", retrieved_at=NOW, records=[MED], empty=False,
                     source_ids=[MED.source_id], notes=["lists store gap"])
    a = verify(envs=[e, e])
    assert a.notes.count("lists store gap") == 1


def test_allergy_conflict_flag_appears_end_to_end():
    al = Allergy(source_id="AllergyIntolerance/a1", substance="Penicillin", status="active", reaction=None, criticality=None)
    amox = Medication(source_id="MedicationRequest/m2", name="Amoxicillin", dose_text=None, status="active", authored_on=None)
    a = verify(envs=[env(ToolName.GET_ALLERGIES, [al]), env(ToolName.GET_MEDICATIONS, [amox])])
    assert any("same drug class" in c.text for c in a.claims)
