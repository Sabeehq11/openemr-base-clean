"""Aggregates stored traces into dashboard numbers and evaluates the three alert rules. Pure functions over a store,
so the dashboard, the CLI alert script and the tests all share one definition."""
from __future__ import annotations

import math
import time
from dataclasses import dataclass

from .tracing import TraceStore

# Alert definitions (docs/week1/ARCHITECTURE.md section 7). Minimum sample sizes stop one slow request from paging anyone.
# 25 s, not 12 s: with the real model a healthy request has p50 ~14 s and p95 ~19 s (docs/week1/PERFORMANCE.md), so 12 s fired
# on normal traffic. Overloaded runs reached p95 31-44 s, so 25 s still fires when something is really wrong.
P95_LIMIT_MS, P95_WINDOW_S = 25_000.0, 300
ERR_LIMIT, ERR_WINDOW_S = 0.05, 600
TOOL_FAIL_LIMIT, TOOL_WINDOW_S = 0.10, 600
MIN_SAMPLES = 5
BAD_OUTCOMES = ("error",)  # denied and degraded are expected behaviours, not errors


def percentile(values: list[float], p: float) -> float | None:
    if not values:
        return None
    s = sorted(values)
    return s[min(len(s) - 1, max(0, math.ceil(p / 100 * len(s)) - 1))]


def summary(store: TraceStore, window_s: int = 3600, now: float | None = None) -> dict:
    since = (now if now is not None else time.time()) - window_s
    t = store.query("SELECT outcome, latency_ms, retries, prompt_tokens, completion_tokens, cost_usd, claims_kept, "
                    "claims_dropped FROM traces WHERE ts >= ?", (since,))
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
    lat = [r[0] for r in store.query("SELECT latency_ms FROM traces WHERE ts >= ?", (now - P95_WINDOW_S,))]
    p95 = percentile(lat, 95)
    outs = [r[0] for r in store.query("SELECT outcome FROM traces WHERE ts >= ?", (now - ERR_WINDOW_S,))]
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
