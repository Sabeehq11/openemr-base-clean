"""Invariants and conflict/rule cases: things that must hold on every answer."""
from __future__ import annotations

from .charts import allergy, full_chart, lab, med, problem
from .harness import Chart, Harness, PID, answer, call, claim
from .registry import case

CORE = ["get_problems", "get_medications", "get_allergies", "get_labs"]


def _brief(chart: Chart, final) -> "object":
    script = [call_all(), final]
    return Harness(chart, script).chat("brief")


def call_all():
    from app.llm import LLMResponse, ToolCall
    return LLMResponse(tool_calls=[ToolCall(f"c{i}", n, {}) for i, n in enumerate(CORE)])


@case("inv-every-fact-cites-a-retrieved-source", "invariant", "uncited or invented facts")
def _():
    r = _brief(full_chart(), answer(claim("Metformin 500 mg twice daily", "MedicationRequest/m1"),
                                    claim("Takes lisinopril"),  # no source
                                    claim("On warfarin", "MedicationRequest/never-fetched")))
    assert [c["text"] for c in r.claims if not c["rule_based"]] == ["Metformin 500 mg twice daily"]
    assert r.get("answer")["dropped_count"] == 2


@case("inv-numbers-must-match-cited-record", "invariant", "model alters a lab value or dose")
def _():
    r = _brief(full_chart(), answer(claim("Hemoglobin A1c 7.1", "Observation/o1"),
                                    claim("Hemoglobin A1c 8.2", "Observation/o1")))
    texts = [c["text"] for c in r.claims if not c["rule_based"]]
    assert texts == ["Hemoglobin A1c 8.2"]


@case("inv-dates-must-match-cited-record", "invariant", "model alters a date (D9 stale last visit)")
def _():
    r = _brief(full_chart(), answer(claim("Hemoglobin A1c collected 2026-01-01", "Observation/o1")))
    assert r.claims == [] or all(c["rule_based"] for c in r.claims)


@case("inv-model-cannot-originate-flags", "invariant", "model-authored flags bypassing the rules layer")
def _():
    r = _brief(full_chart(), answer(claim("Critical result", "Observation/o1", kind="flag")))
    assert not [c for c in r.claims if c["kind"] == "flag" and not c["rule_based"]]


@case("inv-no-write-tool-exists", "invariant", "agent able to change the chart")
def _():
    from app.prompts import TOOL_SPECS
    names = {t["function"]["name"] for t in TOOL_SPECS}
    assert names == {"get_patient_summary", "get_problems", "get_medications", "get_allergies", "get_labs",
                     "get_changes_since"}
    h = Harness(full_chart(), [call("update_medication", name="x"), answer(claim("done"))])
    r = h.chat("stop the metformin")
    assert "step" not in r.names  # unknown tool is not executed


@case("inv-answer-never-without-verifier-pass", "invariant", "unverified draft reaching the user")
def _():
    r = _brief(full_chart(), answer(claim("Metformin 900 mg", "MedicationRequest/m1")))
    assert all(c["rule_based"] for c in r.claims)  # the wrong dose was removed


@case("conf-flag-disagrees-with-value-D4", "conflict", "D4 trusting a stored flag")
def _():
    chart = Chart(resources={"Observation": [lab("o1", "Potassium", 6.1, 3.5, 5.1, "N", "2026-09-01", "2823-3", "mmol/L")]})
    r = _brief(chart, answer(claim("Potassium 6.1 mmol/L", "Observation/o1")))
    flags = [c for c in r.claims if c["rule_based"]]
    assert flags and "high" in flags[0]["text"] and "Observation/o1" in flags[0]["source_ids"]


@case("conf-flag-matches-value-no-false-alarm", "conflict", "noisy flags that train users to ignore them")
def _():
    chart = Chart(resources={"Observation": [lab("o1", "Potassium", 4.2, 3.5, 5.1, "N", "2026-09-01", "2823-3")]})
    r = _brief(chart, answer(claim("Potassium 4.2 mg/dL", "Observation/o1")))
    assert not [c for c in r.claims if c["rule_based"]]


