"""Foundation tests: liveness vs readiness, correlation IDs, log redaction.

Guards against: /ready returning 200 unconditionally; log lines without a correlation ID; PHI or secrets in logs.
"""
import json
import logging

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.config import Settings
from app.logging_setup import JsonFormatter, correlation_id_var, redact
from app.main import create_app


def make_client(tmp_path, **kw):
    s = Settings(data_dir=str(tmp_path), llm_provider="stub", openemr_base_url="http://127.0.0.1:9", **kw)
    return TestClient(create_app(s))


def test_health_is_independent_of_dependencies(tmp_path):
    assert make_client(tmp_path).get("/health").json() == {"status": "ok"}


def test_ready_fails_when_openemr_unreachable(tmp_path):
    r = make_client(tmp_path).get("/ready")
    assert r.status_code == 503
    body = r.json()
    assert body["status"] == "not_ready"
    assert body["checks"]["openemr"]["ok"] is False


def test_ready_ok_when_all_dependencies_ok(tmp_path, monkeypatch):
    async def fake_get(self, url, **kw):
        return httpx.Response(200, request=httpx.Request("GET", url))

    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)
    # The audit chain needs a key. Supply a synthetic one so the test never depends on a developer's git-ignored .env.
    r = make_client(tmp_path, audit_hmac_key=SecretStr("test-only-audit-key-not-a-secret")).get("/ready")
    assert r.status_code == 200
    assert all(c["ok"] for c in r.json()["checks"].values())


def test_ready_fails_when_openrouter_key_missing(tmp_path, monkeypatch):
    async def fake_get(self, url, **kw):
        return httpx.Response(200, request=httpx.Request("GET", url))

    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)
    # Explicit empty key: Settings also reads agent/.env, so a developer's real key must not leak into this test.
    s = Settings(data_dir=str(tmp_path), llm_provider="openrouter", openemr_base_url="http://x",
                 openrouter_api_key=SecretStr(""))
    r = TestClient(create_app(s)).get("/ready")
    assert r.status_code == 503
    assert r.json()["checks"]["llm_provider"]["ok"] is False


def test_correlation_id_generated_and_echoed(tmp_path):
    c = make_client(tmp_path)
    r = c.get("/health")
    assert len(r.headers["x-correlation-id"]) == 36
    r2 = c.get("/health", headers={"x-correlation-id": "abc-12345678"})
    assert r2.headers["x-correlation-id"] == "abc-12345678"


def test_malformed_correlation_id_is_replaced(tmp_path):
    r = make_client(tmp_path).get("/health", headers={"x-correlation-id": "bad id\nwith injection"})
    assert r.headers["x-correlation-id"] != "bad id\nwith injection"


def test_every_log_line_has_correlation_id():
    token = correlation_id_var.set("cid-test-0001")
    rec = logging.LogRecord("t", logging.INFO, "f", 1, "hello", None, None)
    assert json.loads(JsonFormatter().format(rec))["correlation_id"] == "cid-test-0001"
    correlation_id_var.reset(token)


@pytest.mark.parametrize(
    "raw",
    ["Authorization: Bearer abc.def.ghi", "ssn 123-45-6789", "mail a.b@example.com", "call 512-555-0101", "sk-or-v1-abcdefghijkl"],
)
def test_redaction_removes_sensitive_values(raw):
    out = redact(raw)
    assert "abc.def.ghi" not in out and "123-45-6789" not in out
    assert "a.b@example.com" not in out and "512-555-0101" not in out and "abcdefghijkl" not in out


def test_redaction_of_sensitive_keys():
    assert redact({"access_token": "x", "ok": "y"}) == {"access_token": "[REDACTED]", "ok": "y"}


def _w2_client(tmp_path, monkeypatch, **kw):
    async def fake_get(self, url, **k):
        return httpx.Response(200, request=httpx.Request("GET", url))

    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)
    s = Settings(data_dir=str(tmp_path), openemr_base_url="http://x", llm_provider="stub", w2_enabled=True,
                 w2_use_stub_vlm=True,
                 audit_hmac_key=SecretStr("test-only-audit-key-not-a-secret"), **kw)
    return TestClient(create_app(s))


def test_ready_is_unchanged_without_w2(tmp_path, monkeypatch):
    # guards: the Week 1 /ready body changing when W2 is off
    async def fake_get(self, url, **kw):
        return httpx.Response(200, request=httpx.Request("GET", url))

    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)
    body = make_client(tmp_path, w2_enabled=False,  # agent/.env may switch W2 on
                       audit_hmac_key=SecretStr("test-only-audit-key-not-a-secret")).get("/ready").json()
    assert set(body) == {"status", "checks"} and body["status"] == "ready"


def test_ready_reports_w2_checks_without_calling_a_provider(tmp_path, monkeypatch):
    # guards: /ready spending money or hanging on a provider probe (the stubbed GET would be the only network call)
    r = _w2_client(tmp_path, monkeypatch).get("/ready")
    assert r.status_code == 200 and r.json()["status"] == "ready"
    assert set(r.json()["w2_checks"]) == {"document_store", "vector_index", "reranker"}


def test_ready_is_degraded_not_503_when_only_w2_is_impaired(tmp_path, monkeypatch):
    # guards: a Week 2 outage (here: a live reranker with no key) taking Week 1 out of the load balancer
    c = _w2_client(tmp_path, monkeypatch, openrouter_api_key=SecretStr(""))
    c.app.state.settings.w2_use_stub_vlm = False
    r = c.get("/ready")
    assert r.status_code == 200 and r.json()["status"] == "degraded"
    assert r.json()["w2_checks"]["reranker"]["ok"] is False


def test_ready_is_not_ready_when_week1_fails_even_if_w2_is_fine(tmp_path, monkeypatch):
    # guards: degraded masking a real Week 1 outage
    c = _w2_client(tmp_path, monkeypatch)

    async def down(self, url, **k):
        raise httpx.ConnectError("down")

    monkeypatch.setattr(httpx.AsyncClient, "get", down)
    r = c.get("/ready")
    assert r.status_code == 503 and r.json()["status"] == "not_ready"
