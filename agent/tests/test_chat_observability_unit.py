"""A chat request leaves one trace with spans, and every tool call leaves a chained audit row."""
import json

import httpx
from fastapi.testclient import TestClient
from pydantic import SecretStr

from app import signing
from app.authz import AuthzDecision, Reason
from app.config import Settings
from app.main import create_app

USER = "aaaaaaaa-1111-2222-3333-444444444444"
PID = "11111111-2222-3333-4444-555555555555"
_n = [0]


class Authz:
    def __init__(self, allow=True):
        self.allow = allow

    async def authorize(self, token, patient_uuid, claimed_user_uuid=None):
        if self.allow:
            return AuthzDecision(True, Reason.ALLOWED, USER, patient_uuid)
        return AuthzDecision(False, Reason.NOT_ON_CARE_TEAM, USER)


def fhir(request: httpx.Request) -> httpx.Response:
    res = request.url.path.rsplit("/", 1)[-1]
    if request.url.path.endswith("/Patient/" + PID):
        return httpx.Response(200, json={"resourceType": "Patient", "id": PID, "name": [{"family": "Test"}]})
    entry = [{"resource": {"resourceType": "Condition", "id": "c1", "code": {"text": "Asthma"}}}] if res == "Condition" else []
    return httpx.Response(200, json={"resourceType": "Bundle", "entry": entry})


def post(c, secret, question="brief"):
    _n[0] += 1
    body = json.dumps({"patient_uuid": PID, "question": question, "conversation_id": None}).encode()
    ts, nonce = "1800000000", f"nonce-{_n[0]:020d}"
    sig = signing.sign(secret, "POST", "/v1/chat", ts, nonce, body, USER, "tok")
    h = {"x-copilot-timestamp": ts, "x-copilot-nonce": nonce, "x-copilot-signature": sig,
         "x-copilot-user": USER, "x-copilot-token": "tok", "content-type": "application/json"}
    return c.post("/v1/chat", content=body, headers=h)


def build(tmp_path, allow=True):
    s = Settings(data_dir=str(tmp_path), audit_hmac_key=SecretStr("k" * 32), module_signing_secret=SecretStr("s"),
                 llm_provider="stub", dashboard_token=SecretStr("d"))
    app = create_app(s)
    app.state.gateway._clock = lambda: 1_800_000_000
    runner = app.state.runner
    runner._authz = Authz(allow)
    runner._fhir._transport = httpx.MockTransport(fhir)
    return TestClient(app), app


def test_answer_request_leaves_trace_spans_and_chained_audit(tmp_path):
    c, app = build(tmp_path)
    r = post(c, "s")
    assert r.status_code == 200 and "event: answer" in r.text
    traces = app.state.traces.query("SELECT outcome, latency_ms, claims_kept FROM traces")
    assert len(traces) == 1 and traces[0][0] == "answer" and traces[0][1] > 0 and traces[0][2] >= 1
    kinds = {k for (k,) in app.state.traces.query("SELECT kind FROM spans")}
    assert {"authz", "tool", "llm", "verifier"} <= kinds
    rows = app.state.audit_chain.rows()
    assert any(x["event"] == "tool_call" and x["tool"] == "get_problems" for x in rows)
    assert any(x["event"] == "chat_finished" and x["outcome"] == "answer" for x in rows)
    assert app.state.audit_chain.verify().ok


def test_denied_request_is_traced_and_audited(tmp_path):
    c, app = build(tmp_path, allow=False)
    assert "event: denied" in post(c, "s").text
    assert app.state.traces.query("SELECT outcome FROM traces") == [("denied",)]
    events = [x["event"] for x in app.state.audit_chain.rows()]
    assert "tool_denied" in events and "chat_finished" in events
    assert app.state.audit_chain.verify().ok


def test_rejected_signature_is_audited_without_a_trace(tmp_path):
    c, app = build(tmp_path)
    assert post(c, "wrong-secret").status_code == 401
    assert [x["event"] for x in app.state.audit_chain.rows()] == ["gateway_rejected"]
    assert app.state.traces.query("SELECT count(*) FROM traces") == [(0,)]


def test_audit_rows_hold_no_clinical_text_or_tokens(tmp_path):
    c, app = build(tmp_path)
    post(c, "s")
    dump = json.dumps(app.state.audit_chain.rows())
    assert "Asthma" not in dump and "tok" not in dump.replace("tool", "")
