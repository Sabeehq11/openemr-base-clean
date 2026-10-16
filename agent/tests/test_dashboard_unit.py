import base64

from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.config import Settings
from app.main import create_app


def client(tmp_path, token="dash-token", audit_key="k" * 32):
    s = Settings(data_dir=str(tmp_path), dashboard_token=SecretStr(token), audit_hmac_key=SecretStr(audit_key),
                 module_signing_secret=SecretStr("s"), llm_provider="stub", w2_enabled=False)  # .env may enable W2
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


# --- load numbers and auto-refresh -------------------------------------------------------------------------------

def test_dashboard_json_reports_inflight_and_shed(tmp_path):
    s = Settings(data_dir=str(tmp_path), dashboard_token=SecretStr("t"), audit_hmac_key=SecretStr("k" * 32),
                 module_signing_secret=SecretStr("s"), llm_provider="stub", max_inflight_chats=7)
    app = create_app(s)
    app.state.load.inflight, app.state.load.shed = 3, 5
    j = TestClient(app).get("/dashboard.json", headers=basic("t")).json()
    assert j["load"] == {"inflight": 3, "shed": 5, "cap": 7}


def test_dashboard_html_shows_load_cards_with_cap(tmp_path):
    html = client(tmp_path).get("/dashboard", headers=basic("dash-token")).text
    assert "In flight now (no cap)" in html and "Shed since start" in html


def test_dashboard_refreshes_every_15_seconds_by_default(tmp_path):
    html = client(tmp_path).get("/dashboard", headers=basic("dash-token")).text
    assert "<meta http-equiv=refresh content=15>" in html and "Refreshes every 15 s" in html


def test_dashboard_refresh_is_configurable_clamped_and_can_be_off(tmp_path):
    c = client(tmp_path)
    get = lambda q: c.get(f"/dashboard?{q}", headers=basic("dash-token")).text  # noqa: E731
    assert "content=60>" in get("refresh=60")
    assert "content=5>" in get("refresh=1")  # floor, so the page cannot hammer the agent
    assert "content=300>" in get("refresh=99999")
    off = get("refresh=0")
    assert "http-equiv=refresh" not in off and "Auto-refresh off" in off


def test_refresh_never_bypasses_auth_and_json_does_not_refresh(tmp_path):
    c = client(tmp_path)
    assert c.get("/dashboard?refresh=5").status_code == 401  # the reload carries the same Basic credentials
    assert "http-equiv" not in c.get("/dashboard.json", headers=basic("dash-token")).text


def test_dashboard_shows_counts_only_no_identifiers(tmp_path):
    c = client(tmp_path)
    c.app.state.load.inflight = 1
    body = c.get("/dashboard.json", headers=basic("dash-token")).text
    assert set(c.get("/dashboard.json", headers=basic("dash-token")).json()["load"]) == {"inflight", "shed", "cap"}
    assert "uuid" not in body.lower()


def test_dashboard_json_has_a_w2_section_only_when_w2_is_enabled(tmp_path):
    # guards: Week 2 cards or alerts leaking into the Week 1 dashboard payload
    auth = basic("dash-token")
    off = client(tmp_path / "off").get("/dashboard.json", headers=auth).json()
    assert "w2" not in off and len(off["alerts"]) == 3
    s = Settings(data_dir=str(tmp_path / "on"), dashboard_token=SecretStr("dash-token"), w2_enabled=True,
                 w2_use_stub_vlm=True, audit_hmac_key=SecretStr("k" * 32), module_signing_secret=SecretStr("s"),
                 llm_provider="stub")
    c = TestClient(create_app(s))
    on = c.get("/dashboard.json", headers=auth).json()
    assert {"has_data", "ingestions", "retrieval_hit_rate", "eval"} <= set(on["w2"]) and len(on["alerts"]) == 6
    assert "Week 2" in c.get("/dashboard", headers=auth).text


def test_deployed_dashboard_says_eval_regression_is_enforced_in_ci(tmp_path, monkeypatch):
    # guards: a green "ok" for w2_eval_regression in an image that has no eval files to compare
    from pathlib import Path

    from app import metrics

    gone = Path(tmp_path / "absent.json")
    monkeypatch.setattr(metrics, "EVAL_RESULTS", gone)
    monkeypatch.setattr(metrics, "EVAL_BASELINE", gone)
    monkeypatch.setattr(metrics.eval_status, "__defaults__", (gone, gone))
    monkeypatch.setattr(metrics.evaluate_w2_alerts, "__defaults__", (None, gone, gone))
    s = Settings(data_dir=str(tmp_path / "d"), dashboard_token=SecretStr("dash-token"), w2_enabled=True,
                 w2_use_stub_vlm=True, audit_hmac_key=SecretStr("k" * 32), module_signing_secret=SecretStr("s"),
                 llm_provider="stub")
    c = TestClient(create_app(s))
    page = c.get("/dashboard", headers=basic("dash-token")).text
    assert "n/a (CI)" in page and "enforced by CI" in page
    row = next(a for a in c.get("/dashboard.json", headers=basic("dash-token")).json()["alerts"]
               if a["name"] == "w2_eval_regression")
    assert row["firing"] is False and row["samples"] == 0
