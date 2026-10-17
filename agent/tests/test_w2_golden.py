"""Runs the W2 golden set under pytest: one test per case, plus integrity checks on the set itself and a check that
the PHI rubric really fails when a value is logged (a rubric that cannot fail guards nothing)."""
import logging
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from evals import w2_golden as g  # noqa: E402
from evals.w2_rubrics import score  # noqa: E402

CASES = g.load_cases()
FULL = True  # all 50 cases exist: the exact 12/10/8/8/6/6 split and rubric coverage are enforced


@pytest.mark.parametrize("c", CASES, ids=[c["id"] for c in CASES])
def test_golden_case(c):  # guards: whatever the case's own `guards` field names
    scores = score(c, g.run_case(c))
    assert all(scores.values()), {k: v for k, v in scores.items() if not v}


def test_set_integrity():  # guards: the set losing cases, categories, rubric coverage or critical flags
    assert g.check_integrity(CASES, full=FULL) == []


def test_phi_rubric_fails_when_a_value_is_logged(monkeypatch):  # guards: a no_phi_in_logs rubric that cannot fail
    from app.w2 import ingest
    case = next(c for c in CASES if c["id"] == "phi-extract-and-answer-path")
    real = ingest.log_event

    def leaky(logger, msg, **fields):
        real(logger, msg, leaked="Penicillin (rash)", **fields)

    monkeypatch.setattr(ingest, "log_event", leaky)
    assert score(case, g.run_case(case))["no_phi_in_logs"] is False
