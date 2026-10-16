"""attach_and_extract: the only server-side path that turns an uploaded file into stored, cited facts.

It is a plain function behind the signed API. It is never registered as a chat tool (Week 1 invariant: the model has
no write path). Checks run in this order, all before any model call: type by content, size, page count, idempotency.
"""
from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from pathlib import Path

from app import tracing
from app.logging_setup import log_event
from contracts.w2 import DocStatus, DocType, DocumentRecord, ErrorCode, RenderMode

from .extract import extract_intake, extract_lab
from .safelog import error_code
from .render import PageRenderer, Rendered, TextLine, render_pdf
from .store import LAB_KIND, W2Store
from .support import ExtractionError
from .vlm import Vlm, VlmError

logger = logging.getLogger("agent.w2.ingest")

PDF_MAGIC = b"%PDF-"
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
JPEG_MAGIC = b"\xff\xd8\xff"


class RejectedUpload(Exception):
    """The file was refused before any model call. `code` is a fixed string safe to show the user."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def field_stats(rec: DocumentRecord) -> dict[str, int | float]:
    """Per-document extraction outcome as counts only: how many fields were read, how many were supported (cited and
    checked against the page), and the mean model confidence. Never a field name's value or any text."""
    items: list = []
    if rec.lab is not None:
        items = list(rec.lab.rows)
    elif rec.intake is not None:
        i = rec.intake
        items = [f for f in (*(getattr(i.demographics, k) for k in ("first_name", "last_name", "date_of_birth", "sex",
                                                                    "phone", "email", "address")),
                             i.chief_concern, *i.current_medications, *i.allergies, *i.family_history)
                 if f is not None]
    supported = sum(1 for f in items if f.support.value == "supported")
    conf = round(sum(f.confidence for f in items) / len(items), 3) if items else 0.0
    return {"fields_total": len(items), "fields_supported": supported, "confidence_avg": conf,
            "failed": int(rec.status is not DocStatus.EXTRACTED)}


@dataclass(frozen=True)
class IngestResult:
    record: DocumentRecord
    duplicate: bool


def sniff(data: bytes) -> str:
    """File kind by content, not by name or declared type."""
    if data.startswith(PDF_MAGIC):
        return "pdf"
    if data.startswith(PNG_MAGIC):
        return "png"
    if data.startswith(JPEG_MAGIC):
        return "jpeg"
    raise RejectedUpload("unsupported_type")


