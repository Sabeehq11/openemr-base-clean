import json
import time

import pytest

from app import cli
from app.audit import ChainedAuditSink
from app.config import get_settings
from app.tracing import Trace, TraceStore


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("AUDIT_HMAC_KEY", "k" * 32)
    get_settings.cache_clear()
    yield tmp_path
    get_settings.cache_clear()


def seed_traces(path, outcome, n=10, ms=500):
    store = TraceStore(f"{path}/traces.db")
    for _ in range(n):
        t = Trace(correlation_id="c" * 8, started=time.time() - ms / 1000)
        t.outcome = outcome
        store.save(t)


def test_alerts_exit_zero_when_clear(env, capsys):
    seed_traces(env, "answer")
    assert cli.main(["alerts"]) == 0
    assert "FIRING" not in capsys.readouterr().out


def test_alerts_exit_two_when_firing_and_json(env, capsys):
    seed_traces(env, "error")
    assert cli.main(["alerts", "--json"]) == 2
    out = json.loads(capsys.readouterr().out)
    assert {a["name"] for a in out if a["firing"]} == {"high_error_rate"}


def test_verify_and_export_audit(env, capsys):
    sink = ChainedAuditSink(f"{env}/audit.db", "k" * 32)
    sink.record("tool_call", tool="get_labs", outcome="ok")
    assert cli.main(["verify-audit"]) == 0
    dest = env / "export.jsonl"
    assert cli.main(["export-audit", str(dest)]) == 0
    assert len(dest.read_text().splitlines()) == 1
    with pytest.raises(FileExistsError):  # never overwrite an earlier export
        cli.main(["export-audit", str(dest)])


def test_verify_audit_fails_on_tamper_and_missing_key(env, monkeypatch):
    sink = ChainedAuditSink(f"{env}/audit.db", "k" * 32)
    sink.record("tool_call", tool="get_labs", outcome="ok")
    sink._db.execute("UPDATE audit SET event = 'x'")
    sink._db.commit()
    assert cli.main(["verify-audit"]) == 3
    monkeypatch.setenv("AUDIT_HMAC_KEY", "")
    get_settings.cache_clear()
    assert cli.main(["verify-audit"]) == 3
