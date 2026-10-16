"""C4 span ids: a worker span is a child of the supervisor span, and an old traces.db upgrades in place."""
import sqlite3

from app import tracing
from app.tracing import TraceStore
from app.w2.supervisor import Supervisor
from contracts.w2 import Node, WorkerResult, WorkerStatus


def test_nested_spans_record_parent_ids():
    # guards: orphaned spans that make a multi-agent trace impossible to rebuild from the correlation id
    t, tok = tracing.start("corr-12345678")
    try:
        with tracing.span("w2", "supervisor"):
            with tracing.span("w2", "intake_extractor"):
                with tracing.span("w2", "embed"):
                    pass
    finally:
        tracing.end(tok)
    by_name = {s[1]: s for s in t.spans}
    assert by_name["supervisor"][5] is None
    assert by_name["intake_extractor"][5] == by_name["supervisor"][4]
    assert by_name["embed"][5] == by_name["intake_extractor"][4]


async def test_supervisor_workers_are_children_of_the_supervisor_span(tmp_path):
    async def ok(node):
        return WorkerResult(worker=node, status=WorkerStatus.OK)

    nodes = {n: (lambda n=n: ok(n)) for n in (Node.INTAKE_EXTRACTOR, Node.CHART_FACTS, Node.EVIDENCE_RETRIEVER)}
    t, tok = tracing.start("corr-12345678")
    try:
        await Supervisor(nodes, lambda s: True).run()
    finally:
        tracing.end(tok)
    root = next(s for s in t.spans if s[1] == "supervisor")
    workers = [s for s in t.spans if s[1] in {"intake_extractor", "chart_facts", "evidence_retriever"}]
    assert len(workers) == 3 and all(w[5] == root[4] for w in workers)
    store = TraceStore(str(tmp_path / "t.db"))
    store.save(t)
    rows = store.query("SELECT name, parent_span_id FROM spans WHERE name != 'supervisor'")
    assert rows and all(r[1] == root[4] for r in rows)


def test_old_traces_db_is_upgraded_in_place_and_twice_is_safe(tmp_path):
    # guards: a crash at startup on a Week 1 traces.db, or a second start failing on a duplicate column
    path = str(tmp_path / "old.db")
    db = sqlite3.connect(path)
    db.executescript(
        "CREATE TABLE traces (id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL, correlation_id TEXT NOT NULL, "
        "outcome TEXT NOT NULL, latency_ms REAL NOT NULL, retries INTEGER NOT NULL, prompt_tokens INTEGER NOT NULL, "
        "completion_tokens INTEGER NOT NULL, cost_usd REAL NOT NULL, claims_kept INTEGER NOT NULL, "
        "claims_dropped INTEGER NOT NULL, model TEXT NOT NULL);"
        "CREATE TABLE spans (id INTEGER PRIMARY KEY AUTOINCREMENT, trace_id INTEGER NOT NULL, ts REAL NOT NULL, "
        "kind TEXT NOT NULL, name TEXT NOT NULL, latency_ms REAL NOT NULL, status TEXT NOT NULL);"
        "INSERT INTO traces VALUES (1, 1.0, 'old-corr-1', 'answer', 5.0, 0, 1, 1, 0.0, 1, 0, 'm');"
        "INSERT INTO spans VALUES (1, 1, 1.0, 'tool', 'get_labs', 5.0, 'ok');")
    db.commit()
    db.close()
    TraceStore(path)
    store = TraceStore(path)
    assert store.query("SELECT flow FROM traces")[0][0] is None
    assert store.query("SELECT span_id, parent_span_id, attrs FROM spans")[0] == (None, None, None)


def _traced_client(tmp_path):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from pydantic import SecretStr

    from app.config import Settings
    from app.gateway import Gateway
    from app.w2.api import build_w2_router
    from app.w2.rag import build_retriever
    from tests.test_w2_api_unit import NOW, SECRET, Audit, Authz
    from tests.w2_helpers import make_ingestor

    s = Settings(module_signing_secret=SecretStr(SECRET), data_dir=str(tmp_path))
    audit, store = Audit(), TraceStore(str(tmp_path / "t.db"))
    ingestor, w2store = make_ingestor(tmp_path)
    app = FastAPI()
    app.include_router(build_w2_router(Gateway(s, audit, clock=lambda: NOW), Authz(), ingestor, w2store,
                                       build_retriever(s, offline=True), audit, 1_000_000, store))
    return TestClient(app), store


def test_ingest_and_retrieve_each_leave_a_trace_with_counts_only(tmp_path):
    # guards: ingestion and retrieval being invisible to latency, outcome and extraction-rate metrics
    from tests.test_w2_api_unit import call, upload_payload
    from tests.w2_helpers import PATIENT

    c, store = _traced_client(tmp_path)
    assert call(c, "POST", "/v1/w2/documents", upload_payload()).status_code == 200
    assert call(c, "POST", "/v1/w2/retrieve", {"patient_uuid": PATIENT, "question": "HbA1c target diabetes"}).status_code == 200
    assert call(c, "POST", "/v1/w2/documents", upload_payload(patient="99999999-2222-3333-4444-555555555555")).status_code == 403
    rows = store.query("SELECT flow, outcome FROM traces ORDER BY id")
    assert rows == [("w2_ingest", "extracted"), ("w2_retrieve", "hit"), ("w2_ingest", "denied")]
    attrs = store.query("SELECT name, attrs FROM spans WHERE name IN ('ingest','retrieve') AND attrs IS NOT NULL")
    import json as _json
    parsed = {n: _json.loads(a) for n, a in attrs}
    assert parsed["ingest"]["fields_total"] == 5 and parsed["ingest"]["fields_supported"] >= 1
    assert parsed["retrieve"]["hits"] >= 1
    assert all(isinstance(v, (int, float)) for d in parsed.values() for v in d.values())
