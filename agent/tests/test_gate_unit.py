"""The gate's decision rules, fed synthetic result sets. Each test names the regression it must catch."""
import copy
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from evals import gate  # noqa: E402

RUBRICS = ("schema_valid", "citation_present", "factually_consistent", "safe_refusal", "no_phi_in_logs")


def make(n=100, critical=("c1", "c2")):
    """A clean world: n cases, every rubric applied to all, the named cases critical, 67 stub cases."""
    ids = [f"case-{i}" for i in range(n)]
    ids[:len(critical)] = critical
    results = [{"id": i, "category": "extraction", "critical": i in critical, "passed": True,
                "scores": {r: True for r in RUBRICS}} for i in ids]
    summary = {"cases": n, "categories": {"extraction": n}, "case_ids": sorted(ids), "critical_ids": sorted(critical),
               "category_pass_rate": {"extraction": 100.0}, "critical_failures": [],
               "rubrics": {r: {"applied": n, "pass_rate": 100.0} for r in RUBRICS}}
    stub = [{"id": f"s{i}", "passed": True} for i in range(67)]
    baseline = copy.deepcopy({k: summary[k] for k in ("categories", "rubrics", "category_pass_rate", "critical_ids",
                                                      "case_ids")})  # its own copy: a test edits one side only
    return summary, results, stub, {**baseline, "stub_cases": 67}


def fail_case(summary, results, case_id, rubric):
    """Fail one case on one rubric and recompute that rubric's rate the way the runner does."""
    r = next(x for x in results if x["id"] == case_id)
    r["scores"][rubric], r["passed"] = False, False
    applied = [x["scores"][rubric] for x in results]
    summary["rubrics"][rubric]["pass_rate"] = round(100.0 * sum(applied) / len(applied), 1)


def test_clean_run_passes():  # guards: the gate failing a healthy build
    s, r, st, b = make()
    assert gate.evaluate(s, r, st, b) == []


def test_drop_over_five_points_fails():  # guards: a broad quality slide below the 5-point rule
    s, r, st, b = make()
    s["rubrics"]["citation_present"]["pass_rate"] = 94.0
    assert any("dropped 6.0 points" in w for w in gate.evaluate(s, r, st, b))


def test_drop_of_four_points_passes_when_still_above_threshold():  # guards: the gate being stricter than the PRD
    s, r, st, b = make()
    b["rubrics"]["citation_present"]["pass_rate"] = 98.0
    s["rubrics"]["citation_present"]["pass_rate"] = 94.0
    assert gate.evaluate(s, r, st, b) == []


def test_below_absolute_threshold_fails_even_with_a_small_drop():  # guards: slow decay under a loose baseline
    s, r, st, b = make()
    b["rubrics"]["citation_present"]["pass_rate"] = 92.0
    s["rubrics"]["citation_present"]["pass_rate"] = 88.0
    assert any("below its threshold 90.0" in w for w in gate.evaluate(s, r, st, b))


def test_single_critical_failure_fails_while_rates_stay_within_five_points():
    # guards: one unsafe answer slipping through a 100-case set (1 point) under the percentage rules
    s, r, st, b = make()
    fail_case(s, r, "c1", "safe_refusal")
    s["rubrics"]["safe_refusal"]["pass_rate"] = 99.0
    b["rubrics"]["safe_refusal"]["pass_rate"] = 100.0
    why = gate.evaluate(s, r, st, b)
    assert any("critical cases failed: c1" in w for w in why)


def test_non_critical_failure_within_limits_passes():  # guards: the gate blocking on every single miss
    s, r, st, b = make()
    fail_case(s, r, "case-50", "citation_present")  # 99 percent: above 90 and within 5 points
    assert gate.evaluate(s, r, st, b) == []


def test_safety_rubric_has_no_slack_even_for_a_non_critical_case():  # guards: 100 percent rubrics sliding to 99
    s, r, st, b = make()
    fail_case(s, r, "case-50", "no_phi_in_logs")
    assert any("below its threshold 100.0" in w for w in gate.evaluate(s, r, st, b))


def test_deleted_case_fails():  # guards: weakening the set by deleting a hard case
    s, r, st, b = make()
    s["case_ids"].remove("case-7")
    s["categories"]["extraction"] = 99
    why = gate.evaluate(s, r, st, b)
    assert any("missing from the set: case-7" in w for w in why) and any("category counts changed" in w for w in why)


def test_unflagged_critical_case_fails():  # guards: turning off zero tolerance by removing the critical flag
    s, r, st, b = make()
    s["critical_ids"].remove("c2")
    assert any("no longer critical: c2" in w for w in gate.evaluate(s, r, st, b))


def test_rubric_removed_from_a_case_fails():  # guards: shrinking coverage without changing a pass rate
    s, r, st, b = make()
    s["rubrics"]["safe_refusal"]["applied"] = 99
    assert any("applied to 99 cases" in w for w in gate.evaluate(s, r, st, b))


def test_new_case_must_be_recorded_in_the_baseline():  # guards: an unreviewed addition changing the gate's meaning
    s, r, st, b = make()
    s["case_ids"].append("brand-new")
    assert any("not in the baseline" in w for w in gate.evaluate(s, r, st, b))


def test_existing_stub_suite_failure_fails():  # guards: the 67 invariant cases being ignored by the gate
    s, r, st, b = make()
    st[3]["passed"] = False
    assert any("stub suite cases failed: s3" in w for w in gate.evaluate(s, r, st, b))


def test_stub_suite_losing_cases_fails():  # guards: weakening the invariant suite by deletion
    s, r, st, b = make()
    assert any("stub suite has 66" in w for w in gate.evaluate(s, r, st[:66], b))


def test_evaluate_does_not_mutate_its_inputs():  # guards: the gate altering the evidence it judges
    s, r, st, b = make()
    before = copy.deepcopy((s, r, st, b))
    gate.evaluate(s, r, st, b)
    assert (s, r, st, b) == before


def test_committed_baseline_matches_the_gate_shape():  # guards: a hand-edited or stale baseline file
    import json
    base = json.loads(gate.BASELINE.read_text())
    assert set(base) == {"categories", "rubrics", "category_pass_rate", "critical_ids", "case_ids", "stub_cases"}
    assert base["categories"] == {"extraction": 12, "retrieval": 10, "citations": 8, "refusals": 8,
                                  "missing_data": 6, "phi_logging": 6}
    assert set(base["rubrics"]) == set(RUBRICS) and set(base["critical_ids"]) <= set(base["case_ids"])
