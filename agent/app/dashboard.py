"""GET /dashboard (HTML) and /dashboard.json: operational numbers from the trace store, plus audit-chain integrity.

Fail closed: with no DASHBOARD_TOKEN configured the routes do not exist (404). Otherwise HTTP Basic auth (any user name,
the token as password, constant-time compare). Only counts, timings and ids are shown, never prompts or chart values.
The agent port is bound to localhost and is not published by the tunnel; this is a second lock, not the only one.
"""
from __future__ import annotations

import base64
import hmac
from html import escape

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse

from .audit import ChainedAuditSink
from .chat import LoadGauge
from .config import Settings
from .metrics import eval_status, evaluate_alerts, evaluate_w2_alerts, summary, w2_summary
from .tracing import TraceStore


def _authorized(request: Request, token: str) -> bool:
    if not token:
        return False
    h = request.headers.get("authorization", "")
    if not h.lower().startswith("basic "):
        return False
    try:
        _, _, supplied = base64.b64decode(h[6:], validate=True).decode().partition(":")
    except (ValueError, UnicodeDecodeError):
        return False
    return hmac.compare_digest(supplied.encode(), token.encode())


def _fmt(v: object, unit: str = "") -> str:
    if v is None:
        return "n/a"
    return f"{v:.1f}{unit}" if isinstance(v, float) else f"{v}{unit}"


DEFAULT_REFRESH_S = 15


def clamp_refresh(seconds: int) -> int:
    """0 turns auto-refresh off; anything else is kept between 5 s and 5 min."""
    return 0 if seconds <= 0 else max(5, min(seconds, 300))


def _state(a: dict) -> str:
    """FIRING / ok. The eval-regression alert reads repo files the deployed image does not carry; with nothing to compare
    it is `n/a`, not a green `ok`, because CI (not this service) is what enforces the eval gate there."""
    if a["firing"]:
        return "FIRING"
    return "n/a (CI)" if a["name"] == "w2_eval_regression" and a["samples"] == 0 else "ok"


def _pct(v: float | None) -> str:
    return "n/a" if v is None else f"{v:.0%}"


def render_w2(w2: dict, ev: dict) -> str:
    """The Week 2 section: ingestion, extraction, retrieval, routing and the latest eval gate result."""
    cards = [("Ingestions", w2["ingestions"]), ("Ingest p95", _fmt(w2["ingest_p95_ms"], f" ms (SLO {w2['ingest_slo_ms']:.0f})")),
             ("Extraction failure", _pct(w2["extraction_failure_rate"])),
             ("Field pass rate", _pct(w2["extraction_field_pass_rate"])),
             ("Mean confidence", _fmt(w2["extraction_confidence_avg"])), ("Retrievals", w2["retrievals"]),
             ("Retrieval hit rate", _pct(w2["retrieval_hit_rate"])),
             ("Retrieval p95", _fmt(w2["retrieval_p95_ms"], f" ms (SLO {w2['retrieval_slo_ms']:.0f})")),
             ("Rerank skipped", w2["retrieval_rerank_skipped"])]
    card_html = "".join(f"<div class=card><b>{escape(str(v))}</b><span>{escape(k)}</span></div>" for k, v in cards)
    routes = ", ".join(f"{escape(k)}: {v}" for k, v in w2["routing"].items()) or "none"
    wrows = "".join(f"<tr><td>{escape(n)}</td><td>{d['calls']}</td><td>{d['failures']}</td><td>{_fmt(d['p95_ms'], ' ms')}</td></tr>"
                    for n, d in w2["workers"].items()) or "<tr><td colspan=4>no worker runs in window</td></tr>"
    if ev["available"]:
        erows = "".join(
            f"<tr class='{'fire' if n in ev['regressed'] else 'ok'}'><td>{escape(n)}</td><td>{_fmt(r['pass_rate'])}</td>"
            f"<td>{_fmt(r['baseline'])}</td><td>{'REGRESSED' if n in ev['regressed'] else 'ok'}</td></tr>"
            for n, r in ev["rubrics"].items())
        cats = ", ".join(f"{escape(k)}: {v:.0f}%" for k, v in ev["categories"].items())
        evals = (f"<h3>Eval gate (latest run: {escape(str(ev['passed']))}/{escape(str(ev['total']))} cases, "
                 f"{escape(str(ev['generated']))})</h3><table><tr><th>Rubric<th>Pass rate<th>Baseline<th>State</tr>{erows}</table>"
                 f"<p>By category: {cats}</p>")
    else:
        evals = "<h3>Eval gate</h3><p>No eval results in this deployment. Regressions are enforced by CI (w2-eval-gate.yml), which blocks the change before it ships; this panel and the w2_eval_regression alert report only where the evals folder is present (a source checkout).</p>"
    return (f"<h2>Week 2</h2><div class=g>{card_html}</div><p>Routing decisions: {routes}.</p>"
            f"<h3>Workers</h3><table><tr><th>Worker<th>Runs<th>Failures<th>p95</tr>{wrows}</table>{evals}")


