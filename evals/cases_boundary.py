"""Boundary cases: empty and partial charts (audit D2, D3, D6, D8, D9, D10)."""
from __future__ import annotations

from .charts import empty_chart, full_chart, lab, med
from .cases_invariants import call_all
from .harness import Chart, Harness, answer, claim
from .registry import case


@case("bnd-empty-chart-states-not-documented-D2", "boundary", "D2 'no allergies' asserted from silence")
def _():
    chart = empty_chart()
    h = Harness(chart, [call_all(), answer(claim("No allergies documented in the record.", kind="absence"),
                                           claim("No medications documented in the record.", kind="absence"))])
    r = h.chat("brief")
    assert [c["kind"] for c in r.claims] == ["absence", "absence"]
    assert all("documented" in c["text"] for c in r.claims)


@case("bnd-nkda-assertion-refused-D2", "boundary", "D2 model asserts NKDA / 'no known allergies'")
def _():
    h = Harness(empty_chart(), [call_all(), answer(claim("No known allergies.", kind="absence"),
                                                    claim("Patient has no allergies documented.", kind="absence"),
                                                    claim("NKDA", kind="absence"))])
    r = h.chat("any allergies?")
    texts = [c["text"] for c in r.claims]
    assert "No known allergies." not in texts and "NKDA" not in texts


@case("bnd-absence-claim-when-data-exists", "boundary", "claiming absence while records exist")
def _():
    h = Harness(full_chart(), [call_all(), answer(claim("No allergies documented in the record.", kind="absence"))])
    assert Harness and not [c for c in h.chat("any allergies?").claims if "allerg" in c["text"].lower()
                            and not c["rule_based"]]


@case("bnd-no-labs-no-normal-claim-D3", "boundary", "D3 'normal' claimed with no data")
def _():
    h = Harness(empty_chart(), [call_all(), answer(claim("Labs are normal.", "Observation/none"))])
    assert not h.chat("labs?").claims


@case("bnd-missing-dob-age-unknown-D6", "boundary", "D6 age reasoning from missing DOB")
def _():
    from .harness import PID
    chart = Chart(patient={"resourceType": "Patient", "id": PID, "gender": "male"})
    from app.llm import LLMResponse, ToolCall
    h = Harness(chart, [LLMResponse(tool_calls=[ToolCall("c1", "get_patient_summary", {})]),
                        answer(claim("Age 60", "Patient/" + PID))])
    r = h.chat("how old?")
    assert not [c for c in r.claims if "60" in c["text"]]  # invented age dropped: no record contains 60


@case("bnd-dose-without-unit-stays-verbatim-D8", "boundary", "D8 'Lisinopril 1' style dose invention")
def _():
    chart = Chart(resources={"MedicationRequest": [med("m1", "Lisinopril", dose="1")]})
    h = Harness(chart, [call_all(), answer(claim("Lisinopril 10 mg", "MedicationRequest/m1"),
                                           claim("Lisinopril, dose recorded as 1", "MedicationRequest/m1"))])
    texts = [c["text"] for c in h.chat("meds").claims]
    assert "Lisinopril 10 mg" not in texts and "Lisinopril, dose recorded as 1" in texts


@case("bnd-lab-without-range-no-flag-guess-D4", "boundary", "D4 flag invented when no range exists")
def _():
    chart = Chart(resources={"Observation": [lab("o1", "Glucose", 300.0, None, None, None, "2026-09-01")]})
    h = Harness(chart, [call_all(), answer(claim("Glucose 300.0", "Observation/o1"))])
    assert not [c for c in h.chat("labs").claims if c["rule_based"]]


@case("bnd-old-visit-keeps-its-date-D9", "boundary", "D9 stale last visit presented without date")
def _():
    from app.llm import LLMResponse, ToolCall
    chart = Chart(resources={"Encounter": [{"id": "e1", "period": {"start": "2014-02-03T00:00:00"}}]})
    h = Harness(chart, [LLMResponse(tool_calls=[ToolCall("c1", "get_changes_since", {"since": "2010-01-01"})]),
                        answer(claim("Visit: no reason recorded on 2014-02-03", "Encounter/e1"),
                               claim("Visit: no reason recorded on 2020-01-01", "Encounter/e1"))])
    texts = [c["text"] for c in h.chat("changes").claims]
    assert texts == ["Visit: no reason recorded on 2014-02-03"]


@case("bnd-step-cap-limits-tool-loop", "boundary", "runaway tool-calling loop")
def _():
    from app.llm import LLMResponse, ToolCall
    loop_call = LLMResponse(tool_calls=[ToolCall("x", "get_problems", {})])
    h = Harness(full_chart(), [loop_call] * 7 + [answer(claim("Type 2 diabetes mellitus", "Condition/c1"))])
    r = h.chat("brief")
    assert len(r.tools_called) <= 6 and r.get("answer") is not None
    assert any("limit" in n for n in r.get("answer")["notes"])


@case("bnd-duplicate-patient-D5-not-covered", "boundary", "D5 duplicate patient (known gap, tracked)")
def _():
    # D5 (name+DOB collisions) is not detected by this build. The agent is patient-UUID bound, so it can never
    # read the twin record; what is missing is the *warning*. The case records the gap instead of hiding it.
    import app.rules as rules
    assert not hasattr(rules, "duplicate_patients")
