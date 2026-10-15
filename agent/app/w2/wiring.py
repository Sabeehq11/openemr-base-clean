"""Builds the Week 2 router from Settings. Imported by create_app only when W2_ENABLED, so Week 1 never loads it."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from fastapi import APIRouter

from app.authz import PatientAuthorizer
from app.chat import Finalizer, LoadGauge
from app.config import Settings
from app.conversation import ConversationStore
from app.gateway import AuditSink, Gateway
from app.llm import LLMClient
from app.logging_setup import log_event
from app.tools import ToolRunner
from app.tracing import TraceStore
from contracts.w2 import DocType

from .api import build_w2_router
from .chat import build_w2_chat_router
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


@dataclass(frozen=True)
class ChatDeps:
    """What /v1/w2/chat borrows from the Week 1 app: the same runner, model factory, verifier and conversations."""

    runner: ToolRunner
    llm_factory: Callable[[], LLMClient]
    finalizer: Finalizer
    conversations: ConversationStore
    traces: TraceStore | None = None
    model: str = ""
    max_inflight: int = 0
    gauge: LoadGauge | None = None


def build_w2(settings: Settings, gateway: Gateway, authorizer: PatientAuthorizer, audit: AuditSink,
             chat: ChatDeps | None = None) -> APIRouter:
    store = W2Store(f"{settings.data_dir}/w2.db")
    ingestor = Ingestor(store, make_vlm(settings), settings.w2_max_upload_bytes, settings.w2_max_pages)
    retriever = build_retriever(settings, offline=settings.w2_use_stub_vlm)
    # A missing renderer is visible at startup, not discovered on the first upload.
    log_event(logger, "w2_startup", renderers=available_renderers(), stub_vlm=settings.w2_use_stub_vlm)
    router = build_w2_router(gateway, authorizer, ingestor, store, retriever, audit, settings.w2_max_upload_bytes)
    if chat is not None:  # C2: the supervisor path. Without it only the C1 upload and retrieval routes exist.
        router.include_router(build_w2_chat_router(
            gateway, chat.runner, chat.llm_factory, chat.finalizer, chat.conversations, store, retriever, audit,
            chat.traces, chat.model, chat.max_inflight, chat.gauge, prefix=""))
    return router