def render(data: dict, alerts: list[dict], chain: dict, load: dict | None = None, refresh_s: int = 0,
           w2_html: str = "") -> str:
    rows = "".join(
        f"<tr><td>{escape(n)}</td><td>{d['calls']}</td><td>{d['failures']}</td><td>{_fmt(d['p95_ms'], ' ms')}</td></tr>"
        for n, d in data["tools"].items()) or "<tr><td colspan=4>no tool calls in window</td></tr>"
    arows = "".join(
        f"<tr class='{'fire' if a['firing'] else 'ok'}'><td>{escape(a['name'])}</td>"
        f"<td>{_state(a)}</td><td>{_fmt(a['value'])}</td><td>{a['threshold']}</td>"
        f"<td>{a['samples']}</td><td>{escape(a['runbook'])}</td></tr>" for a in alerts)
    rate = data["verification_pass_rate"]
    cards = [("Requests", data["requests"]), ("Errors", data["errors"]), ("p50", _fmt(data["p50_ms"], " ms")),
             ("p95", _fmt(data["p95_ms"], " ms")), ("Tool calls", data["tool_calls"]),
             ("Tool failures", data["tool_failures"]), ("Retries", data["retries"]),
             ("Verification pass", "n/a" if rate is None else f"{rate:.0%}"),
             ("Dropped claims", data["claims_dropped"]),
             ("Tokens in/out", f"{data['prompt_tokens']}/{data['completion_tokens']}"),
             ("Cost", f"${data['cost_usd']:.4f}"), ("Audit chain", "intact" if chain["ok"] else "BROKEN")]
    if load is not None:
        cap = f" (cap {load['cap']})" if load["cap"] else " (no cap)"
        cards += [("In flight now" + cap, load["inflight"]), ("Shed since start", load["shed"])]
    card_html = "".join(f"<div class=card><b>{escape(str(v))}</b><span>{escape(k)}</span></div>" for k, v in cards)
    outcomes = ", ".join(f"{escape(k)}: {v}" for k, v in sorted(data["outcomes"].items())) or "none"
    meta = f"<meta http-equiv=refresh content={refresh_s}>" if refresh_s else ""
    return (
        f"<!doctype html><meta charset=utf-8>{meta}<title>Co-Pilot dashboard</title>"
        "<style>body{font:14px system-ui;margin:24px;color:#222}.g{display:flex;flex-wrap:wrap;gap:10px}"
        ".card{border:1px solid #ccd;border-radius:6px;padding:10px 14px;min-width:120px}.card b{display:block;font-size:20px}"
        ".card span{color:#667}table{border-collapse:collapse;margin:10px 0}td,th{border:1px solid #ccd;padding:4px 10px}"
        ".fire{background:#fdd}.ok td:nth-child(2){color:#080}</style>"
        f"<h1>Clinical Co-Pilot</h1><p>Window: last {data['window_s'] // 60} min. Synthetic data only. "
        f"Outcomes: {outcomes}. Audit rows verified: {chain['rows']}. "
        + (f"Refreshes every {refresh_s} s.</p>" if refresh_s else "Auto-refresh off.</p>")
        + f"<div class=g>{card_html}</div>"
        "<h2>Alerts</h2><table><tr><th>Alert<th>State<th>Value<th>Threshold<th>Samples<th>Runbook</tr>"
        f"{arows}</table><h2>Tools</h2><table><tr><th>Tool<th>Calls<th>Failures<th>p95</tr>{rows}</table>{w2_html}")


def build_dashboard_router(settings: Settings, traces: TraceStore, audit: ChainedAuditSink,
                           load: LoadGauge | None = None) -> APIRouter:
    router = APIRouter()
    token = settings.dashboard_token.get_secret_value()

    def gate(request: Request) -> None:
        if not token:
            raise HTTPException(status_code=404)
        if not _authorized(request, token):
            raise HTTPException(status_code=401, headers={"WWW-Authenticate": 'Basic realm="copilot"'})

    def payload(window_s: int) -> tuple[dict, list[dict], dict]:
        data = summary(traces, window_s)
        alerts = [a.__dict__ for a in evaluate_alerts(traces)]
        if settings.w2_enabled:  # Week 2 alerts exist only when Week 2 does; the Week 1 list is otherwise unchanged
            alerts += [a.__dict__ for a in evaluate_w2_alerts(traces)]
        res = audit.verify()
        return data, alerts, {"ok": res.ok, "rows": res.rows, "first_bad_seq": res.first_bad_seq}

    @router.get("/dashboard", response_class=HTMLResponse)
    async def dashboard(request: Request, window: int = 3600, refresh: int = DEFAULT_REFRESH_S) -> HTMLResponse:
        gate(request)
        data, alerts, chain = payload(max(60, min(window, 86_400)))
        w2_html = render_w2(w2_summary(traces, data["window_s"]), eval_status()) if settings.w2_enabled else ""
        page = render(data, alerts, chain, load.snapshot() if load else None, clamp_refresh(refresh), w2_html)
        return HTMLResponse(page, headers={"cache-control": "no-store"})

    @router.get("/dashboard.json")
    async def dashboard_json(request: Request, window: int = 3600) -> JSONResponse:
        gate(request)
        data, alerts, chain = payload(max(60, min(window, 86_400)))
        body = {"summary": data, "alerts": alerts, "audit_chain": chain, "load": load.snapshot() if load else None}
        if settings.w2_enabled:
            body["w2"] = {**w2_summary(traces, data["window_s"]), "eval": eval_status()}
        return JSONResponse(body, headers={"cache-control": "no-store"})

    return router
