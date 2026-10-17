"""Week 2 dashboard numbers and alerts: thresholds, minimum samples, and the eval-regression rule."""
import json
import time

from app.metrics import MIN_SAMPLES, eval_status, evaluate_alerts, evaluate_w2_alerts, summary, w2_summary
from app.tracing import Trace, TraceStore

BASE = {"rubrics": {"schema_valid": {"pass_rate": 100.0}, "safe_refusal": {"pass_rate": 100.0}}}


def add(store, flow, outcome, spans=(), ms=100.0):
    t = Trace(correlation_id="c" * 8, started=time.time() - ms / 1000, flow=flow)
    t.outcome, t.spans = outcome, list(spans)
    store.save(t)


def sp(name, ms, attrs, status="ok"):
    return ("w2", name, ms, status, "s" + name, None, attrs)


def eval_files(tmp_path, schema=100.0, refusal=100.0):
    res, base = tmp_path / "res.json", tmp_path / "base.json"
    base.write_text(json.dumps(BASE))
    res.write_text(json.dumps({"passed": 50, "total": 50, "generated": "t", "summary": {
        "rubrics": {"schema_valid": {"pass_rate": schema}, "safe_refusal": {"pass_rate": refusal}},
        "category_pass_rate": {"extraction": 100.0}}}))
    return res, base


def test_summary_rates_come_from_span_counts(tmp_path):
    store = TraceStore(str(tmp_path / "t.db"))
    add(store, "w2_ingest", "extracted", [sp("ingest", 900, {"fields_total": 5, "fields_supported": 4,
                                                             "confidence_avg": 0.9})])
    add(store, "w2_ingest", "failed", [sp("ingest", 300, {"fields_total": 0, "fields_supported": 0,
                                                          "confidence_avg": 0.0})])
    add(store, "w2_retrieve", "hit", [sp("retrieve", 40, {"hits": 3, "miss": 0, "rerank_skipped": 0})])
    add(store, "w2_retrieve", "miss", [sp("retrieve", 60, {"hits": 0, "miss": 1, "rerank_skipped": 1})])
    add(store, "w2_chat", "answer", [sp("supervisor", 500, {"route_document_facts_needed": 1, "route_ready": 1}),
                                     sp("intake_extractor", 20, {})])
    s = w2_summary(store)
    assert s["has_data"] and s["ingestions"] == 2 and s["extraction_failure_rate"] == 0.5
    assert s["extraction_field_pass_rate"] == 0.8 and s["retrieval_hit_rate"] == 0.5
    assert s["retrieval_rerank_skipped"] == 1 and s["routing"] == {"document_facts_needed": 1, "ready": 1}
    assert s["workers"]["intake_extractor"]["calls"] == 1


def test_summary_without_w2_traffic_reports_no_data(tmp_path):
    assert w2_summary(TraceStore(str(tmp_path / "t.db")))["has_data"] is False


def test_extraction_alert_needs_minimum_samples_and_ignores_rejected_uploads(tmp_path):
    # guards: one failed upload paging someone, and a rejected (too large) file counting as an extraction failure
    res, base = eval_files(tmp_path)
    store = TraceStore(str(tmp_path / "t.db"))
    for _ in range(MIN_SAMPLES - 1):
        add(store, "w2_ingest", "failed")
    assert not evaluate_w2_alerts(store, results=res, baseline=base)[0].firing
    add(store, "w2_ingest", "failed")
    for _ in range(10):
        add(store, "w2_ingest", "rejected")
    assert evaluate_w2_alerts(store, results=res, baseline=base)[0].firing


def test_retrieval_latency_alert_fires_above_the_slo(tmp_path):
    res, base = eval_files(tmp_path)
    store = TraceStore(str(tmp_path / "t.db"))
    for _ in range(MIN_SAMPLES):
        add(store, "w2_retrieve", "hit", [sp("retrieve", 5000, {"hits": 1, "miss": 0})])
    assert evaluate_w2_alerts(store, results=res, baseline=base)[1].firing


def test_eval_regression_fires_only_beyond_five_points(tmp_path):
    # guards: a real 5+ point drop going unnoticed, and noise inside the allowed margin paging
    store = TraceStore(str(tmp_path / "t.db"))
    res, base = eval_files(tmp_path, schema=95.0)
    assert not evaluate_w2_alerts(store, results=res, baseline=base)[2].firing
    res, base = eval_files(tmp_path, schema=94.9)
    alert = evaluate_w2_alerts(store, results=res, baseline=base)[2]
    assert alert.firing and eval_status(res, base)["regressed"] == ["schema_valid"]


def test_missing_eval_files_say_unavailable_and_do_not_fire(tmp_path):
    store = TraceStore(str(tmp_path / "t.db"))
    gone = tmp_path / "nope.json"
    assert eval_status(gone, gone)["available"] is False
    assert not evaluate_w2_alerts(store, results=gone, baseline=gone)[2].firing


def test_week1_alerts_and_summary_ignore_week2_flows(tmp_path):
    # guards: a normal 28 s ingestion (inside its 30 s SLO) paging the chat latency alert, and upload/retrieve rows
    # diluting the chat error rate and request count
    store = TraceStore(str(tmp_path / "t.db"))
    for _ in range(MIN_SAMPLES + 1):
        add(store, "w2_ingest", "extracted", ms=28_000.0)
        add(store, "w2_retrieve", "hit", ms=50.0)
        add(store, "w2_preflight", "ok", ms=5.0)
    w1 = {a.name: a for a in evaluate_alerts(store)}
    assert not any(a.firing for a in w1.values())
    assert w1["high_latency_p95"].samples == 0 and w1["high_error_rate"].samples == 0
    s = summary(store)
    assert s["requests"] == 0 and s["p95_ms"] is None and s["outcomes"] == {}


def test_week1_alerts_still_count_chat_flows_and_pre_c4_rows(tmp_path):
    # guards: the filter hiding real chat traffic (flow 'chat', 'w2_chat', or NULL from a Week 1 database)
    store = TraceStore(str(tmp_path / "t.db"))
    for flow in ("chat", "w2_chat", None):
        for _ in range(2):
            add(store, flow, "error", ms=30_000.0)
    add(store, "w2_ingest", "extracted", ms=28_000.0)  # must not be counted
    w1 = {a.name: a for a in evaluate_alerts(store)}
    assert w1["high_latency_p95"].samples == 6 and w1["high_latency_p95"].firing
    assert w1["high_error_rate"].samples == 6 and w1["high_error_rate"].firing
    assert summary(store)["requests"] == 6
