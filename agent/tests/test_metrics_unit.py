import time

import pytest

from app import tracing
from app.metrics import evaluate_alerts, percentile, summary
from app.tracing import Trace, TraceStore


@pytest.fixture
def store(tmp_path):
    return TraceStore(str(tmp_path / "t.db"))


def add(store, outcome="answer", ms=1000.0, spans=(), retries=0, kept=3, dropped=1, cost=0.01, age=0.0):
    t = Trace(correlation_id="c" * 8, started=time.time() - ms / 1000)
    t.outcome, t.retries, t.claims_kept, t.claims_dropped, t.cost_usd = outcome, retries, kept, dropped, cost
    t.prompt_tokens, t.completion_tokens = 100, 20
    t.spans = list(spans)
    store.save(t)
    if age:
        store._db.execute("UPDATE traces SET ts = ts - ?", (age,))
        store._db.execute("UPDATE spans SET ts = ts - ?", (age,))
        store._db.commit()


def test_percentile_basics():
    assert percentile([], 95) is None
    assert percentile([5], 50) == 5
    vals = list(range(1, 101))
    assert percentile(vals, 50) == 50 and percentile(vals, 95) == 95


def test_span_records_ok_and_error_and_is_noop_without_trace():
    with tracing.span("tool", "x"):
        pass  # no active trace: must not fail
    t, tok = tracing.start("abcd1234")
    with tracing.span("tool", "get_labs"):
        pass
    with pytest.raises(RuntimeError):
        with tracing.span("tool", "get_problems"):
            raise RuntimeError("boom")
    tracing.count_retry()
    tracing.add_usage(10, 5, 0.002)
    tracing.end(tok)
    assert [(s[1], s[3]) for s in t.spans] == [("get_labs", "ok"), ("get_problems", "error")]
    assert t.retries == 1 and t.prompt_tokens == 10 and t.cost_usd == pytest.approx(0.002)


def test_summary_numbers(store):
    add(store, ms=1000, spans=[("tool", "get_labs", 50.0, "ok"), ("tool", "get_labs", 70.0, "error")], retries=1)
    add(store, outcome="denied", ms=200)
    add(store, outcome="error", ms=3000)
    s = summary(store)
    assert s["requests"] == 3 and s["errors"] == 1 and s["outcomes"]["denied"] == 1
    assert s["tool_calls"] == 2 and s["tool_failures"] == 1 and s["retries"] == 1
    assert s["tools"]["get_labs"]["calls"] == 2
    assert s["verification_pass_rate"] == pytest.approx(9 / 12)
    assert s["cost_usd"] == pytest.approx(0.03) and s["prompt_tokens"] == 300


def test_summary_window_excludes_old_traces(store):
    add(store, age=7200)
    add(store)
    assert summary(store, window_s=3600)["requests"] == 1


def test_no_alerts_when_healthy(store):
    for _ in range(10):
        add(store, ms=800, spans=[("tool", "get_labs", 10.0, "ok")])
    assert not any(a.firing for a in evaluate_alerts(store))


def test_latency_alert_fires_above_12s_p95(store):
    for _ in range(10):
        add(store, ms=15_000)
    a = {x.name: x for x in evaluate_alerts(store)}
    assert a["high_latency_p95"].firing and not a["high_error_rate"].firing


def test_latency_alert_ignores_old_samples_and_tiny_samples(store):
    for _ in range(10):
        add(store, ms=15_000, age=900)  # outside the 5 minute window
    add(store, ms=20_000)
    assert not {x.name: x for x in evaluate_alerts(store)}["high_latency_p95"].firing


def test_error_rate_alert_over_5_percent(store):
    for _ in range(8):
        add(store)
    for _ in range(2):
        add(store, outcome="error")
    assert {x.name: x for x in evaluate_alerts(store)}["high_error_rate"].firing


def test_denials_and_degraded_are_not_errors(store):
    for o in ("denied", "degraded") * 5:
        add(store, outcome=o)
    assert not {x.name: x for x in evaluate_alerts(store)}["high_error_rate"].firing


def test_tool_failure_alert_over_10_percent(store):
    sp = [("tool", "get_labs", 5.0, "ok")] * 7 + [("tool", "get_labs", 5.0, "error")] * 3
    add(store, spans=sp)
    a = {x.name: x for x in evaluate_alerts(store)}["high_tool_failure_rate"]
    assert a.firing and a.value == pytest.approx(0.3)