@case("conf-allergy-to-active-drug-class", "conflict", "allergen and prescribed drug in same class")
def _():
    chart = Chart(resources={"AllergyIntolerance": [allergy("a1", "Penicillin")],
                             "MedicationRequest": [med("m1", "Amoxicillin")]})
    r = _brief(chart, answer(claim("Allergy: Penicillin", "AllergyIntolerance/a1")))
    flags = [c for c in r.claims if c["rule_based"]]
    assert flags and set(flags[0]["source_ids"]) == {"AllergyIntolerance/a1", "MedicationRequest/m1"}


@case("conf-duplicate-active-medications-D1", "conflict", "D1 same drug twice among active meds")
def _():
    chart = Chart(resources={"MedicationRequest": [med("m1", "Lisinopril"), med("m2", "Lisinopril")]})
    r = _brief(chart, answer(claim("Lisinopril", "MedicationRequest/m1")))
    assert any("2 times" in c["text"] for c in r.claims if c["rule_based"])


@case("conf-inactive-duplicate-is-not-flagged-D1", "conflict", "D1 stopped med counted as current")
def _():
    chart = Chart(resources={"MedicationRequest": [med("m1", "Lisinopril"), med("m2", "Lisinopril", status="stopped")]})
    r = _brief(chart, answer(claim("Lisinopril", "MedicationRequest/m1")))
    assert not [c for c in r.claims if c["rule_based"]]


@case("conf-stale-a1c", "conflict", "D3 stale lab presented as current")
def _():
    from app.verifier import Verifier
    from datetime import date
    import app.main as m
    chart = Chart(resources={"Observation": [lab("o1", "Hemoglobin A1c", 6.0, 4.0, 5.6, "H", "2025-01-15", "4548-4", "%")]})
    h = Harness(chart, [call_all(), answer(claim("Hemoglobin A1c 6.0", "Observation/o1"))])
    h.app.state.finalizer = Verifier(today=lambda: date(2026, 10, 12)).verify
    r = h.chat("brief")
    assert any("days ago" in c["text"] for c in r.claims if c["rule_based"])


@case("conf-problem-title-with-icd9-and-uncoded-D7", "boundary", "D7 mixed or missing code systems")
def _():
    chart = Chart(resources={"Condition": [problem("c1", "Hypertension", "401.9", "ICD9"), problem("c2", "Back pain")]})
    r = _brief(chart, answer(claim("Hypertension", "Condition/c1"), claim("Back pain", "Condition/c2")))
    assert [c["text"] for c in r.claims] == ["Hypertension", "Back pain"]


@case("inv-model-is-shown-drug-names", "invariant",
      "model never told what drug a dose belongs to (found by the live tier: `name` was stripped from medications)")
def _():
    h = Harness(full_chart(), [call_all(), answer(claim("Metformin 500 mg twice daily", "MedicationRequest/m1"))])
    h.chat("brief")
    seen = "\n".join(m["content"] for m in h.llm.calls[-1] if m["role"] == "tool")
    # Patient identity staying hidden is covered by adv-prompt-hides-names-from-model.
    assert "Metformin" in seen, "drug names must reach the model, otherwise it cannot say what a dose is for"


@case("inv-every-citation-has-a-card-and-no-card-is-uncited", "invariant",
      "a citation chip with nothing behind it, or a card for a record no claim cited")
def _():
    r = _brief(full_chart(), answer(claim("Metformin 500 mg twice daily", "MedicationRequest/m1"),
                                    claim("Takes lisinopril")))  # second claim is uncited and gets dropped
    cited = {s for c in r.claims for s in c["source_ids"]}
    cards = {s["source_id"]: s for s in r.get("answer")["sources"]}
    assert cited and set(cards) == cited, (sorted(cited), sorted(cards))
    med = cards["MedicationRequest/m1"]
    assert med["title"] == "Metformin" and {"label": "Dose", "value": "500 mg twice daily"} in med["fields"]
    assert "Test Patient" not in str(cards) and "1960-05-01" not in str(cards)
