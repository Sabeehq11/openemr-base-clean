"""Deterministic statements about which sources were checked. Written by the server from tool outcomes, never by the model.

  * a tool that succeeded and returned nothing  -> "No X documented in the retrieved records."
  * a tool that succeeded and returned records  -> listed under "Sources checked", with the record count
  * a tool that failed or timed out             -> "X could not be retrieved", and nothing is concluded about it
  * a tool that was never called                -> no statement at all

A successful read of one source says nothing about another: a lab list with no INR is reported as "labs (4)", never as
"no INR documented".
"""
from __future__ import annotations

from contracts import ToolEnvelope, ToolName

_LABEL = {
    ToolName.GET_PATIENT_SUMMARY: "patient summary", ToolName.GET_PROBLEMS: "problems",
    ToolName.GET_MEDICATIONS: "medications", ToolName.GET_ALLERGIES: "allergies", ToolName.GET_LABS: "lab results",
    ToolName.GET_CHANGES_SINCE: "changes since the requested date",
}
_ORDER = list(_LABEL)


def coverage_notes(envelopes: list[ToolEnvelope], failed: frozenset[ToolName] | set[ToolName] = frozenset()) -> list[str]:
    ok: dict[ToolName, ToolEnvelope] = {}
    for e in envelopes:
        ok[e.tool] = e  # a failure this turn wins below, so a stale success is never shown for a failed refresh
    notes: list[str] = []
    checked: list[str] = []
    for tool in _ORDER:
        if tool in failed:
            notes.append(f"The {_LABEL[tool]} could not be retrieved, so nothing is concluded about them.")
        elif tool in ok and tool is not ToolName.GET_PATIENT_SUMMARY:
            e = ok[tool]
            if e.empty:
                notes.append(f"No {_LABEL[tool]} documented in the retrieved records.")
            else:
                checked.append(f"{_LABEL[tool]} ({len(e.records)})")
    if checked:
        notes.append("Sources checked: " + ", ".join(checked) + ".")
    return notes
