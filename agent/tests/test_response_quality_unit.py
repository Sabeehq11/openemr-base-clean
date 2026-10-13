"""Regression tests for the Maria Alvarez manual tests.

Test 3 (a request for a specific new warfarin dose) must be refused explicitly, with recorded medication facts only when
an authorized tool returned them, each cited, and never a new dose. Test 2 (medications and concerns) must reach the
model, keep concerns tied to records or rules, and report what was checked only from tool outcomes.
"""
import json
from datetime import date, datetime, timezone

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.advice import AdviceKind, classify
from app.chat import build_chat_router
from app.config import Settings
from app.conversation import ConversationStore
from app.fhir_client import FhirClient
from app.gateway import Gateway
from app.llm import LLMResponse, StubLLM, ToolCall
from app.tools import ToolRunner
from app.verifier import UNSUPPORTED_ASSERTION_NOTE, Verifier
from contracts import AnswerDraft, Claim, ClaimKind, Medication, ToolEnvelope, ToolName
from tests.test_advice_unit import MANUAL_TEST_2, MANUAL_TEST_3
from tests.test_chat_unit import NOW, PID, Audit, Authz, chat, events

NEW_DOSE = ("7.5 mg", "7.5", "6 mg", "10 mg")
DEFAULT_MEDS = [("m1", "Warfarin", "5 mg daily"), ("m2", "Lisinopril", "10 mg daily")]


def build(med_status=200, llm=None, allow=True, meds=None):
    """A chat router over a mocked FHIR server holding Warfarin and Lisinopril, with the real verifier."""
    meds = DEFAULT_MEDS if meds is None else meds

    def handler(req):
        res = req.url.path.rsplit("/", 1)[-1]
        if res == "MedicationRequest":
            if med_status != 200:
                return httpx.Response(med_status)
            return httpx.Response(200, json={"resourceType": "Bundle", "entry": [
                {"resource": {"resourceType": "MedicationRequest", "id": i, "status": "active",
                              "authoredOn": "2026-01-02", "medicationCodeableConcept": {"text": n},
                              "dosageInstruction": [{"text": d}]}} for i, n, d in meds]})
        return httpx.Response(200, json={"resourceType": "Bundle", "entry": []})

    s = Settings(module_signing_secret=SecretStr("s"), openemr_base_url="http://oe")
    audit = Audit()
    runner = ToolRunner(Authz(allow), FhirClient(s, transport=httpx.MockTransport(handler)), audit=audit)
    verifier = Verifier(today=lambda: date(2026, 10, 13))
    app = FastAPI()
    app.include_router(build_chat_router(Gateway(s, audit, clock=lambda: NOW), runner, lambda: llm or StubLLM([]),
                                         verifier.verify, ConversationStore(), audit))
    return TestClient(app), audit


# ---- Test 3: the exact dosing prompt ----------------------------------------------------------------------------

def test_the_exact_test_3_prompt_is_classified_as_a_treatment_request():
    assert classify(MANUAL_TEST_3) is AdviceKind.TREATMENT


def test_test_3_is_refused_explicitly_with_cited_recorded_facts_and_no_model_call():
    llm = StubLLM([])  # raises if the model is called
    c, audit = build(llm=llm)
    ev = events(chat(c, question=MANUAL_TEST_3))
    assert [n for n, _ in ev] == ["conversation", "declined", "end"]
    d = dict(ev)["declined"]
    assert d["kind"] == "treatment"
    assert d["message"].startswith("I can't recommend, calculate or adjust a Warfarin dose")
    assert "won't suggest a new dose" in d["message"] and "Treatment decisions are yours" in d["message"]
    assert [cl["text"] for cl in d["claims"]] == ["Warfarin, recorded as 5 mg daily, status active"]  # the named drug
    assert all(cl["source_ids"] for cl in d["claims"])
    assert {s["source_id"] for s in d["sources"]} == {sid for cl in d["claims"] for sid in cl["source_ids"]}
    assert llm.calls == []
    assert audit.events[-1][1]["outcome"] == "declined"


def test_test_3_never_contains_a_new_dose():
    c, _ = build()
    d = dict(events(chat(c, question=MANUAL_TEST_3)))["declined"]
    shown = d["message"] + " " + " ".join(cl["text"] for cl in d["claims"]) + " ".join(d["notes"])
    assert not any(x in shown for x in NEW_DOSE)
    assert [ch for ch in d["message"] if ch.isdigit()] == []  # the refusal text carries no number at all


