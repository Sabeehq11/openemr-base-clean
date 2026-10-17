"""Final extraction contracts: what the agent stores and returns.

A field is `supported` only with an extracted citation (page, quote, bbox). Otherwise it is `unsupported`: kept,
flagged and never used as the basis for an answer or written to the chart.
"""
from __future__ import annotations

from datetime import date
from typing import Annotated

from pydantic import Field, model_validator

from .common import MAX_ROWS, AbnormalFlag, Citation, Confidence, SourceType, Support, Text, W2Model


def _check_support(support: Support, citation: Citation | None) -> None:
    if support is Support.SUPPORTED and (citation is None or citation.source_type is not SourceType.EXTRACTED):
        raise ValueError("a supported value needs an extracted citation")


class LabRow(W2Model):
    test_name: Text
    value: Text
    unit: Annotated[str, Field(max_length=64)] | None = None
    reference_range: Annotated[str, Field(max_length=64)] | None = None
    abnormal_flag: AbnormalFlag = AbnormalFlag.UNKNOWN
    loinc: Annotated[str, Field(pattern=r"^\d{1,7}-\d$")] | None = None
    citation: Citation | None = None
    confidence: Confidence
    support: Support

    @model_validator(mode="after")
    def _supported_needs_citation(self) -> "LabRow":
        _check_support(self.support, self.citation)
        return self


class LabPdfExtraction(W2Model):
    document_id: Annotated[int, Field(ge=1)]
    collection_date: date | None = None
    rows: Annotated[list[LabRow], Field(max_length=MAX_ROWS)]


class IntakeField(W2Model):
    value: Text
    citation: Citation | None = None
    confidence: Confidence
    support: Support

    @model_validator(mode="after")
    def _supported_needs_citation(self) -> "IntakeField":
        _check_support(self.support, self.citation)
        return self


class Demographics(W2Model):
    first_name: IntakeField | None = None
    last_name: IntakeField | None = None
    date_of_birth: IntakeField | None = None
    sex: IntakeField | None = None
    phone: IntakeField | None = None
    email: IntakeField | None = None
    address: IntakeField | None = None


class IntakeFormExtraction(W2Model):
    document_id: Annotated[int, Field(ge=1)]
    demographics: Demographics = Demographics()
    chief_concern: IntakeField | None = None
    current_medications: Annotated[list[IntakeField], Field(max_length=MAX_ROWS)] = []
    allergies: Annotated[list[IntakeField], Field(max_length=MAX_ROWS)] = []
    family_history: Annotated[list[IntakeField], Field(max_length=MAX_ROWS)] = []


# ------------------------------------------------------------ raw VLM output (untrusted, closed schema)


class RawEvidence(W2Model):
    """What the model reports for one field. It never supplies source ids or document ids."""

    page: Annotated[int, Field(ge=1, le=50)] = 1
    quote: Annotated[str, Field(max_length=500)] = ""
    bbox: tuple[float, float, float, float] | None = None


class RawLabRow(W2Model):
    test_name: Annotated[str, Field(max_length=200)]
    value: Annotated[str, Field(max_length=100)]
    unit: Annotated[str, Field(max_length=64)] | None = None
    reference_range: Annotated[str, Field(max_length=64)] | None = None
    abnormal_flag: str | None = None
    loinc: str | None = None
    confidence: float = 0.0
    evidence: RawEvidence = RawEvidence()


class RawLabOutput(W2Model):
    collection_date: str | None = None
    rows: Annotated[list[RawLabRow], Field(max_length=MAX_ROWS)]


class RawIntakeField(W2Model):
    value: Annotated[str, Field(max_length=500)]
    confidence: float = 0.0
    evidence: RawEvidence = RawEvidence()


class RawIntakeOutput(W2Model):
    first_name: RawIntakeField | None = None
    last_name: RawIntakeField | None = None
    date_of_birth: RawIntakeField | None = None
    sex: RawIntakeField | None = None
    phone: RawIntakeField | None = None
    email: RawIntakeField | None = None
    address: RawIntakeField | None = None
    chief_concern: RawIntakeField | None = None
    current_medications: Annotated[list[RawIntakeField], Field(max_length=MAX_ROWS)] = []
    allergies: Annotated[list[RawIntakeField], Field(max_length=MAX_ROWS)] = []
    family_history: Annotated[list[RawIntakeField], Field(max_length=MAX_ROWS)] = []
