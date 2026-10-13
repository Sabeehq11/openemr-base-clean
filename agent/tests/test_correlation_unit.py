"""One correlation ID follows a request through every step and every outbound call.

Guards against: a trace that cannot be rebuilt from logs (tool, LLM and verifier steps with no ID), and outbound calls
to OpenEMR or the model provider that drop the ID.
"""
import json
import logging

import httpx
from fastapi.testclient import TestClient
from pydantic import SecretStr

from app import signing, tracing
from app.authz import PatientAuthorizer
from app.config import Settings
from app.fhir_client import FhirClient
from app.llm import OpenRouterClient
from app.logging_setup import correlation_id_var, outbound_headers
from app.main import create_app

USER = "aaaaaaaa-1111-2222-3333-444444444444"
PID = "11111111-2222-3333-4444-555555555555"
CID = "cid-corr-test-0001"


def _settings(tmp_path=None, **kw):
    return Settings(data_dir=str(tmp_path or "."), openemr_base_url="http://oe", openrouter_api_key=SecretStr("k"),
                    oauth_client_secret=SecretStr("s"), **kw)


def test_outbound_headers_carry_the_current_id_and_keep_existing_headers():
    token = correlation_id_var.set(CID)
    try:
        assert outbound_headers({"Authorization": "Bearer x"}) == {"Authorization": "Bearer x", "x-correlation-id": CID}
    finally:
        correlation_id_var.reset(token)


def test_outbound_headers_add_nothing_outside_a_request():
    assert "x-correlation-id" not in outbound_headers({"a": "b"})


async def test_fhir_search_and_read_send_the_id():
    seen: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        if "/Patient/" in req.url.path:
            return httpx.Response(200, json={"resourceType": "Patient", "id": PID})
        return httpx.Response(200, json={"resourceType": "Bundle", "entry": []})

    client = FhirClient(_settings(), transport=httpx.MockTransport(handler))
    token = correlation_id_var.set(CID)
    try:
        await client.search("Condition", "tok", {"patient": PID})
        await client.read("Patient", PID, "tok")
    finally:
        correlation_id_var.reset(token)
    assert len(seen) == 2 and all(r.headers["x-correlation-id"] == CID for r in seen)


async def test_authorization_calls_send_the_id():
    seen: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        if req.url.path.endswith("/introspect"):
            return httpx.Response(200, json={"active": True, "sub": USER, "scope": "user/Patient.rs user/CareTeam.rs"})
        return httpx.Response(200, json={"resourceType": "Bundle", "entry": []})

    authz = PatientAuthorizer(_settings(), transport=httpx.MockTransport(handler))
    token = correlation_id_var.set(CID)
    try:
        await authz.authorize("tok", PID, USER)
    finally:
        correlation_id_var.reset(token)
    assert len(seen) == 2 and all(r.headers["x-correlation-id"] == CID for r in seen)


async def test_llm_call_sends_the_id():
    seen: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        return httpx.Response(200, json={"choices": [{"message": {"content": "{}"}}], "usage": {}})

    llm = OpenRouterClient(_settings(llm_provider="openrouter"), transport=httpx.MockTransport(handler))
    token = correlation_id_var.set(CID)
    try:
        await llm.complete([{"role": "user", "content": "hi"}], [])
    finally:
        correlation_id_var.reset(token)
    assert seen[0].headers["x-correlation-id"] == CID
    assert seen[0].headers["authorization"] == "Bearer k"  # the key header is untouched


def test_span_emits_one_log_line_with_name_timing_and_status_only(caplog):
    caplog.set_level(logging.INFO, logger="agent.trace")
    _, tok = tracing.start(CID)
    try:
        with tracing.span("tool", "get_problems"):
            pass
        try:
            with tracing.span("llm", "complete"):
                raise RuntimeError("boom")
        except RuntimeError:
            pass
    finally:
        tracing.end(tok)
    fields = [r.fields for r in caplog.records if r.name == "agent.trace"]
    assert [(f["span_kind"], f["span_name"], f["status"]) for f in fields] == [("tool", "get_problems", "ok"),
                                                                              ("llm", "complete", "error")]
    assert all(set(f) == {"span_kind", "span_name", "ms", "status"} for f in fields)


def test_every_log_line_of_one_chat_has_the_same_id(tmp_path, capsys):
    """Runs a full chat and parses the JSON log lines the service printed."""
    from tests.test_chat_observability_unit import Authz, fhir  # same fakes as the trace test

    s = Settings(data_dir=str(tmp_path), audit_hmac_key=SecretStr("k" * 32), module_signing_secret=SecretStr("s"),
                 llm_provider="stub")
    app = create_app(s)  # installs the JSON handler on stdout
    app.state.gateway._clock = lambda: 1_800_000_000
    app.state.runner._authz = Authz(True)
    app.state.runner._fhir._transport = httpx.MockTransport(fhir)
    body = json.dumps({"patient_uuid": PID, "question": "brief", "conversation_id": None}).encode()
    nonce = "nonce-corr-0000000001"
    sig = signing.sign("s", "POST", "/v1/chat", "1800000000", nonce, body, USER, "tok")
    capsys.readouterr()
    r = TestClient(app).post("/v1/chat", content=body, headers={
        "x-copilot-timestamp": "1800000000", "x-copilot-nonce": nonce, "x-copilot-signature": sig,
        "x-copilot-user": USER, "x-copilot-token": "tok", "content-type": "application/json",
        "x-correlation-id": CID})
    assert r.status_code == 200 and "event: answer" in r.text
    lines = [json.loads(x) for x in capsys.readouterr().out.splitlines() if x.startswith("{")]
    # The test client's own httpx line ("POST http://testserver/...") is logged after the service finished the request,
    # so it belongs to the test harness and not to the service under test.
    lines = [x for x in lines if not (x["logger"] == "httpx" and "testserver" in x["msg"])]
    spans = [x for x in lines if x.get("logger") == "agent.trace"]
    assert {x["span_kind"] for x in spans} >= {"authz", "tool", "llm", "verifier"}
    assert lines and {x["correlation_id"] for x in lines} == {CID}