def test_the_medication_read_is_an_audited_authorized_tool_call():
    c, audit = build()
    chat(c, question=MANUAL_TEST_3)
    calls = [f for e, f in audit.events if e == "tool_call"]
    assert "get_medications" in [f["tool"] for f in calls]
    assert all(f["patient_uuid"] == PID for f in calls)


def test_an_unauthorized_caller_gets_only_the_generic_denial_and_no_medication_read():
    c, audit = build(allow=False)
    ev = dict(events(chat(c, question=MANUAL_TEST_3)))
    assert "denied" in ev and "declined" not in ev
    assert not any(e == "tool_call" for e, _ in audit.events)


def test_a_failed_medication_read_gives_the_refusal_and_says_unavailable_never_none():
    c, _ = build(med_status=500)
    d = dict(events(chat(c, question=MANUAL_TEST_3)))["declined"]
    assert d["message"].startswith("I can't recommend, calculate or adjust a dose")
    assert d["claims"] == [] and d["sources"] == []
    assert any("could not be retrieved" in n for n in d["notes"])
    assert not any("No medications" in n for n in d["notes"])


def test_an_empty_medication_list_is_stated_as_not_documented_in_the_retrieved_records():
    c, _ = build(meds=[])
    d = dict(events(chat(c, question=MANUAL_TEST_3)))["declined"]
    assert d["claims"] == [] and any("documented in the retrieved medication records" in n for n in d["notes"])


def test_when_the_drug_is_not_named_every_active_recorded_medication_is_shown():
    c, _ = build()
    d = dict(events(chat(c, question="How much should I increase the dose?")))["declined"]
    assert [cl["text"] for cl in d["claims"]] == ["Warfarin, recorded as 5 mg daily, status active",
                                                  "Lisinopril, recorded as 10 mg daily, status active"]


def test_a_diagnosis_decline_carries_no_medication_facts_and_makes_no_tool_call():
    c, audit = build()
    d = dict(events(chat(c, question="What is the diagnosis?")))["declined"]
    assert d["kind"] == "diagnosis" and d["claims"] == []
    assert not any(e == "tool_call" for e, _ in audit.events)


# ---- Test 2: the exact follow-up prompt -------------------------------------------------------------------------

def test_the_exact_test_2_prompt_reaches_the_model():
    assert classify(MANUAL_TEST_2) is None


def test_test_2_answer_lists_medications_and_reports_only_what_was_checked():
    llm = StubLLM([
        LLMResponse(tool_calls=[ToolCall("a", "get_medications", {}), ToolCall("b", "get_allergies", {}),
                                ToolCall("c", "get_labs", {})]),
        LLMResponse(text=json.dumps({"claims": [
            {"text": "Warfarin, recorded as 5 mg daily, status active", "kind": "fact",
             "source_ids": ["MedicationRequest/m1"]},
            {"text": "Warfarin and lisinopril interact", "kind": "fact",
             "source_ids": ["MedicationRequest/m1", "MedicationRequest/m2"]}]}))])
    c, _ = build(llm=llm)
    a = dict(events(chat(c, question=MANUAL_TEST_2)))["answer"]
    shown = [cl["text"] for cl in a["claims"]]
    assert "Warfarin, recorded as 5 mg daily, status active" in shown
    assert not any("interact" in t for t in shown)  # no record states an interaction, so the model cannot assert one
    assert UNSUPPORTED_ASSERTION_NOTE in a["notes"] and a["dropped_count"] == 1
    assert "Sources checked: medications (2)." in a["notes"]
    assert "No allergies documented in the retrieved records." in a["notes"]
    assert "No lab results documented in the retrieved records." in a["notes"]


def test_a_tool_that_failed_is_reported_unavailable_and_never_as_absence():
    llm = StubLLM([LLMResponse(tool_calls=[ToolCall("a", "get_medications", {})]),
                   LLMResponse(text=json.dumps({"claims": []}))])
    c, _ = build(llm=llm, med_status=500)
    a = dict(events(chat(c, question=MANUAL_TEST_2)))["answer"]
    assert any("medications could not be retrieved" in n for n in a["notes"])
    assert not any("No medications documented" in n for n in a["notes"])


