import base64

from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.config import Settings
from app.main import create_app


def client(tmp_path, token="dash-token", audit_key="k" * 32):
    s = Settings(data_dir=str(tmp_path), dashboard_token=SecretStr(token), audit_hmac_key=SecretStr(audit_key),
                 module_signing_secret=SecretStr("s"), llm_provider="stub")
    return TestClient(create_app(s))


def basic(pw):
    return {"authorization": "Basic " + base64.b64encode(f"ops:{pw}".encode()).decode()}


def test_dashboard_is_404_when_no_token_configured(tmp_path):
    c = client(tmp_path, token="")
    assert c.get("/dashboard").status_code == 404
    assert c.get("/dashboard.json", headers=basic("")).status_code == 404


def test_dashboard_requires_the_token(tmp_path):
    c = client(tmp_path)
    assert c.get("/dashboard").status_code == 401
    assert c.get("/dashboard", headers=basic("wrong")).status_code == 401
    assert c.get("/dashboard", headers={"authorization": "Basic !!!notbase64"}).status_code == 401
    assert c.get("/dashboard", headers={"authorization": "Bearer dash-token"}).status_code == 401


def test_dashboard_renders_with_token_and_shows_chain_state(tmp_path):
    c = client(tmp_path)
    r = c.get("/dashboard", headers=basic("dash-token"))
    assert r.status_code == 200 and "Audit chain" in r.text and "intact" in r.text
    assert "no-store" in r.headers["cache-control"]
    j = c.get("/dashboard.json", headers=basic("dash-token")).json()
    assert j["audit_chain"]["ok"] and len(j["alerts"]) == 3


def test_dashboard_reports_a_broken_chain(tmp_path):
    c = client(tmp_path)
    chain = c.app.state.audit_chain
    chain.record("tool_call", tool="get_labs", patient_uuid="p", outcome="ok")
    chain._db.execute("UPDATE audit SET body = '{}' WHERE seq = 1")
    chain._db.commit()
    assert "BROKEN" in c.get("/dashboard", headers=basic("dash-token")).text
    assert c.get("/ready").json()["checks"]["audit_chain"]["ok"] is False


def test_ready_fails_without_audit_key(tmp_path):
    c = client(tmp_path, audit_key="")
    body = c.get("/ready").json()
    assert body["checks"]["audit_chain"]["ok"] is False
    assert c.get("/dashboard", headers=basic("dash-token")).status_code == 404  # no chain, no dashboard


def test_dashboard_html_escapes_values(tmp_path):
    from app.dashboard import render
    data = {"window_s": 3600, "requests": 0, "errors": 0, "p50_ms": None, "p95_ms": None, "tool_calls": 0,
            "tool_failures": 0, "retries": 0, "verification_pass_rate": None, "claims_dropped": 0,
            "prompt_tokens": 0, "completion_tokens": 0, "cost_usd": 0.0, "outcomes": {"<script>": 1},
            "tools": {"<b>x": {"calls": 1, "failures": 0, "p95_ms": 1.0}}}
    html = render(data, [], {"ok": True, "rows": 0})
    assert "<script>" not in html and "&lt;script&gt;" in html and "<b>x" not in html
