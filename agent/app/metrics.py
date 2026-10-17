"""Aggregates stored traces into dashboard numbers and evaluates the three alert rules. Pure functions over a store,
so the dashboard, the CLI alert script and the tests all share one definition."""
from __future__ import annotations

import json
import math
import time
from dataclasses import dataclass
from pathlib import Path

from .tracing import TraceStore

# Alert definitions (docs/week1/ARCHITECTURE.md section 7). Minimum sample sizes stop one slow request from paging anyone.
# 25 s, not 12 s: with the real model a healthy request has p50 ~14 s and p95 ~19 s (docs/week1/PERFORMANCE.md), so 12 s fired
# on normal traffic. Overloaded runs reached p95 31-44 s, so 25 s still fires when something is really wrong.
P95_LIMIT_MS, P95_WINDOW_S = 25_000.0, 300
ERR_LIMIT, ERR_WINDOW_S = 0.05, 600
TOOL_FAIL_LIMIT, TOOL_WINDOW_S = 0.10, 600
MIN_SAMPLES = 5
# Week 1 numbers describe chat requests only. Upload, retrieve and preflight traces (C4) have different latency and outcome
# vocabularies; counting them would page the chat latency alert on a normal 28 s ingestion. NULL = a row from before C4.
CHAT_FLOWS = "(flow IS NULL OR flow IN ('chat', 'w2_chat'))"
BAD_OUTCOMES = ("error",)  # denied and degraded are expected behaviours, not errors


def percentile(values: list[float], p: float) -> float | None:
    if not values:
        return None
    s = sorted(values)
    return s[min(len(s) - 1, max(0, math.ceil(p / 100 * len(s)) - 1))]


def summary(store: TraceStore, window_s: int = 3600, now: float | None = None) -> dict:
    since = (now if now is not None else time.time()) - window_s
    t = store.query("SELECT outcome, latency_ms, retries, prompt_tokens, completion_tokens, cost_usd, claims_kept, "
                    f"claims_dropped FROM traces WHERE {CHAT_FLOWS} AND ts >= ?", (since,))
    lat = [r[1] for r in t]
    spans = store.query("SELECT kind, name, status, latency_ms FROM spans WHERE ts >= ?", (since,))
    tools = [s for s in spans if s[0] == "tool"]
    by_tool: dict[str, dict] = {}
    for _, name, status, ms in tools:
        d = by_tool.setdefault(name, {"calls": 0, "failures": 0, "ms": []})
        d["calls"] += 1
        d["failures"] += status != "ok"
        d["ms"].append(ms)
    kept, dropped = sum(r[6] for r in t), sum(r[7] for r in t)
    outcomes: dict[str, int] = {}
    for r in t:
        outcomes[r[0]] = outcomes.get(r[0], 0) + 1
    return {
        "window_s": window_s,
        "requests": len(t),
        "outcomes": outcomes,
        "errors": sum(outcomes.get(o, 0) for o in BAD_OUTCOMES),
        "p50_ms": percentile(lat, 50), "p95_ms": percentile(lat, 95),
        "tool_calls": len(tools), "tool_failures": sum(1 for s in tools if s[2] != "ok"),
        "tools": {n: {"calls": d["calls"], "failures": d["failures"], "p95_ms": percentile(d["ms"], 95)}
                  for n, d in sorted(by_tool.items())},
        "retries": sum(r[2] for r in t),
        "claims_kept": kept, "claims_dropped": dropped,
        "verification_pass_rate": (kept / (kept + dropped)) if (kept + dropped) else None,
        "prompt_tokens": sum(r[3] for r in t), "completion_tokens": sum(r[4] for r in t),
        "cost_usd": round(sum(r[5] for r in t), 6),
        "cost_per_request_usd": round(sum(r[5] for r in t) / len(t), 6) if t else None,
    }


@dataclass(frozen=True)
class Alert:
    name: str
    firing: bool
    value: float | None
    threshold: float
    samples: int
    runbook: str


