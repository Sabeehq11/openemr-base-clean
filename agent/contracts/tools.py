"""Tool contracts: one input model and one output type per tool. These are the source of truth.

`app/prompts.py:TOOL_SPECS` is generated from the input models here, the runner validates every call against them, and
every result is checked against the matching output type. Nothing about a tool's arguments is hand-parsed.
"""
from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from .records import (
    Allergy,
    ChangeItem,
    LabResult,
    Medication,
    PatientSummary,
    Problem,
    ToolEnvelope,
    ToolError,
    ToolName,
)


class NoArgs(BaseModel):
    """Tools that read the open patient take no arguments. The patient is never a model-supplied value."""

    model_config = ConfigDict(frozen=True, extra="forbid")


class ChangesSinceInput(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    since: date = Field(description="ISO date YYYY-MM-DD")

    @field_validator("since", mode="before")
    @classmethod
    def _iso_date_only(cls, v: object) -> date:
        # A date or an ISO date string only. Pydantic's default would also read an integer as a Unix timestamp.
        if isinstance(v, datetime):
            raise ValueError("since must be a date")
        if isinstance(v, date):
            return v
        if isinstance(v, str):
            return date.fromisoformat(v)
        raise ValueError("since must be an ISO date string")


TOOL_INPUTS: dict[ToolName, type[BaseModel]] = {
    ToolName.GET_PATIENT_SUMMARY: NoArgs,
    ToolName.GET_PROBLEMS: NoArgs,
    ToolName.GET_MEDICATIONS: NoArgs,
    ToolName.GET_ALLERGIES: NoArgs,
    ToolName.GET_LABS: NoArgs,
    ToolName.GET_CHANGES_SINCE: ChangesSinceInput,
}

TOOL_OUTPUTS: dict[ToolName, type[BaseModel]] = {
    ToolName.GET_PATIENT_SUMMARY: PatientSummary,
    ToolName.GET_PROBLEMS: Problem,
    ToolName.GET_MEDICATIONS: Medication,
    ToolName.GET_ALLERGIES: Allergy,
    ToolName.GET_LABS: LabResult,
    ToolName.GET_CHANGES_SINCE: ChangeItem,
}

assert set(TOOL_INPUTS) == set(TOOL_OUTPUTS) == set(ToolName), "every tool needs an input and an output contract"


def validate_input(tool: ToolName, args: object) -> BaseModel:
    """Parse a tool call's arguments into the tool's input model. Unknown or extra arguments are rejected."""
    if not isinstance(args, dict):
        raise ToolError("invalid_input", "invalid arguments: arguments")
    try:
        return TOOL_INPUTS[tool].model_validate(args)
    except ValidationError as e:
        problems = sorted({".".join(str(p) for p in err["loc"]) or "arguments" for err in e.errors()})
        raise ToolError("invalid_input", f"invalid arguments: {', '.join(problems)}") from None


def input_schema(tool: ToolName) -> dict:
    """JSON schema offered to the model, derived from the input contract."""
    schema = TOOL_INPUTS[tool].model_json_schema()
    schema.pop("title", None)
    schema.pop("description", None)  # the class docstring is for developers; the tool has its own description
    for prop in schema.get("properties", {}).values():
        prop.pop("title", None)
    schema["additionalProperties"] = False
    return schema


def check_output(tool: ToolName, env: ToolEnvelope) -> ToolEnvelope:
    """Every record in an envelope must be the tool's declared output type, and the id lists must agree."""
    expected = TOOL_OUTPUTS[tool]
    if env.tool is not tool or any(not isinstance(r, expected) for r in env.records):
        raise ToolError("bad_response", f"{tool.value} produced an unexpected record type")
    if env.empty != (not env.records) or env.source_ids != [r.source_id for r in env.records]:
        raise ToolError("bad_response", f"{tool.value} produced an inconsistent envelope")
    return env