def test_a_tool_that_was_never_called_produces_no_statement_at_all():
    llm = StubLLM([LLMResponse(tool_calls=[ToolCall("a", "get_medications", {})]),
                   LLMResponse(text=json.dumps({"claims": []}))])
    c, _ = build(llm=llm)
    a = dict(events(chat(c, question=MANUAL_TEST_2)))["answer"]
    assert not any("allerg" in n.lower() or "lab" in n.lower() for n in a["notes"])


# ---- verifier: unsupported clinical assertions are dropped, nothing else is weakened -----------------------------

NOW_DT = datetime(2026, 10, 13, tzinfo=timezone.utc)
ENV = ToolEnvelope(tool=ToolName.GET_MEDICATIONS, patient_uuid="p", retrieved_at=NOW_DT, empty=False,
                   source_ids=["MedicationRequest/m1"],
                   records=[Medication(source_id="MedicationRequest/m1", name="Warfarin", dose_text="5 mg daily",
                                       status="active", authored_on=date(2026, 1, 2))])


def verify(*claims):
    return Verifier(today=lambda: date(2026, 10, 13)).verify(AnswerDraft(claims=list(claims)), [ENV])


def kept(a):
    return [c.text for c in a.claims if not c.rule_based]


@pytest.mark.parametrize("text", ["Warfarin carries a bleeding risk", "Warfarin may interact with other drugs",
                                  "Warfarin is contraindicated here"])
def test_a_risk_or_interaction_claim_that_no_cited_record_states_is_dropped(text):
    a = verify(Claim(text=text, source_ids=["MedicationRequest/m1"], kind=ClaimKind.FACT))
    assert kept(a) == [] and a.dropped_count == 1 and UNSUPPORTED_ASSERTION_NOTE in a.notes


def test_a_plain_recorded_fact_is_still_kept():
    assert kept(verify(Claim(text="Warfarin 5 mg daily is active", source_ids=["MedicationRequest/m1"]))) == [
        "Warfarin 5 mg daily is active"]


def test_the_forbidden_output_rules_and_citation_checks_are_unchanged():
    refusal = Claim(text="I cannot recommend a warfarin dose; the chart shows warfarin 5 mg daily.",
                    source_ids=["MedicationRequest/m1"])
    uncited = Claim(text="Warfarin 5 mg daily is active", source_ids=[])
    wrong_number = Claim(text="Warfarin 7.5 mg daily is active", source_ids=["MedicationRequest/m1"])
    assert kept(verify(refusal, uncited, wrong_number)) == []


def test_a_model_flag_is_still_dropped():
    assert kept(verify(Claim(text="Check warfarin", source_ids=["MedicationRequest/m1"], kind=ClaimKind.FLAG))) == []


# ---- coverage notes come from tool outcomes only --------------------------------------------------------------

def env(tool, records=()):
    return ToolEnvelope(tool=tool, patient_uuid="p", retrieved_at=NOW_DT, empty=not records, records=list(records),
                        source_ids=[r.source_id for r in records])


def test_a_successful_empty_read_is_stated_as_not_documented_in_the_retrieved_records():
    from app.coverage import coverage_notes
    assert coverage_notes([env(ToolName.GET_ALLERGIES)]) == ["No allergies documented in the retrieved records."]


def test_a_failed_read_is_never_evidence_of_absence():
    from app.coverage import coverage_notes
    notes = coverage_notes([], failed={ToolName.GET_LABS})
    assert notes == ["The lab results could not be retrieved, so nothing is concluded about them."]


def test_a_failure_this_turn_overrides_an_earlier_success():
    from app.coverage import coverage_notes
    notes = coverage_notes([env(ToolName.GET_ALLERGIES)], failed={ToolName.GET_ALLERGIES})
    assert notes == ["The allergies could not be retrieved, so nothing is concluded about them."]


def test_an_uncalled_tool_is_not_mentioned_and_labs_without_inr_never_say_no_inr():
    from app.coverage import coverage_notes
    from contracts import LabResult
    lab = LabResult(source_id="Observation/o1", test="Creatinine", loinc="2160-0", value=1.0, unit="mg/dL",
                    ref_low=0.6, ref_high=1.3, flag_reported="N", collected=date(2026, 9, 1))
    notes = coverage_notes([env(ToolName.GET_LABS, [lab])])
    assert notes == ["Sources checked: lab results (1)."]
    assert not any("INR" in n or "No " in n for n in notes)
