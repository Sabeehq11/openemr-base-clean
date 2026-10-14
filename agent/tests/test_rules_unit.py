from datetime import date

import pytest

from app import rules
from contracts import Allergy, LabResult, Medication

TODAY = date(2026, 10, 12)


def lab(value, low=4.0, high=5.6, flag=None, test="HbA1c", loinc="4548-4", collected=date(2026, 9, 1), sid="Observation/o1"):
    return LabResult(source_id=sid, test=test, loinc=loinc, value=value, unit="%", ref_low=low, ref_high=high,
                     flag_reported=flag, collected=collected)


def med(name, status="active", sid="MedicationRequest/m1"):
    return Medication(source_id=sid, name=name, dose_text=None, status=status, authored_on=None)


def allergy(sub, status="active", sid="AllergyIntolerance/a1"):
    return Allergy(source_id=sid, substance=sub, status=status, reaction=None, criticality=None)


@pytest.mark.parametrize("value,expected", [(3.9, "low"), (4.0, "normal"), (5.6, "normal"), (5.7, "high"), (9.1, "high")])
def test_recompute_flag_boundaries(value, expected):
    assert rules.recompute_flag(lab(value)) == expected


def test_recompute_is_none_without_value_or_range():
    assert rules.recompute_flag(lab(None)) is None
    assert rules.recompute_flag(lab(5.0, low=None, high=None)) is None


def test_one_sided_range_is_used():
    assert rules.recompute_flag(lab(7.0, low=None, high=5.6)) == "high"
    assert rules.recompute_flag(lab(1.0, low=4.0, high=None)) == "low"


def test_stored_normal_flag_on_high_value_is_reported():
    f = rules.lab_flag_findings([lab(9.1, flag="N")])
    assert len(f) == 1 and f[0].rule == "lab_flag_recompute" and f[0].source_ids == ("Observation/o1",)
    assert "high" in f[0].text and "chart flag is: N" in f[0].text


def test_lab_flag_finding_states_the_collection_date():
    f = rules.lab_flag_findings([lab(9.1, flag="N", collected=date(2024, 11, 11))])
    assert "(collected 2024-11-11)" in f[0].text


def test_lab_flag_finding_says_when_the_date_is_unknown():
    f = rules.lab_flag_findings([lab(9.1, flag="N", collected=None)])
    assert "(collection date not recorded)" in f[0].text and "None" not in f[0].text


def test_old_and_current_findings_for_one_test_are_told_apart():
    old = lab(9.1, flag="N", collected=date(2024, 11, 11), sid="Observation/old")
    new = lab(9.4, flag="N", collected=date(2026, 9, 12), sid="Observation/new")
    texts = {f.source_ids[0]: f.text for f in rules.lab_flag_findings([new, old])}
    assert "collected 2024-11-11" in texts["Observation/old"] and "collected 2026-09-12" in texts["Observation/new"]


def test_matching_stored_flag_produces_no_finding():
    assert rules.lab_flag_findings([lab(9.1, flag="H")]) == []


def test_missing_stored_flag_on_abnormal_value_is_reported():
    assert "no flag stored" in rules.lab_flag_findings([lab(9.1)])[0].text


def test_normal_value_produces_no_finding():
    assert rules.lab_flag_findings([lab(5.0)]) == []


def test_penicillin_allergy_with_amoxicillin_conflicts_and_cites_both():
    f = rules.allergy_conflicts([allergy("Penicillin")], [med("Amoxicillin")])
    assert len(f) == 1 and set(f[0].source_ids) == {"AllergyIntolerance/a1", "MedicationRequest/m1"}


def test_no_conflict_for_unrelated_drug_or_inactive_med_or_inactive_allergy():
    assert rules.allergy_conflicts([allergy("Penicillin")], [med("Metformin")]) == []
    assert rules.allergy_conflicts([allergy("Penicillin")], [med("Amoxicillin", status="stopped")]) == []
    assert rules.allergy_conflicts([allergy("Penicillin", status="inactive")], [med("Amoxicillin")]) == []


def test_allergy_to_a_specific_drug_matches_class_members():
    assert len(rules.allergy_conflicts([allergy("Aspirin")], [med("Ibuprofen")])) >= 1


def test_unknown_allergen_is_not_flagged():
    assert rules.allergy_conflicts([allergy("Latex")], [med("Amoxicillin")]) == []
    assert rules.allergy_conflicts([allergy(None)], [med("Amoxicillin")]) == []


def test_a1c_older_than_six_months_is_stale():
    f = rules.stale_labs([lab(6.0, collected=date(2026, 1, 1))], TODAY)
    assert len(f) == 1 and f[0].rule == "stale_lab"


def test_recent_a1c_is_not_stale_and_only_latest_counts():
    old, new = lab(6.0, collected=date(2025, 1, 1), sid="Observation/old"), lab(6.0, collected=date(2026, 9, 1))
    assert rules.stale_labs([old, new], TODAY) == []


def test_other_labs_have_no_staleness_rule():
    assert rules.stale_labs([lab(4.1, test="Potassium", loinc="2823-3", collected=date(2020, 1, 1))], TODAY) == []


def test_duplicate_active_medications_found_case_insensitively():
    f = rules.duplicate_medications([med("Metformin", sid="MedicationRequest/1"), med("metformin", sid="MedicationRequest/2"),
                                     med("Metformin", status="stopped", sid="MedicationRequest/3")])
    assert len(f) == 1 and f[0].source_ids == ("MedicationRequest/1", "MedicationRequest/2")


@pytest.mark.parametrize("text", [
    "The patient likely has type 2 diabetes.", "This is consistent with heart failure.",
    "Start lisinopril 10 mg.", "I recommend increasing the dose.", "Consider taking aspirin daily.",
    "Discontinue metformin.", "Differential includes anemia.", "Take 500 mg twice daily.",
])
def test_output_rules_reject_diagnosis_and_treatment_direction(text):
    assert rules.violates_output_rules(text)


@pytest.mark.parametrize("text", [
    "Metformin is on the active medication list.", "HbA1c was 9.1 % on 2026-09-01.",
    "Allergy recorded: Penicillin (rash).", "Problem list includes hypertension.",
])
def test_output_rules_allow_plain_chart_facts(text):
    assert not rules.violates_output_rules(text)


def test_recorded_dose_text_is_allowed_verbatim():
    assert rules.violates_output_rules("Metformin 500 mg twice daily")
    assert not rules.violates_output_rules("Metformin 500 mg twice daily", ("500 mg twice daily",))
