"""Builds the Week 2 router from Settings. Imported by create_app only when W2_ENABLED, so Week 1 never loads it."""
from __future__ import annotations

import logging
from pathlib import Path

from fastapi import APIRouter

from app.authz import PatientAuthorizer
from app.config import Settings
from app.gateway import AuditSink, Gateway
from app.logging_setup import log_event
from contracts.w2 import DocType

from .api import build_w2_router
from .ingest import Ingestor
from .rag import build_retriever
from .render import available_renderers
from .store import W2Store
from .vlm import OpenRouterVlm, StubVlm, Vlm

logger = logging.getLogger("agent.w2")
_FIXTURES = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "w2"


def make_vlm(settings: Settings) -> Vlm:
    if not settings.w2_use_stub_vlm:
        return OpenRouterVlm(settings)
    # Offline demos and CI: canned, synthetic output only. Never used with real documents.
    return StubVlm({
        DocType.LAB_PDF: (_FIXTURES / "vlm_lab_good.json").read_text(),
        DocType.INTAKE_FORM: (_FIXTURES / "vlm_intake_good.json").read_text(),
    })


def build_w2(settings: Settings, gateway: Gateway, authorizer: PatientAuthorizer, audit: AuditSink) -> APIRouter:
    store = W2Store(f"{settings.data_dir}/w2.db")
    ingestor = Ingestor(store, make_vlm(settings), settings.w2_max_upload_bytes, settings.w2_max_pages)
    retriever = build_retriever(settings, offline=settings.w2_use_stub_vlm)
    # A missing renderer is visible at startup, not discovered on the first upload.
    log_event(logger, "w2_startup", renderers=available_renderers(), stub_vlm=settings.w2_use_stub_vlm)
    return build_w2_router(gateway, authorizer, ingestor, store, retriever, audit, settings.w2_max_upload_bytes)
