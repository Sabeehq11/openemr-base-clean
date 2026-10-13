"""Evidence gaps for current medications: a reviewed drug-to-lab table, used only to say what is NOT in the records.

It never asserts that a result is clinically required, never states an interaction, and never says anything about a
dose. For each active medication in the table it asks one factual question of records already retrieved: is there a
lab result of the listed kind in the retrieved lab results? If not, it says so, citing the medication.

Fail closed. A gap is reported only when BOTH the medication tool and the lab tool succeeded. A lab tool that was never
called, failed or was denied produces no statement, so a missing read is never mistaken for a missing result. The table
is small, versioned and conservative; like the allergen table in rules.py it needs clinician review before wider use.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from contracts import LabResult, Medication, ToolEnvelope, ToolName

MONITORING_TABLE_VERSION = "2026-10-v1"


@dataclass(frozen=True)
class LabKind:
    label: str               # how the gap line names the missing result
    loinc: frozenset[str]    # matched on the LOINC code when the chart has one
    names: tuple[str, ...]   # otherwise matched as whole words in the lab's own test name


INR = LabKind("INR", frozenset({"6301-6", "34714-6", "5902-2"}), ("inr", "prothrombin"))
RENAL = LabKind("kidney function (creatinine or eGFR)",
                frozenset({"2160-0", "38483-4", "33914-3", "62238-1", "48642-3", "48643-1", "98979-8", "77147-7"}),
                ("creatinine", "egfr", "gfr"))
POTASSIUM = LabKind("potassium", frozenset({"2823-3", "6298-4"}), ("potassium",))
LIPIDS = LabKind("a lipid result (LDL, cholesterol or a lipid panel)",
                 frozenset({"13457-7", "18262-6", "2089-1", "2093-3", "57698-3"}), ("ldl", "cholesterol", "lipid"))

# Reviewed list: drug name (a whole word in the medication's name) -> lab kinds checked. A drug not listed is not checked.
DRUG_LABS: dict[str, tuple[LabKind, ...]] = {
    "warfarin": (INR,),
    "metformin": (RENAL,),
    "lisinopril": (POTASSIUM, RENAL),
    "enalapril": (POTASSIUM, RENAL),
    "ramipril": (POTASSIUM, RENAL),
    "atorvastatin": (LIPIDS,),
    "simvastatin": (LIPIDS,),
    "rosuvastatin": (LIPIDS,),
    "pravastatin": (LIPIDS,),
}
_ACTIVE = {"active", "on-hold"}


def _words(s: str) -> set[str]:
    return set(re.findall(r"[a-z][a-z\-]+", s.lower()))


def has_lab(kind: LabKind, labs: list[LabResult]) -> bool:
    return any(lab.loinc in kind.loinc or _words(lab.test) & set(kind.names) for lab in labs)


def evidence_gaps(envelopes: list[ToolEnvelope]) -> list[tuple[str, str, tuple[str, ...]]]:
    """(drug names, missing lab label, medication source ids): one entry per missing lab kind, drugs grouped together."""
    by_tool = {e.tool: e for e in envelopes}
    med_env, lab_env = by_tool.get(ToolName.GET_MEDICATIONS), by_tool.get(ToolName.GET_LABS)
    if med_env is None or lab_env is None:  # not called, failed or denied: no statement at all
        return []
    labs = [r for r in lab_env.records if isinstance(r, LabResult)]
    missing: dict[str, list[Medication]] = {}
    for m in med_env.records:
        if not isinstance(m, Medication) or (m.status and m.status.lower() not in _ACTIVE):
            continue
        for drug in sorted(_words(m.name) & DRUG_LABS.keys()):
            for kind in DRUG_LABS[drug]:
                if not has_lab(kind, labs) and m not in missing.setdefault(kind.label, []):
                    missing[kind.label].append(m)
    return [(_join(sorted({m.name.strip() for m in meds})), label, tuple(m.source_id for m in meds))
            for label, meds in missing.items() if meds]


def _join(names: list[str]) -> str:
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]
