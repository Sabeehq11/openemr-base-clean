"""Reviewed LOINC codes for extracted lab rows. A code the document does not support is never stored.

The model proposes a code; this table decides whether to keep it. A code survives only when it is in the table, the
printed test name fits that code, and, for codes that name a method, the page says that method. Nothing is ever
substituted: an unsupported code becomes None, and the row keeps its name, value and citation.

Every code and its meaning was checked against the NLM Clinical Tables LOINC service (clinicaltables.nlm.nih.gov,
2026-10-14). The LDL entries show why: three different tests share one printed name.
"""
from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class Reviewed:
    meaning: str                    # the LOINC long common name, for people reading this table
    name: re.Pattern[str]           # the printed test name must match
    avoid: re.Pattern[str] | None   # ...and must not match this (a different specimen or test)
    method: re.Pattern[str] | None  # when set, the row's own text must state this method


def _p(pattern: str) -> re.Pattern[str]:
    return re.compile(pattern, re.I)


_NOT_SERUM = _p(r"\b(urine|urinary|ratio|clearance|egfr|gfr)\b")

REVIEWED: dict[str, Reviewed] = {
    "4548-4": Reviewed("Hemoglobin A1c/Hemoglobin.total in Blood", _p(r"\b(hemoglobin a1c|hba1c|a1c|glycated h)"),
                       None, None),
    "2160-0": Reviewed("Creatinine [Mass/volume] in Serum or Plasma", _p(r"\bcreatinine\b"), _NOT_SERUM, None),
    "2345-7": Reviewed("Glucose [Mass/volume] in Serum or Plasma", _p(r"\bglucose\b"),
                       _p(r"\b(urine|urinary|fasting|random|tolerance|post|csf)\b"), None),
    "2823-3": Reviewed("Potassium [Moles/volume] in Serum or Plasma", _p(r"\bpotassium\b"), _p(r"\b(urine|urinary)\b"),
                       None),
    "2089-1": Reviewed("Cholesterol in LDL [Mass/volume] in Serum or Plasma", _p(r"\bldl\b"), None, None),
    "13457-7": Reviewed("Cholesterol in LDL [Mass/volume] in Serum or Plasma by calculation", _p(r"\bldl\b"), None,
                        _p(r"\bcalc")),
    "18262-6": Reviewed("Cholesterol in LDL [Mass/volume] in Serum or Plasma by Direct assay", _p(r"\bldl\b"), None,
                        _p(r"\bdirect\b")),
}

_SHAPE = re.compile(r"^\d{1,7}-\d$")


def supported_loinc(code: str | None, test_name: str, quote: str) -> str | None:
    """The code when the document supports it; otherwise None."""
    if not code or not _SHAPE.match(code):
        return None
    entry = REVIEWED.get(code)
    if entry is None:
        return None
    if not entry.name.search(test_name) or (entry.avoid and entry.avoid.search(test_name)):
        return None
    if entry.method and not entry.method.search(f"{test_name} {quote}"):
        return None
    return code