def evaluate_alerts(store: TraceStore, now: float | None = None) -> list[Alert]:
    now = now if now is not None else time.time()
    lat = [r[0] for r in store.query(f"SELECT latency_ms FROM traces WHERE {CHAT_FLOWS} AND ts >= ?", (now - P95_WINDOW_S,))]
    p95 = percentile(lat, 95)
    outs = [r[0] for r in store.query(f"SELECT outcome FROM traces WHERE {CHAT_FLOWS} AND ts >= ?", (now - ERR_WINDOW_S,))]
    err = (sum(o in BAD_OUTCOMES for o in outs) / len(outs)) if outs else None
    st = [r[0] for r in store.query("SELECT status FROM spans WHERE kind='tool' AND ts >= ?", (now - TOOL_WINDOW_S,))]
    tf = (sum(s != "ok" for s in st) / len(st)) if st else None
    return [
        Alert("high_latency_p95", len(lat) >= MIN_SAMPLES and p95 is not None and p95 > P95_LIMIT_MS, p95, P95_LIMIT_MS,
              len(lat), "RUNBOOK.md#high_latency_p95"),
        Alert("high_error_rate", len(outs) >= MIN_SAMPLES and err is not None and err > ERR_LIMIT, err, ERR_LIMIT,
              len(outs), "RUNBOOK.md#high_error_rate"),
        Alert("high_tool_failure_rate", len(st) >= MIN_SAMPLES and tf is not None and tf > TOOL_FAIL_LIMIT, tf,
              TOOL_FAIL_LIMIT, len(st), "RUNBOOK.md#high_tool_failure_rate"),
    ]


# ---------------------------------------------------------------------------------------------------------------------
# Week 2 (C4). Additive: nothing above changes. Same trace store, same Alert shape, same minimum-sample rule.
# Counts and timings only; the span `attrs` column holds numbers, never text.
# ---------------------------------------------------------------------------------------------------------------------
SLO_INGEST_P95_MS = 30_000.0   # upload to stored extraction; the VLM timeout (30 s) is the ceiling. Re-measured in C5.
SLO_RETRIEVE_P95_MS = 3_000.0  # hybrid retrieval with rerank. Re-measured in C5.
W2_EXTRACT_FAIL_LIMIT, W2_EXTRACT_WINDOW_S = 0.20, 600
W2_RETRIEVE_WINDOW_S = 300
EVAL_MAX_DROP = 5.0  # points; the same rule as evals/gate.py
_REPO = Path(__file__).resolve().parents[2]
EVAL_RESULTS = _REPO / "evals" / "results" / "w2-golden-latest.json"
EVAL_BASELINE = _REPO / "evals" / "w2" / "baseline.json"
WORKERS = ("intake_extractor", "chart_facts", "evidence_retriever")


def _attrs(raw: object) -> dict:
    try:
        v = json.loads(raw) if isinstance(raw, str) else {}
    except ValueError:
        return {}
    return v if isinstance(v, dict) else {}


def _rate(num: float, den: float) -> float | None:
    return (num / den) if den else None


def w2_summary(store: TraceStore, window_s: int = 3600, now: float | None = None) -> dict:
    """Week 2 numbers for the dashboard. `has_data` is False until a W2 request has been traced."""
    since = (now if now is not None else time.time()) - window_s
    ingest = store.query("SELECT outcome, latency_ms FROM traces WHERE flow='w2_ingest' AND ts >= ?", (since,))
    spans = store.query("SELECT name, status, latency_ms, attrs FROM spans WHERE kind='w2' AND ts >= ?", (since,))
    outcomes: dict[str, int] = {}
    for o, _ in ingest:
        outcomes[o] = outcomes.get(o, 0) + 1
    extracted, failed = outcomes.get("extracted", 0), outcomes.get("failed", 0)
    ing = [_attrs(a) for n, _, _, a in spans if n == "ingest"]
    total = sum(a.get("fields_total", 0) for a in ing)
    supported = sum(a.get("fields_supported", 0) for a in ing)
    scored = [a for a in ing if a.get("fields_total")]
    ret = [(_attrs(a), ms) for n, _, ms, a in spans if n == "retrieve"]
    routes: dict[str, int] = {}
    for n, _, _, a in spans:
        if n == "supervisor":
            for k, v in _attrs(a).items():
                if k.startswith("route_"):
                    routes[k[6:]] = routes.get(k[6:], 0) + int(v)
    workers = {}
    for w in WORKERS:
        rows = [(s, m) for n, s, m, _ in spans if n == w]
        if rows:
            workers[w] = {"calls": len(rows), "failures": sum(1 for s, _ in rows if s != "ok"),
                          "p95_ms": percentile([m for _, m in rows], 95)}
    return {
        "has_data": bool(ingest or ret or routes),
        "ingestions": len(ingest), "ingest_outcomes": outcomes,
        "ingest_p95_ms": percentile([r[1] for r in ingest], 95), "ingest_slo_ms": SLO_INGEST_P95_MS,
        "extraction_failure_rate": _rate(failed, extracted + failed),
        "extraction_field_pass_rate": _rate(supported, total),
        "extraction_confidence_avg": (round(sum(a["confidence_avg"] for a in scored) / len(scored), 3)
                                      if scored else None),
        "retrievals": len(ret),
        "retrieval_hit_rate": _rate(sum(1 for a, _ in ret if not a.get("miss", 0)), len(ret)),
        "retrieval_rerank_skipped": sum(int(a.get("rerank_skipped", 0)) for a, _ in ret),
        "retrieval_p95_ms": percentile([m for _, m in ret], 95), "retrieval_slo_ms": SLO_RETRIEVE_P95_MS,
        "routing": dict(sorted(routes.items())), "workers": workers,
    }


