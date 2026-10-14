"""Shared enums and the Citation contract for Week 2. Every model is frozen and rejects unknown fields."""
from __future__ import annotations

from enum import Enum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# Upper bounds keep a hostile or broken model output from producing unbounded records.
MAX_TEXT = 2000
MAX_ROWS = 200


class W2Model(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", str_strip_whitespace=True)


class SourceType(str, Enum):
    CHART = "chart"
    EXTRACTED = "extracted"
    GUIDELINE = "guideline"


class DocType(str, Enum):
    LAB_PDF = "lab_pdf"
    INTAKE_FORM = "intake_form"


class AbnormalFlag(str, Enum):
    NORMAL = "normal"
    HIGH = "high"
    LOW = "low"
    CRITICAL = "critical"
    UNKNOWN = "unknown"


class Support(str, Enum):
    SUPPORTED = "supported"
    UNSUPPORTED = "unsupported"


Text = Annotated[str, Field(min_length=1, max_length=MAX_TEXT)]
Confidence = Annotated[float, Field(ge=0.0, le=1.0)]


class Citation(W2Model):
    """Machine-readable pointer to the exact evidence for a claim (PRD shape plus a page-relative bbox)."""

    source_type: SourceType
    source_id: Text
    page_or_section: Text
    field_or_chunk_id: Text
    quote_or_value: Text
    bbox: tuple[float, float, float, float] | None = None

    @field_validator("bbox")
    @classmethod
    def _bbox_in_page(cls, v: tuple[float, float, float, float] | None) -> tuple[float, float, float, float] | None:
        if v is None:
            return v
        x0, y0, x1, y1 = v
        if not all(0.0 <= c <= 1.0 for c in v):
            raise ValueError("bbox coordinates must be page-relative, between 0 and 1")
        if not (x0 < x1 and y0 < y1):
            raise ValueError("bbox must have x0 < x1 and y0 < y1")
        return v

    @model_validator(mode="after")
    def _extracted_needs_region(self) -> "Citation":
        if self.source_type is SourceType.EXTRACTED and self.bbox is None:
            raise ValueError("an extracted citation requires a bbox")
        return self
