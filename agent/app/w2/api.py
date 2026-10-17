"""Week 2 HTTP surface. Mounted only when W2_ENABLED. Every route: Gateway.verify (signature), then
PatientAuthorizer.authorize (fail closed), then the work. The store is scoped by patient UUID, so a document id that
belongs to another patient is simply not found.

The agent never writes to OpenEMR. The module writes the document and lab rows in the user's session and reports the
resulting record uuids back through /links.
"""
from __future__ import annotations

import base64
import binascii
import functools
import hashlib
import json
import logging
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app import tracing
from app.authz import PatientAuthorizer
from app.gateway import AuditSink, CallerContext, Gateway
from app.logging_setup import correlation_id_var, log_event
from app.tracing import TraceStore
from contracts.w2 import DocType

from .ingest import Ingestor, RejectedUpload, field_stats
from .rag.retriever import Retriever
from .render import available_renderers
from .store import W2Store

logger = logging.getLogger("agent.w2.api")
GENERIC_DENIAL = {"error": "forbidden", "message": "You do not have access to this patient."}
BAD_REQUEST = {"error": "invalid_request", "message": "Malformed request."}
NOT_FOUND = {"error": "not_found", "message": "No such document."}
_REJECT_STATUS = {"too_large": 413, "unsupported_type": 415, "too_many_pages": 413}
_UUID = r"^[0-9a-fA-F\-]{32,36}$"


class _Body(BaseModel):
    model_config = ConfigDict(extra="forbid")
    patient_uuid: str = Field(pattern=_UUID)


class UploadBody(_Body):
    document_id: int = Field(ge=1)
    doc_type: DocType
    content_b64: str


class PreflightBody(_Body):
    doc_type: DocType
    content_b64: str


class PageBody(_Body):
    page: int = Field(ge=1, le=50)
    content_b64: str


class LinksBody(_Body):
    links: dict[str, str] = Field(max_length=200)  # row index (as text) -> OpenEMR record uuid


class RetrieveBody(_Body):
    question: str = Field(min_length=1, max_length=500)
    test_names: list[str] = Field(default_factory=list, max_length=10)


