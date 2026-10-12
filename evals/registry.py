"""Case registry. A case is a function that raises AssertionError on failure and carries the failure mode it guards."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

CATEGORIES = ("invariant", "boundary", "conflict", "authorization", "adversarial", "failure_injection")


@dataclass(frozen=True)
class Case:
    id: str
    category: str
    guards: str  # the failure mode this case protects against (audit id such as D4, or a named risk)
    fn: Callable[[], None]
    tier: str = "stub"  # stub = deterministic, runs in pytest; live = needs the real model, on demand


CASES: list[Case] = []


def case(id: str, category: str, guards: str, tier: str = "stub"):
    assert category in CATEGORIES, category

    def deco(fn: Callable[[], None]) -> Callable[[], None]:
        assert all(c.id != id for c in CASES), f"duplicate case id {id}"
        CASES.append(Case(id, category, guards, fn, tier))
        return fn

    return deco


def load_all() -> list[Case]:
    from . import cases_adversarial, cases_authz, cases_boundary, cases_failure, cases_invariants  # noqa: F401
    return list(CASES)