def eval_status(results: Path = EVAL_RESULTS, baseline: Path = EVAL_BASELINE) -> dict:
    """Latest golden-set result against its baseline, per rubric and per category. Reads two repo files; where they
    are absent (a deployed container without the evals folder) it says so instead of guessing."""
    try:
        res, base = json.loads(results.read_text()), json.loads(baseline.read_text())
        rubrics, base_rubrics = res["summary"]["rubrics"], base["rubrics"]
        cats = res["summary"].get("category_pass_rate", {})
    except (OSError, ValueError, KeyError, TypeError):
        return {"available": False, "rubrics": {}, "categories": {}, "regressed": []}
    out, regressed = {}, []
    for name, b in base_rubrics.items():
        now = rubrics.get(name, {}).get("pass_rate")
        drop = None if now is None else round(b["pass_rate"] - now, 2)
        out[name] = {"pass_rate": now, "baseline": b["pass_rate"], "drop": drop}
        if now is None or drop > EVAL_MAX_DROP:
            regressed.append(name)
    return {"available": True, "passed": res.get("passed"), "total": res.get("total"), "generated": res.get("generated"),
            "rubrics": out, "categories": dict(sorted(cats.items())), "regressed": sorted(regressed)}


def evaluate_w2_alerts(store: TraceStore, now: float | None = None, results: Path = EVAL_RESULTS,
                       baseline: Path = EVAL_BASELINE) -> list[Alert]:
    now = now if now is not None else time.time()
    outs = [r[0] for r in store.query("SELECT outcome FROM traces WHERE flow='w2_ingest' AND ts >= ?",
                                      (now - W2_EXTRACT_WINDOW_S,))]
    done = [o for o in outs if o in ("extracted", "failed")]  # a rejected or denied upload is not an extraction
    fail = _rate(sum(o == "failed" for o in done), len(done))
    lat = [r[0] for r in store.query("SELECT latency_ms FROM spans WHERE kind='w2' AND name='retrieve' AND ts >= ?",
                                     (now - W2_RETRIEVE_WINDOW_S,))]
    p95 = percentile(lat, 95)
    ev = eval_status(results, baseline)
    worst = max((r["drop"] for r in ev["rubrics"].values() if r["drop"] is not None), default=None)
    return [
        Alert("w2_extraction_failure_rate", len(done) >= MIN_SAMPLES and fail is not None and fail > W2_EXTRACT_FAIL_LIMIT,
              fail, W2_EXTRACT_FAIL_LIMIT, len(done), "RUNBOOK.md#w2_extraction_failure_rate"),
        Alert("w2_retrieval_latency_p95", len(lat) >= MIN_SAMPLES and p95 is not None and p95 > SLO_RETRIEVE_P95_MS,
              p95, SLO_RETRIEVE_P95_MS, len(lat), "RUNBOOK.md#w2_retrieval_latency_p95"),
        # Samples = rubrics compared. No result file means nothing to compare, so it cannot fire.
        Alert("w2_eval_regression", bool(ev["regressed"]), worst, EVAL_MAX_DROP, len(ev["rubrics"]),
              "RUNBOOK.md#w2_eval_regression"),
    ]
