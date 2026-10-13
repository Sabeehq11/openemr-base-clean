"""Merging a new live run into live-latest.json must never alter the earlier results.

Guards against: a re-run erasing the six baseline results, or relabelling old cases and their spend as new.
"""
import copy
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from evals.merge import merge, render_md, write  # noqa: E402
from evals.registry import CATEGORIES  # noqa: E402

BASELINE = Path(__file__).resolve().parents[2] / "evals" / "results" / "live-baseline-2026-10-12.json"


def case(i, ok=True, **kw):
    return {"id": i, "category": "invariant", "guards": "g", "passed": ok, "error": None if ok else "boom",
            "ms": 1.5, **kw}


OLD = {"tier": "live", "generated": "2026-10-12 20:52:58 UTC", "passed": 2, "total": 2, "model": "m",
       "spend_usd": 0.05, "spend_cap_usd": 1.0, "cases": [case("a"), case("b")]}
RUN = {"run": "2026-10-13", "generated": "2026-10-13 18:00:00 UTC", "model": "m", "spend_usd": 0.07,
       "spend_cap_usd": 0.4}


def test_old_cases_are_kept_byte_for_byte_and_new_ones_appended():
    before = copy.deepcopy(OLD["cases"])
    doc = merge(OLD, [case("c"), case("d", no_model_call=True)], RUN)
    assert doc["cases"][:2] == before
    assert [c["id"] for c in doc["cases"]] == ["a", "b", "c", "d"]
    assert OLD["cases"] == before  # the input was not mutated either


def test_a_failed_new_case_does_not_change_the_old_results():
    doc = merge(OLD, [case("c", ok=False)], RUN)
    assert doc["cases"][:2] == OLD["cases"] and doc["passed"] == 2 and doc["total"] == 3


def test_runs_keep_their_own_spend_and_cases_and_the_total_adds_up():
    doc = merge(OLD, [case("c")], RUN)
    first, second = doc["runs"]
    assert first["case_ids"] == ["a", "b"] and first["spend_usd"] == 0.05 and first["run"] == "2026-10-12"
    assert second["case_ids"] == ["c"] and second["spend_usd"] == 0.07
    assert doc["spend_usd"] == pytest.approx(0.12)


def test_same_day_runs_get_distinct_labels():
    once = merge(OLD, [case("c")], {**RUN, "run": "2026-10-12"})
    assert [r["run"] for r in once["runs"]] == ["2026-10-12", "2026-10-12#2"]


def test_existing_case_id_is_refused_unless_rerun():
    with pytest.raises(ValueError, match="--rerun"):
        merge(OLD, [case("a")], RUN)
    doc = merge(OLD, [case("a", ms=9.9)], RUN, rerun=True)
    assert [c["id"] for c in doc["cases"]] == ["a", "b"] and doc["cases"][0]["ms"] == 9.9
    assert doc["cases"][1] == OLD["cases"][1]
    assert doc["runs"][0]["case_ids"] == ["b"] and doc["runs"][1]["case_ids"] == ["a"]


def test_markdown_labels_runs_and_marks_cases_without_a_model_call():
    md = render_md(merge(OLD, [case("c", no_model_call=True)], RUN), CATEGORIES)
    assert "Cumulative model spend **$0.1200** over 2 runs" in md
    assert "| a | 2026-10-12 |" in md and "| c | 2026-10-13 |" in md
    assert "| no model call |" in md


def test_write_round_trips_on_disk(tmp_path):
    (tmp_path / "live-latest.json").write_text(json.dumps(OLD))
    write(tmp_path, [case("c")], RUN, CATEGORIES)
    doc = json.loads((tmp_path / "live-latest.json").read_text())
    assert doc["cases"][:2] == OLD["cases"] and doc["total"] == 3
    assert (tmp_path / "live-latest.md").read_text().startswith("# Eval results: live tier")


def test_the_real_baseline_survives_a_merge_unchanged():
    base = json.loads(BASELINE.read_text())
    assert base["total"] == 6 and base["spend_usd"] == 0.0537  # the saved first run
    doc = merge(base, [case("new-1"), case("new-2", ok=False)], RUN)
    assert doc["cases"][:6] == base["cases"]
    assert doc["runs"][0]["spend_usd"] == 0.0537 and doc["runs"][0]["run"] == "2026-10-12"
    assert len(doc["runs"][0]["case_ids"]) == 6
