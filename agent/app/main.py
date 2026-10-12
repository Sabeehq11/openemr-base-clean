"""Agent service entry point: /health, /ready and correlation-ID middleware."""
from __future__ import annotations

import logging
import re
import time
import uuid

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from .api import build_router
from .authz import PatientAuthorizer
from .config import Settings, get_settings
from .fhir_client import FhirClient
from .gateway import Gateway, LogAuditSink
from .logging_setup import correlation_id_var, log_event, setup_logging
from .tools import ToolRunner

logger = logging.getLogger("agent.http")
_CID_RE = re.compile(r"^[A-Za-z0-9\-]{8,64}$")


def create_app(settings: Settings | None = None) -> FastAPI:
    setup_logging()
    app = FastAPI(title="Clinical Co-Pilot agent", docs_url=None, redoc_url=None)
    app.state.settings = settings or get_settings()
    cfg: Settings = app.state.settings
    audit = LogAuditSink()
    app.state.gateway = Gateway(cfg, audit)
    app.state.runner = ToolRunner(PatientAuthorizer(cfg), FhirClient(cfg), audit=audit)
    app.include_router(build_router(app.state.gateway, app.state.runner))

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


app = create_app()
