"""Deterministic clinical rules. No model involvement: same input, same output, every time.

Each rule returns RuleFinding objects that cite real source ids. The verifier turns them into rule-based flag claims.
The allergen table is small and versioned on purpose; it does not claim to cover all cross-reactivity.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

from contracts import Allergy, LabResult, Medication, ToolEnvelope, ToolName

from .monitoring import evidence_gaps

ALLERGEN_TABLE_VERSION = "2026-10-v1"

# allergen keyword -> drug names (lowercase) that belong to the same class. Reviewed list, common classes only.
ALLERGEN_CLASSES: dict[str, frozenset[str]] = {
    "penicillin": frozenset({"penicillin", "amoxicillin", "ampicillin", "piperacillin", "dicloxacillin",
                             "augmentin", "amoxicillin-clavulanate"}),
    "sulfa": frozenset({"sulfamethoxazole", "trimethoprim-sulfamethoxazole", "bactrim", "sulfasalazine",
                        "sulfadiazine"}),
    "nsaid": frozenset({"ibuprofen", "naproxen", "diclofenac", "ketorolac", "meloxicam", "celecoxib", "aspirin"}),
    "aspirin": frozenset({"aspirin", "ibuprofen", "naproxen", "diclofenac", "ketorolac"}),
    "codeine": frozenset({"codeine", "morphine", "hydrocodone", "oxycodone", "tramadol"}),
    "cephalosporin": frozenset({"cephalexin", "cefazolin", "ceftriaxone", "cefuroxime", "cefdinir"}),
    "statin": frozenset({"atorvastatin", "simvastatin", "rosuvastatin", "pravastatin", "lovastatin"}),
    "ace inhibitor": frozenset({"lisinopril", "enalapril", "ramipril", "benazepril", "captopril"}),
}

# lab LOINC -> max age in days before a result is stale (A1c: 6 months).
STALENESS_DAYS: dict[str, int] = {"4548-4": 183, "17856-6": 183}
STALENESS_NAMES: dict[str, int] = {"a1c": 183, "hba1c": 183, "hemoglobin a1c": 183}


@dataclass(frozen=True)
class RuleFinding:
    rule: str
    text: str
    source_ids: tuple[str, ...]


def recompute_flag(lab: LabResult) -> str | None:
    """'low' | 'high' | 'normal', or None when value or range is missing (never guessed)."""
    if lab.value is None:
        return None
    if lab.ref_low is not None and lab.value < lab.ref_low:
        return "low"
    if lab.ref_high is not None and lab.value > lab.ref_high:
        return "high"
    if lab.ref_low is None and lab.ref_high is None:
        return None
    return "normal"


def _reported_kind(flag: str | None) -> str | None:
    if not flag:
        return None
    f = flag.upper()
    if f in ("L", "LL", "LOW", "<"):
        return "low"
    if f in ("H", "HH", "HIGH", ">"):
        return "high"
    if f in ("N", "NORMAL", "A"):
        return "normal" if f != "A" else None
    return None


def lab_flag_findings(labs: list[LabResult]) -> list[RuleFinding]:
    out: list[RuleFinding] = []
    for lab in labs:
        calc = recompute_flag(lab)
        if calc is None:
            continue
        reported = _reported_kind(lab.flag_reported)
        if calc != "normal" and reported != calc:
            shown = lab.flag_reported or "no flag stored"
            out.append(RuleFinding(
                "lab_flag_recompute",
                f"{lab.test} {lab.value} {lab.unit or ''} is {calc} against the recorded range "
                f"({lab.ref_low}-{lab.ref_high}); the chart flag is: {shown}.".replace("  ", " "),
                (lab.source_id,)))
    return out


def _words(s: str) -> set[str]:
    return set(re.findall(r"[a-z][a-z\-]+", s.lower()))


def allergy_conflicts(allergies: list[Allergy], meds: list[Medication]) -> list[RuleFinding]:
    out: list[RuleFinding] = []
    for a in allergies:
        if a.status and a.status.lower() not in ("active", "unconfirmed"):
            continue
        substance = (a.substance or "").lower()
        aw = _words(substance)
        for key, drugs in ALLERGEN_CLASSES.items():
            if key not in substance and not (aw & drugs):
                continue
            for m in meds:
                if m.status and m.status.lower() not in ("active", "on-hold"):
                    continue
                if _words(m.name) & drugs:
                    out.append(RuleFinding(
                        "allergy_medication_conflict",
                        f"Recorded allergy to {a.substance} and active medication {m.name} are in the same drug "
                        f"class group ({key}); review.", (a.source_id, m.source_id)))
    return out


def stale_labs(labs: list[LabResult], today: date) -> list[RuleFinding]:
    latest: dict[str, LabResult] = {}
    for lab in labs:
        limit = STALENESS_DAYS.get(lab.loinc or "") or STALENESS_NAMES.get(lab.test.lower())
        if limit is None or lab.collected is None:
            continue
        key = lab.loinc or lab.test.lower()
        if key not in latest or (latest[key].collected or date.min) < lab.collected:
            latest[key] = lab
    out: list[RuleFinding] = []
    for lab in latest.values():
        limit = STALENESS_DAYS.get(lab.loinc or "") or STALENESS_NAMES.get(lab.test.lower(), 183)
        age = (today - (lab.collected or today)).days
        if age > limit:
            out.append(RuleFinding("stale_lab", f"Most recent {lab.test} was collected {lab.collected} "
                                   f"({age} days ago), older than the {limit}-day threshold.", (lab.source_id,)))
    return out


def duplicate_medications(meds: list[Medication]) -> list[RuleFinding]:
    by_name: dict[str, list[Medication]] = {}
    for m in meds:
        if m.status and m.status.lower() not in ("active", "on-hold"):
            continue
        by_name.setdefault(m.name.strip().lower(), []).append(m)
    return [RuleFinding("duplicate_medication", f"{ms[0].name} appears {len(ms)} times among active medications.",
                        tuple(m.source_id for m in ms)) for ms in by_name.values() if len(ms) > 1]


def monitoring_gaps(envelopes: list[ToolEnvelope]) -> list[RuleFinding]:
    """An evidence gap, never a clinical requirement: it says only that no such result is in the retrieved lab results."""
    return [RuleFinding("monitoring_evidence_gap",
                        f"No {label} result is in the retrieved lab results for the active medication"
                        f"{'s' if ' and ' in drugs or ', ' in drugs else ''} {drugs}.", ids)
            for drugs, label, ids in evidence_gaps(envelopes)]


def run_rules(envelopes: list[ToolEnvelope], today: date) -> list[RuleFinding]:
    by_tool = {e.tool: e.records for e in envelopes}
    labs = by_tool.get(ToolName.GET_LABS, [])
    meds = by_tool.get(ToolName.GET_MEDICATIONS, [])
    allergies = by_tool.get(ToolName.GET_ALLERGIES, [])
    return [*lab_flag_findings(labs), *stale_labs(labs, today), *duplicate_medications(meds),
            *allergy_conflicts(allergies, meds), *monitoring_gaps(envelopes)]


# Output rules: the answer may not diagnose or direct treatment. Matching is conservative and deliberately broad.
_FORBIDDEN = [
    re.compile(r"\b(diagnos(is|ed|e)|likely has|consistent with|suggests? (a )?diagnosis of|differential)\b", re.I),
    re.compile(r"\b(start(?:ing)?|initiat(?:e|ing)|begin(?:ning)?|stop(?:ping)?|discontinu(?:e|ing)|hold(?:ing)?|increas(?:e|ing)|"
               r"decreas(?:e|ing)|titrat(?:e|ing)|switch(?:ing)? to|prescrib(?:e|ing))\b"
               r"[^.]{0,60}\b(\w+(in|ol|il|ide|one|pril|sartan|statin|mab|cillin|formin)|mg|dose|medication|drug)\b", re.I),
    re.compile(r"\b(should|recommend|advise|consider)\b[^.]{0,40}\b(take|taking|dose|dosage|mg|treat|treatment)\b", re.I),
    re.compile(r"\b\d+(\.\d+)?\s?(mg|mcg|units?|ml)\b[^.]{0,20}\b(daily|bid|tid|qd|once|twice|per day)\b", re.I),
]


def violates_output_rules(text: str, allowed_verbatim: tuple[str, ...] = ()) -> bool:
    """True when the text diagnoses or directs treatment. Verbatim chart text (e.g. a recorded dose) is exempt."""
    scrubbed = text
    for chunk in allowed_verbatim:
        if chunk:
            scrubbed = scrubbed.replace(chunk, " ")
    return any(p.search(scrubbed) for p in _FORBIDDEN)
