from __future__ import annotations

from datetime import date, datetime
from enum import Enum
from typing import Generic, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class ToolName(str, Enum):
    GET_PATIENT_SUMMARY = "get_patient_summary"
    GET_PROBLEMS = "get_problems"
    GET_MEDICATIONS = "get_medications"
    GET_ALLERGIES = "get_allergies"
    GET_LABS = "get_labs"
    GET_CHANGES_SINCE = "get_changes_since"


class ToolError(Exception):
    """Typed failure. `code` is machine-readable; `message` is safe to show (never raw upstream text)."""

    def __init__(self, code: Literal["unauthorized", "timeout", "upstream_error", "bad_response", "invalid_input"],
                 message: str, retryable: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable


class PatientSummary(_Frozen):
    source_id: str
    patient_uuid: str
    name: str | None
    birth_date: date | None = Field(description="None when missing in the chart (audit D-series defect)")
    sex: str | None
    age_years: int | None


class Problem(_Frozen):
    source_id: str
    title: str
    code: str | None = Field(description="ICD/SNOMED code when present; None for uncoded problems")
    code_system: str | None
    status: str | None
    onset: date | None


class Medication(_Frozen):
    source_id: str
    name: str
    dose_text: str | None = Field(description="Verbatim dose string from the chart; never normalised or computed")
    status: str | None
    authored_on: date | None
    store: Literal["prescriptions", "lists"] = "prescriptions"


class Allergy(_Frozen):
    source_id: str
    substance: str | None
    status: str | None
    reaction: str | None = Field(description="None when the chart has no reaction recorded or FHIR omits it")
    criticality: str | None


class LabResult(_Frozen):
    source_id: str
    test: str
    loinc: str | None
    value: float | None
    unit: str | None
    ref_low: float | None
    ref_high: float | None
    flag_reported: str | None = Field(description="Flag exactly as stored. The verifier recomputes it; do not trust it")
    collected: date | None


class Encounter(_Frozen):
    source_id: str
    date: date | None
    reason: str | None
    kind: str | None


class ChangeItem(_Frozen):
    source_id: str
    resource_type: str
    summary: str
    changed_on: date | None


class ChangesSince(_Frozen):
    since: date
    items: list[ChangeItem]


T = TypeVar("T")


class ToolEnvelope(BaseModel, Generic[T]):
    """What every tool returns. `empty` is explicit so 'no data' can be stated as a fact with a source."""

    model_config = ConfigDict(frozen=True)
    tool: ToolName
    patient_uuid: str
    retrieved_at: datetime
    records: list[T]
    empty: bool
    source_ids: list[str]
    notes: list[str] = Field(default_factory=list, description="Known coverage limits for this tool")
