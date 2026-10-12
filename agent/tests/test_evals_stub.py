"""Runs every stub-tier eval case under pytest, one test per case, plus coverage checks on the suite itself."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from evals.registry import CATEGORIES, load_all  # noqa: E402

CASES = [c for c in load_all() if c.tier == "stub"]


@pytest.mark.parametrize("c", CASES, ids=[c.id for c in CASES])
def test_eval_case(c):
    c.fn()


def test_every_category_has_cases():
    assert {c.category for c in CASES} == set(CATEGORIES)


def test_every_audit_defect_d1_to_d11_has_a_case():
    text = " ".join(c.guards for c in load_all())
    missing = [f"D{i}" for i in range(1, 12) if f"D{i} " not in text + " " and f"D{i}" not in text]
    # D10 (vitals) and D11 (uuid registry) are outside the six tools' scope in this build; they must be named below.
    assert set(missing) <= {"D10", "D11"}, missing