class Ingestor:
    def __init__(self, store: W2Store, vlm: Vlm, max_bytes: int, max_pages: int,
                 chain: list[PageRenderer] | None = None, allow_native_pdf: bool = True) -> None:
        self._store, self._vlm = store, vlm
        self._max_bytes, self._max_pages = max_bytes, max_pages
        self._chain, self._native = chain, allow_native_pdf

    def check(self, data: bytes) -> str:
        """Everything that can refuse a file, with no storage and no model call: size, type by content, page count.
        The module calls this (through /preflight) before it writes anything to OpenEMR."""
        if len(data) > self._max_bytes:
            raise RejectedUpload("too_large")
        kind = sniff(data)
        if kind == "pdf":
            rendered = render_pdf(data, self._max_pages, self._chain)
            if rendered is not None and rendered.total_pages > self._max_pages:
                raise RejectedUpload("too_many_pages")
        return kind

    def page_image(self, data: bytes, page: int) -> tuple[bytes, str, int] | None:
        """One page of an already-stored document as an image, for the click-to-source viewer: (bytes, mime type,
        total pages). Nothing is stored. None when the page does not exist or no renderer could produce it.
        Raises RejectedUpload for a file that is not a PDF or a supported image."""
        kind = sniff(data)
        if kind != "pdf":
            return (data, "image/png" if kind == "png" else "image/jpeg", 1) if page == 1 else None
        if page < 1 or page > self._max_pages:
            return None
        rendered = render_pdf(data, page, self._chain)
        if rendered is None or page > rendered.total_pages or len(rendered.pages) < page:
            return None
        return rendered.pages[page - 1], "image/png", rendered.total_pages

    async def ingest(self, patient_uuid: str, document_id: int, doc_type: DocType, data: bytes) -> IngestResult:
        if len(data) > self._max_bytes:
            raise RejectedUpload("too_large")
        kind = sniff(data)
        sha = hashlib.sha256(data).hexdigest()
        existing = self._store.find_by_hash(patient_uuid, sha)
        if existing is not None and existing.status is DocStatus.EXTRACTED:
            # The bytes were already extracted for this patient. Reuse that extraction, but under the document id
            # OpenEMR assigned this request: a page image is only shown when those two ids are the same.
            rebound = self._store.rebind(patient_uuid, existing.document_id, document_id)
            if rebound is None:
                log_event(logger, "w2_ingest", status="duplicate", document_id=existing.document_id)
                return IngestResult(existing, True)
            log_event(logger, "w2_ingest", status="duplicate", document_id=rebound.document_id)
            return IngestResult(rebound, True)
        pages, text, renderer, mode, native_pdf, lines = self._pages(data, kind)
        if mode is RenderMode.IMAGES and not pages:
            return self._failed(patient_uuid, document_id, doc_type, sha, ErrorCode.RENDER_FAILED, None, None)
        try:
            with tracing.span("w2", "vlm_extract"):
                raw = await self._vlm.extract(doc_type, pages, native_pdf)
        except VlmError as e:
            log_event(logger, "w2_extract", status="vlm_error", error=error_code(e), document_id=document_id)
            return self._failed(patient_uuid, document_id, doc_type, sha, ErrorCode.VLM_UNAVAILABLE, mode, renderer)
        return self._build(patient_uuid, document_id, doc_type, sha, raw, text, mode, renderer, lines)

    def _pages(self, data: bytes, kind: str) -> tuple[list[bytes], str, str | None, RenderMode, bytes | None,
                                                     tuple[TextLine, ...]]:
        if kind != "pdf":
            return [data], "", None, RenderMode.IMAGES, None, ()
        rendered: Rendered | None = render_pdf(data, self._max_pages, self._chain)
        if rendered is not None:
            if rendered.total_pages > self._max_pages:
                raise RejectedUpload("too_many_pages")
            return rendered.pages, rendered.text_layer, rendered.renderer, RenderMode.IMAGES, None, rendered.lines
        if self._native:
            return [], "", None, RenderMode.NATIVE_PDF, data, ()  # last resort: the VLM reads the PDF itself
        return [], "", None, RenderMode.IMAGES, None, ()

    def _build(self, patient: str, document_id: int, doc_type: DocType, sha: str, raw: str, text: str,
               mode: RenderMode, renderer: str | None, lines: tuple[TextLine, ...] = ()) -> IngestResult:
        native = mode is RenderMode.NATIVE_PDF
        try:
            if doc_type is DocType.LAB_PDF:
                lab = extract_lab(raw, document_id, text, native, lines)
                facts = [(LAB_KIND, r.model_dump(mode="json"), r.support.value,
                          r.citation.model_dump(mode="json") if r.citation else None) for r in lab.rows]
                date_ = lab.collection_date.isoformat() if lab.collection_date else None
            else:
                intake = extract_intake(raw, document_id, text, native, lines)
                facts, date_ = _intake_facts(intake), None
        except ExtractionError as e:
            log_event(logger, "w2_extract", status=error_code(e), document_id=document_id)
            return self._failed(patient, document_id, doc_type, sha, ErrorCode.SCHEMA_INVALID, mode, renderer)
        rec = self._store.save_extracted(patient, document_id, doc_type, sha, facts, date_, mode, renderer,
                                         self._vlm.model)
        stats = field_stats(rec)
        log_event(logger, "w2_extract", status="ok", document_id=document_id, facts=len(facts),
                  fields_supported=stats["fields_supported"], fields_total=stats["fields_total"],
                  confidence_avg=stats["confidence_avg"])
        return IngestResult(rec, False)

    def _failed(self, patient: str, document_id: int, doc_type: DocType, sha: str, code: ErrorCode,
                mode: RenderMode | None, renderer: str | None) -> IngestResult:
        rec = self._store.save_failed(patient, document_id, doc_type, sha, code, mode, renderer, self._vlm.model)
        return IngestResult(rec, False)


def _intake_facts(e) -> list[tuple[str, dict, str, dict | None]]:
    out: list[tuple[str, dict, str, dict | None]] = []

    def add(kind: str, f) -> None:
        if f is not None:
            out.append((kind, f.model_dump(mode="json"), f.support.value,
                        f.citation.model_dump(mode="json") if f.citation else None))

    for k in ("first_name", "last_name", "date_of_birth", "sex", "phone", "email", "address"):
        add(k, getattr(e.demographics, k))
    add("chief_concern", e.chief_concern)
    for k in ("current_medications", "allergies", "family_history"):
        for f in getattr(e, k):
            add(k, f)
    return out


async def attach_and_extract(ingestor: Ingestor, patient_uuid: str, file_path: str, doc_type: DocType,
                             document_id: int) -> IngestResult:
    """Server-side entry point (never a chat tool): read a file the module already stored in OpenEMR and extract."""
    return await ingestor.ingest(patient_uuid, document_id, doc_type, Path(file_path).read_bytes())
