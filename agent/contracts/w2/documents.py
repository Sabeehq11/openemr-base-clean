"""Document registry, retrieval and API response contracts."""
from __future__ import annotations

from enum import Enum
from typing import Annotated

from pydantic import Field, model_validator

from .common import Citation, DocType, SourceType, Text, W2Model
from .extraction import IntakeFormExtraction, LabPdfExtraction


class DocStatus(str, Enum):
    EXTRACTED = "extracted"
    EXTRACTION_FAILED = "extraction_failed"


class ErrorCode(str, Enum):
    RENDER_FAILED = "render_failed"
    VLM_UNAVAILABLE = "vlm_unavailable"
    SCHEMA_INVALID = "schema_invalid"
    NOT_RUN = "not_run"


class RenderMode(str, Enum):
    IMAGES = "images"
    NATIVE_PDF = "native_pdf"


class DocumentRecord(W2Model):
    document_id: Annotated[int, Field(ge=1)]
    patient_uuid: Text
    doc_type: DocType
    sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    status: DocStatus
    error_code: ErrorCode | None = None
    render_mode: RenderMode | None = None
    renderer: str | None = None
    model: str | None = None
    extractor_version: str
    created_at: str
    lab: LabPdfExtraction | None = None
    intake: IntakeFormExtraction | None = None
    links: dict[str, str] = {}


class GuidelineChunk(W2Model):
    chunk_id: Text
    doc_id: Text
    title: Text
    publisher: Text
    year: Annotated[int, Field(ge=1900, le=2100)]
    section: Text
    url: str = ""
    licence: Text
    text: Text


class RetrievalStatus(str, Enum):
    OK = "ok"
    EMPTY = "empty"


class RetrievedSnippet(W2Model):
    chunk_id: Text
    score: float
    text: Text
    title: Text
    publisher: Text
    year: int
    citation: Citation

    @model_validator(mode="after")
    def _guideline_citation(self) -> "RetrievedSnippet":
        if self.citation.source_type is not SourceType.GUIDELINE:
            raise ValueError("a snippet carries a guideline citation")
        return self


class RetrievalResult(W2Model):
    status: RetrievalStatus
    rerank_skipped: bool = False
    snippets: Annotated[list[RetrievedSnippet], Field(max_length=5)] = []
