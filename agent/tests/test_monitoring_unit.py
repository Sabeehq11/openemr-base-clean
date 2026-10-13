"""Test 2 regression: missing monitoring evidence is reported by the rules layer, memory-based pharmacology is dropped,
and every dropped claim gets a fixed reason code (counts only, no claim text)."""
import json
import logging
from datetime import date, datetime, timezone

import pytest

from app.monitoring import evidence_gaps
from app.rules import run_rules
from app.verifier import Verifier
from contracts import AnswerDraft, Claim, ClaimKind, LabResult, Medication, ToolEnvelope, ToolName

NOW = datetime(2026, 10, 13, tzinfo=timezone.utc)
TODAY = date(2026, 10, 13)


def env(tool, records):
    return ToolEnvelope(tool=tool, patient_uuid="p", retrieved_at=NOW, records=records, empty=not records,
                        source_ids=[r.source_id for r in records])


def med(i, name, status="active"):
    return Medication(source_id=f"MedicationRequest/{i}", name=name, dose_text="5 mg daily", status=status,
                      authored_on=date(2026, 1, 2))


def lab(i, test, loinc=None):
    return LabResult(source_id=f"Observation/{i}", test=test, loinc=loinc, value=1.0, unit="x", ref_low=0.5, ref_high=2.0,
                     flag_reported=None, collected=date(2026, 10, 1))


MARIA_MEDS = [med("m1", "Lisinopril"), med("m2", "Warfarin"), med("m3", "Atorvastatin"), med("m4", "Metformin")]
MARIA_LABS = [lab("o1", "Hemoglobin A1c"), lab("o2", "Creatinine"), lab("o3", "Glucose"), lab("o4", "LDL Cholesterol")]


def gaps(meds, labs, lab_tool=True):
    envs = [env(ToolName.GET_MEDICATIONS, meds)]
    if lab_tool:
        envs.append(env(ToolName.GET_LABS, labs))
    return evidence_gaps(envs)


def test_marias_chart_reports_inr_and_potassium_gaps_only():
    got = {(d, lbl): ids for d, lbl, ids in gaps(MARIA_MEDS, MARIA_LABS)}
    assert got == {("Warfarin", "INR"): ("MedicationRequest/m2",), ("Lisinopril", "potassium"): ("MedicationRequest/m1",)}


def test_the_gap_is_a_cited_rule_based_flag_worded_as_what_the_records_lack():
    envs = [env(ToolName.GET_MEDICATIONS, MARIA_MEDS), env(ToolName.GET_LABS, MARIA_LABS)]
    f = [x for x in run_rules(envs, TODAY) if x.rule == "monitoring_evidence_gap"]
    assert {x.text for x in f} == {
        "No INR result is in the retrieved lab results for the active medication Warfarin.",
        "No potassium result is in the retrieved lab results for the active medication Lisinopril."}
    assert all(x.source_ids for x in f)
    assert not any(w in x.text.lower() for x in f for w in ("require", "should", "monitor", "risk"))


@pytest.mark.parametrize("labs", [[lab("o9", "INR")], [lab("o9", "Prothrombin time")], [lab("o9", "x", "6301-6")]])
def test_a_present_inr_under_any_listed_name_or_loinc_is_no_gap(labs):
    assert gaps([med("m2", "Warfarin")], labs) == []


def test_a_lab_name_that_merely_contains_the_letters_does_not_count():
    assert gaps([med("m2", "Warfarin")], [lab("o9", "Painter panel")])  # 'inr' is not a whole word there


def test_no_statement_when_the_lab_tool_was_not_called_or_failed():
    assert gaps(MARIA_MEDS, [], lab_tool=False) == []


def test_no_statement_when_the_medication_tool_is_missing():
    assert evidence_gaps([env(ToolName.GET_LABS, MARIA_LABS)]) == []


def test_stopped_medications_and_unlisted_drugs_are_not_checked():
    assert gaps([med("m2", "Warfarin", status="stopped"), med("m5", "Acetaminophen")], MARIA_LABS) == []


def test_several_drugs_missing_the_same_lab_are_grouped_into_one_line():
    out = gaps([med("m1", "Lisinopril"), med("m6", "Enalapril")], [lab("o1", "Creatinine")])
    assert out == [("Enalapril and Lisinopril", "potassium", ("MedicationRequest/m1", "MedicationRequest/m6"))]


# ---- verifier safeguards and reason codes --------------------------------------------------------------------------

M = med("m2", "Warfarin")
ENVS = [env(ToolName.GET_MEDICATIONS, [M])]


def verify(*claims):
    return Verifier(today=lambda: TODAY).verify(AnswerDraft(claims=list(claims)), ENVS)


def fact(text):
    return Claim(text=text, source_ids=["MedicationRequest/m2"], kind=ClaimKind.FACT)


@pytest.mark.parametrize("text", [
    "Warfarin requires regular INR monitoring", "Warfarin is a high-risk medication", "Warfarin has a narrow therapeutic index",
    "Warfarin should be monitored closely", "Warfarin needs close surveillance", "Warfarin has dangerous side effects"])
def test_memory_based_pharmacology_about_a_cited_drug_is_dropped(text):
    a = verify(fact(text))
    assert [c for c in a.claims if not c.rule_based] == [] and a.rejections == {"unsupported_assertion": 1}


def test_a_recorded_fact_without_that_wording_is_kept():
    a = verify(fact("Warfarin recorded as 5 mg daily, status active"))
    assert a.rejections == {} and a.dropped_count == 0


def test_every_rejection_gets_one_fixed_code():
    a = verify(Claim(text="Warfarin 5 mg daily", source_ids=[], kind=ClaimKind.FACT),
               Claim(text="Warfarin 5 mg daily", source_ids=["MedicationRequest/zzz"], kind=ClaimKind.FACT),
               fact("Warfarin 9 mg daily"),
               fact("Warfarin ordered 2020-01-01"),
               fact("Start warfarin 5 mg daily"),
               Claim(text="Concerning", source_ids=[], kind=ClaimKind.FLAG),
               Claim(text="No INR documented.", source_ids=[], kind=ClaimKind.ABSENCE),
               fact("Warfarin may interact with other drugs"))
    assert a.rejections == {"uncited": 1, "unknown_source": 1, "number_mismatch": 1, "date_mismatch": 1,
                            "output_rules": 1, "model_flag": 1, "absence_unsupported": 1, "unsupported_assertion": 1}
    assert a.dropped_count == sum(a.rejections.values()) == 8


def test_rejection_logs_carry_counts_but_never_claim_text(caplog):
    with caplog.at_level(logging.INFO, logger="agent.verifier"):
        verify(fact("Warfarin 9 mg daily for Maria Alvarez"))
    rec = [r for r in caplog.records if r.name == "agent.verifier"]
    assert rec and rec[0].fields == {"rejections": {"number_mismatch": 1}}
    assert "Alvarez" not in json.dumps(rec[0].fields) and "Alvarez" not in rec[0].getMessage()