def build_w2_router(gateway: Gateway, authorizer: PatientAuthorizer, ingestor: Ingestor, store: W2Store,
                    retriever: Retriever, audit: AuditSink, max_upload_bytes: int,
                    traces: TraceStore | None = None) -> APIRouter:
    router = APIRouter(prefix="/v1/w2")

    def traced(flow: str):
        """One trace per request (latency, outcome, spans), saved even if the handler raises. A handler may set
        `trace.outcome` itself (extracted, failed, hit, miss); otherwise it follows the HTTP status. No-op without a
        store, so Week 1 and the C1 tests are unchanged."""
        def deco(fn):
            @functools.wraps(fn)
            async def wrapper(*args, **kwargs):
                if traces is None:
                    return await fn(*args, **kwargs)
                trace, tok = tracing.start(correlation_id_var.get(), flow=flow)
                try:
                    resp = await fn(*args, **kwargs)
                    if trace.outcome == "error":
                        code = resp.status_code
                        trace.outcome = ("ok" if code == 200 else "denied" if code == 403
                                         else "rejected" if code < 500 else "error")
                    return resp
                finally:
                    traces.save(trace)
                    try:
                        tracing.end(tok)
                    except ValueError:
                        pass
            return wrapper
        return deco

    async def deny_unless_allowed(ctx: CallerContext, patient: str) -> JSONResponse | None:
        decision = await authorizer.authorize(ctx.token, patient, ctx.user_uuid)
        if decision.allowed:
            return None
        audit.record("w2_denied", user=ctx.user_uuid, patient=patient, reason=decision.reason.value,
                     correlation_id=ctx.correlation_id)
        return JSONResponse(GENERIC_DENIAL, status_code=403)  # same body whatever the reason

    def parse(ctx: CallerContext, model: type[BaseModel]) -> Any:
        try:
            return model.model_validate(json.loads(ctx.body or b"{}"))
        except (ValueError, ValidationError):
            return None

    @router.post("/preflight")
    @traced("w2_preflight")
    async def preflight(request: Request) -> JSONResponse:
        """Authorize and validate, store nothing. The module calls this before it writes anything to OpenEMR, so a
        denied or invalid upload leaves no document, lab row or extraction behind."""
        ctx = await gateway.verify(request)
        if len(ctx.body) > max_upload_bytes * 4 // 3 + 4096:
            return JSONResponse({"error": "too_large", "message": "File is too large."}, status_code=413)
        body: PreflightBody | None = parse(ctx, PreflightBody)
        if body is None:
            return JSONResponse(BAD_REQUEST, status_code=400)
        if (denied := await deny_unless_allowed(ctx, body.patient_uuid)) is not None:
            return denied
        try:
            data = base64.b64decode(body.content_b64, validate=True)
        except (binascii.Error, ValueError):
            return JSONResponse(BAD_REQUEST, status_code=400)
        try:
            ingestor.check(data)
        except RejectedUpload as e:
            audit.record("w2_upload_rejected", user=ctx.user_uuid, patient=body.patient_uuid, code=e.code,
                         correlation_id=ctx.correlation_id)
            return JSONResponse({"error": e.code, "message": "This file cannot be accepted."},
                                status_code=_REJECT_STATUS.get(e.code, 400))
        return JSONResponse({"ok": True})

    @router.post("/documents")
    @traced("w2_ingest")
    async def upload(request: Request) -> JSONResponse:
        ctx = await gateway.verify(request)
        if len(ctx.body) > max_upload_bytes * 4 // 3 + 4096:
            return JSONResponse({"error": "too_large", "message": "File is too large."}, status_code=413)
        body: UploadBody | None = parse(ctx, UploadBody)
        if body is None:
            return JSONResponse(BAD_REQUEST, status_code=400)
        if (denied := await deny_unless_allowed(ctx, body.patient_uuid)) is not None:
            return denied
        try:
            data = base64.b64decode(body.content_b64, validate=True)
        except (binascii.Error, ValueError):
            return JSONResponse(BAD_REQUEST, status_code=400)
        log_event(logger, "w2_ingest_start", doc_type=body.doc_type.value, size_bytes=len(data))
        try:
            with tracing.span("w2", "ingest") as attrs:
                res = await ingestor.ingest(body.patient_uuid, body.document_id, body.doc_type, data)
                attrs.update(field_stats(res.record))
        except RejectedUpload as e:
            audit.record("w2_upload_rejected", user=ctx.user_uuid, patient=body.patient_uuid, code=e.code,
                         correlation_id=ctx.correlation_id)
            return JSONResponse({"error": e.code, "message": "This file cannot be accepted."},
                                status_code=_REJECT_STATUS.get(e.code, 400))
        if (tr := tracing.current()) is not None:
            # extracted | failed | duplicate: the words the dashboard and the extraction alert count
            tr.outcome = ("duplicate" if res.duplicate
                          else "extracted" if res.record.status.value == "extracted" else "failed")
        log_event(logger, "w2_ingest_complete", status=res.record.status.value, duplicate=res.duplicate,
                  error_code=res.record.error_code.value if res.record.error_code else None,
                  doc_type=res.record.doc_type.value)
        audit.record("w2_upload", user=ctx.user_uuid, patient=body.patient_uuid, document_id=res.record.document_id,
                     status=res.record.status.value, duplicate=res.duplicate, correlation_id=ctx.correlation_id)
        return JSONResponse({"document": json.loads(res.record.model_dump_json()), "duplicate": res.duplicate,
                             "renderers_available": available_renderers()})

    @router.get("/documents/{document_id}")
    async def read(document_id: int, request: Request, patient_uuid: str = "") -> JSONResponse:
        # The gateway signs method, path and user but not the query string. That is safe here: the query names a
        # patient, and access to that patient is decided by the caller's token (PatientAuthorizer), not by the
        # query. A tampered query can only reach a patient the same user could already reach.
        ctx = await gateway.verify(request)
        try:
            body = _Body(patient_uuid=patient_uuid)
        except ValidationError:
            return JSONResponse(BAD_REQUEST, status_code=400)
        if (denied := await deny_unless_allowed(ctx, body.patient_uuid)) is not None:
            return denied
        record = store.get(body.patient_uuid, document_id)
        if record is None:
            return JSONResponse(NOT_FOUND, status_code=404)
        return JSONResponse({"document": json.loads(record.model_dump_json())})

    @router.post("/documents/{document_id}/page")
    async def page(document_id: int, request: Request) -> JSONResponse:
        """One page of a stored document as an image, for the click-to-source viewer. The module fetches the bytes
        from OpenEMR in the user's session; the agent keeps no document bytes. The bytes must hash to the stored
        extraction's sha256, so a page can only be shown for the document that was actually extracted."""
        ctx = await gateway.verify(request)
        if len(ctx.body) > max_upload_bytes * 4 // 3 + 4096:
            return JSONResponse({"error": "too_large", "message": "File is too large."}, status_code=413)
        body: PageBody | None = parse(ctx, PageBody)
        if body is None:
            return JSONResponse(BAD_REQUEST, status_code=400)
        if (denied := await deny_unless_allowed(ctx, body.patient_uuid)) is not None:
            return denied
        record = store.get(body.patient_uuid, document_id)
        if record is None:
            return JSONResponse(NOT_FOUND, status_code=404)
        try:
            data = base64.b64decode(body.content_b64, validate=True)
        except (binascii.Error, ValueError):
            return JSONResponse(BAD_REQUEST, status_code=400)
        if hashlib.sha256(data).hexdigest() != record.sha256:
            return JSONResponse(BAD_REQUEST, status_code=400)  # not the document that was extracted
        try:
            shown = ingestor.page_image(data, body.page)
        except RejectedUpload:
            return JSONResponse(BAD_REQUEST, status_code=400)
        if shown is None:
            return JSONResponse(NOT_FOUND, status_code=404)
        image, mime, total = shown
        log_event(logger, "w2_page", document_id=document_id, page=body.page, total_pages=total)
        audit.record("w2_page", user=ctx.user_uuid, patient=body.patient_uuid, document_id=document_id,
                     page=body.page, correlation_id=ctx.correlation_id)
        return JSONResponse({"page": body.page, "total_pages": total, "mime": mime,
                             "content_b64": base64.b64encode(image).decode("ascii")})

    @router.post("/documents/{document_id}/links")
    async def links(document_id: int, request: Request) -> JSONResponse:
        ctx = await gateway.verify(request)
        body: LinksBody | None = parse(ctx, LinksBody)
        if body is None:
            return JSONResponse(BAD_REQUEST, status_code=400)
        if (denied := await deny_unless_allowed(ctx, body.patient_uuid)) is not None:
            return denied
        if store.get(body.patient_uuid, document_id) is None:
            return JSONResponse(NOT_FOUND, status_code=404)
        try:
            parsed = {int(k): v for k, v in body.links.items() if v}
        except ValueError:
            return JSONResponse(BAD_REQUEST, status_code=400)
        updated = store.save_links(body.patient_uuid, document_id, parsed)
        audit.record("w2_links", user=ctx.user_uuid, patient=body.patient_uuid, document_id=document_id,
                     count=updated, correlation_id=ctx.correlation_id)
        return JSONResponse({"updated": updated})

    @router.post("/retrieve")
    @traced("w2_retrieve")
    async def retrieve(request: Request) -> JSONResponse:
        ctx = await gateway.verify(request)
        body: RetrieveBody | None = parse(ctx, RetrieveBody)
        if body is None:
            return JSONResponse(BAD_REQUEST, status_code=400)
        if (denied := await deny_unless_allowed(ctx, body.patient_uuid)) is not None:
            return denied
        # The query is the question plus test names only: no patient identifier or chart value is added.
        query = " ".join([body.question, *body.test_names])
        result = retriever.retrieve(query)
        if (tr := tracing.current()) is not None:
            tr.outcome = "hit" if result.snippets else "miss"
        audit.record("w2_retrieve", user=ctx.user_uuid, patient=body.patient_uuid, status=result.status.value,
                     count=len(result.snippets), rerank_skipped=result.rerank_skipped,
                     correlation_id=ctx.correlation_id)
        return JSONResponse(json.loads(result.model_dump_json()))

    return router
