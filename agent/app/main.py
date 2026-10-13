"""Agent service entry point: /health, /ready and correlation-ID middleware."""
from __future__ import annotations

import logging
import os
import re
import time
import uuid

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from .api import build_router
from .chat import LoadGauge, build_chat_router
from .conversation import ConversationStore
from .llm import make_llm
from .authz import PatientAuthorizer
from .config import Settings, get_settings
from .fhir_client import FhirClient
from .audit import ChainedAuditSink
from .dashboard import build_dashboard_router
from .gateway import Gateway, LogAuditSink
from .tracing import TraceStore
from .logging_setup import correlation_id_var, log_event, setup_logging
from .tools import ToolRunner
from .verifier import Verifier

logger = logging.getLogger("agent.http")
_CID_RE = re.compile(r"^[A-Za-z0-9\-]{8,64}$")


class _TeeAudit:
    """Writes every audit event to the HMAC chain and to the structured log."""

    def __init__(self, chain: ChainedAuditSink, log: LogAuditSink) -> None:
        self.chain, self._log = chain, log

    def record(self, event: str, **fields: object) -> None:
        self.chain.record(event, **fields)
        self._log.record(event, **fields)


def create_app(settings: Settings | None = None) -> FastAPI:
    setup_logging()
    app = FastAPI(title="Clinical Co-Pilot agent", docs_url=None, redoc_url=None)
    app.state.settings = settings or get_settings()
    cfg: Settings = app.state.settings
    os.makedirs(cfg.data_dir, exist_ok=True)
    key = cfg.audit_hmac_key.get_secret_value()
    # Fail closed on auditing: without a key there is no tamper-evident record, so the log-only sink is never used
    # silently for clinical calls; the readiness check below reports it.
    audit = _TeeAudit(ChainedAuditSink(f"{cfg.data_dir}/audit.db", key), LogAuditSink()) if key else LogAuditSink()
    app.state.audit_chain = audit.chain if isinstance(audit, _TeeAudit) else None
    app.state.traces = TraceStore(f"{cfg.data_dir}/traces.db")
    app.state.gateway = Gateway(cfg, audit)
    app.state.runner = ToolRunner(PatientAuthorizer(cfg), FhirClient(cfg), audit=audit)
    app.include_router(build_router(app.state.gateway, app.state.runner))
    app.state.finalizer = Verifier().verify  # the only route from model output to a user
    app.state.load = LoadGauge(cfg.max_inflight_chats)
    app.include_router(build_chat_router(
        app.state.gateway, app.state.runner, lambda: make_llm(cfg),
        lambda d, e: app.state.finalizer(d, e), ConversationStore(), audit, app.state.traces,
        cfg.openrouter_model if cfg.llm_provider == "openrouter" else cfg.llm_provider,
        max_inflight=cfg.max_inflight_chats, gauge=app.state.load))
    if isinstance(audit, _TeeAudit):
        app.include_router(build_dashboard_router(cfg, app.state.traces, audit.chain, app.state.load))

    @app.middleware("http")
    async def correlation_middleware(request: Request, call_next):
        supplied = request.headers.get("x-correlation-id", "")
        cid = supplied if _CID_RE.match(supplied) else str(uuid.uuid4())
        token = correlation_id_var.set(cid)
        start = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            logger.exception("unhandled error")
            response = JSONResponse({"error": "internal_error", "correlation_id": cid}, status_code=500)
        response.headers["x-correlation-id"] = cid
        log_event(
            logger,
            "request",
            method=request.method,
            path=request.url.path,
            status=response.status_code,
            ms=round((time.perf_counter() - start) * 1000, 1),
        )
        correlation_id_var.reset(token)
        return response

    @app.get("/health")
    async def health() -> dict[str, str]:
        """Liveness only: the process is up."""
        return {"status": "ok"}

    @app.get("/ready")
    async def ready(request: Request) -> JSONResponse:
        """Readiness: every dependency must be reachable, otherwise 503."""
        s: Settings = request.app.state.settings
        checks = {
            "openemr": await _check_openemr(s),
            "llm_provider": await _check_llm(s),
            "trace_store": _check_trace_store(s),
            "audit_chain": _check_audit(request.app.state.audit_chain),
        }
        ok = all(c["ok"] for c in checks.values())
        return JSONResponse({"status": "ready" if ok else "not_ready", "checks": checks}, status_code=200 if ok else 503)

    return app


async def _check_openemr(s: Settings) -> dict[str, object]:
    url = f"{s.openemr_base_url}/apis/default/fhir/metadata"
    try:
        async with httpx.AsyncClient(verify=s.openemr_verify_tls, timeout=3.0) as c:
            r = await c.get(url)
        return {"ok": r.status_code == 200, "detail": f"HTTP {r.status_code}"}
    except httpx.HTTPError as e:
        return {"ok": False, "detail": type(e).__name__}


async def _check_llm(s: Settings) -> dict[str, object]:
    if s.llm_provider == "stub":
        return {"ok": True, "detail": "stub provider (no network)"}
    if not s.openrouter_api_key.get_secret_value():
        return {"ok": False, "detail": "OPENROUTER_API_KEY not set"}
    try:
        async with httpx.AsyncClient(timeout=3.0) as c:
            r = await c.get(
                f"{s.openrouter_base_url}/models",
                headers={"Authorization": f"Bearer {s.openrouter_api_key.get_secret_value()}"},
            )
        return {"ok": r.status_code == 200, "detail": f"HTTP {r.status_code}"}
    except httpx.HTTPError as e:
        return {"ok": False, "detail": type(e).__name__}


def _check_trace_store(s: Settings) -> dict[str, object]:
    import os

    try:
        os.makedirs(s.data_dir, exist_ok=True)
        probe = os.path.join(s.data_dir, ".ready_probe")
        with open(probe, "w") as f:
            f.write("ok")
        os.remove(probe)
        return {"ok": True, "detail": "data dir writable"}
    except OSError as e:
        return {"ok": False, "detail": type(e).__name__}


def _check_audit(chain: ChainedAuditSink | None) -> dict[str, object]:
    if chain is None:
        return {"ok": False, "detail": "AUDIT_HMAC_KEY not set"}
    res = chain.verify()
    return {"ok": res.ok, "detail": f"{res.rows} rows verified" if res.ok else f"broken at row {res.first_bad_seq}"}


app = create_app()
