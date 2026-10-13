"""Tool input and output contracts are the single source of truth for what the model may ask for.

Guards against: a hand-written tool schema drifting from what the runner enforces, extra or mistyped arguments
reaching a tool, and a tool returning records of the wrong type.
"""
import pytest

from app.prompts import TOOL_SPECS
from contracts import (
    TOOL_INPUTS,
    TOOL_OUTPUTS,
    ChangesSinceInput,
    NoArgs,
    ToolEnvelope,
    ToolError,
    ToolName,
    check_output,
    input_schema,
    validate_input,
)
from contracts.records import Allergy, Problem

PID = "11111111-2222-3333-4444-555555555555"

NO_ARG_SCHEMA = {"type": "object", "properties": {}, "additionalProperties": False}
CHANGES_SCHEMA = {
    "type": "object",
    "properties": {"since": {"type": "string", "format": "date", "description": "ISO date YYYY-MM-DD"}},
    "required": ["since"],
    "additionalProperties": False,
}


def test_every_tool_has_an_input_and_an_output_contract():
    assert set(TOOL_INPUTS) == set(TOOL_OUTPUTS) == set(ToolName)


def test_generated_schema_snapshot():
    """Changing a tool's arguments changes this test on purpose: the model-facing contract must be reviewed."""
    for t in ToolName:
        assert input_schema(t) == (CHANGES_SCHEMA if t is ToolName.GET_CHANGES_SINCE else NO_ARG_SCHEMA), t


def test_tool_specs_are_generated_from_the_input_models():
    assert [s["function"]["name"] for s in TOOL_SPECS] == [t.value for t in ToolName]
    for spec, t in zip(TOOL_SPECS, ToolName):
        assert spec["function"]["parameters"] == input_schema(t)
        assert set(spec["function"]["parameters"].get("properties", {})) == set(TOOL_INPUTS[t].model_fields)


def test_valid_inputs_parse():
    assert isinstance(validate_input(ToolName.GET_PROBLEMS, {}), NoArgs)
    parsed = validate_input(ToolName.GET_CHANGES_SINCE, {"since": "2026-09-01"})
    assert isinstance(parsed, ChangesSinceInput) and parsed.since.isoformat() == "2026-09-01"
    assert parsed.model_dump(mode="json") == {"since": "2026-09-01"}


@pytest.mark.parametrize(
    "tool,args",
    [
        (ToolName.GET_PROBLEMS, {"patient_uuid": "99999999-0000-0000-0000-000000000000"}),  # no way to pick a patient
        (ToolName.GET_LABS, {"since": "2026-01-01"}),  # argument the tool does not take
        (ToolName.GET_CHANGES_SINCE, {}),  # required argument missing
        (ToolName.GET_CHANGES_SINCE, {"since": "yesterday"}),
        (ToolName.GET_CHANGES_SINCE, {"since": "2026-13-45"}),
        (ToolName.GET_CHANGES_SINCE, {"since": 20260101}),  # an integer is not read as a timestamp
        (ToolName.GET_CHANGES_SINCE, {"since": None}),
        (ToolName.GET_CHANGES_SINCE, {"since": "2026-01-01", "patient_uuid": PID}),
        (ToolName.GET_CHANGES_SINCE, "2026-01-01"),  # not an object
        (ToolName.GET_PROBLEMS, None),
    ],
)
def test_invalid_inputs_map_to_invalid_input(tool, args):
    with pytest.raises(ToolError) as e:
        validate_input(tool, args)
    assert e.value.code == "invalid_input"


def test_invalid_input_message_names_the_field_but_not_the_value():
    with pytest.raises(ToolError) as e:
        validate_input(ToolName.GET_CHANGES_SINCE, {"since": "secret-looking-value"})
    assert "since" in e.value.message and "secret-looking-value" not in e.value.message


def _env(tool, records, **kw):
    return ToolEnvelope(tool=tool, patient_uuid=PID, retrieved_at="2026-10-12T00:00:00Z", records=records,
                        empty=not records, source_ids=[r.source_id for r in records], **kw)


def test_output_check_accepts_the_declared_type():
    env = _env(ToolName.GET_PROBLEMS, [Problem(source_id="Condition/c1", title="HTN", code=None, code_system=None,
                                               status=None, onset=None)])
    assert check_output(ToolName.GET_PROBLEMS, env) is env


def test_output_check_rejects_wrong_record_type_or_tool():
    wrong = _env(ToolName.GET_PROBLEMS, [Allergy(source_id="AllergyIntolerance/a1", substance="x", status=None,
                                                 reaction=None, criticality=None)])
    with pytest.raises(ToolError) as e:
        check_output(ToolName.GET_PROBLEMS, wrong)
    assert e.value.code == "bad_response"
    with pytest.raises(ToolError):
        check_output(ToolName.GET_LABS, _env(ToolName.GET_PROBLEMS, []))


def test_output_check_rejects_inconsistent_source_ids():
    env = _env(ToolName.GET_PROBLEMS, [Problem(source_id="Condition/c1", title="HTN", code=None, code_system=None,
                                               status=None, onset=None)]).model_copy(update={"source_ids": []})
    with pytest.raises(ToolError):
        check_output(ToolName.GET_PROBLEMS, env)
