import json

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr

from app import signing
from app.api import build_router
from app.authz import AuthzDecision, Reason
from app.config import Settings
from app.fhir_client import FhirClient
from app.gateway import Gateway
from app.tools import ToolRunner

SECRET = "test-signing-secret"
USER = "aaaaaaaa-1111-2222-3333-444444444444"
PID = "11111111-2222-3333-4444-555555555555"
NOW = 1_800_000_000


class Audit:
    def __init__(self):
        self.events = []

    def record(self, event, **fields):
        self.events.append((event, fields))


class FakeAuthz:
    def __init__(self, allow=True):
        self.allow, self.calls = allow, []

    async def authorize(self, token, patient_uuid, claimed_user_uuid=None):
        self.calls.append((token, patient_uuid, claimed_user_uuid))
        if self.allow:
            return AuthzDecision(True, Reason.ALLOWED, USER, patient_uuid)
        return AuthzDecision(False, Reason.NOT_ON_CARE_TEAM, USER)


def build(allow=True, secret=SECRET, fhir_status=200):
    audit, authz = Audit(), FakeAuthz(allow)
    s = Settings(module_signing_secret=SecretStr(secret), openemr_base_url="http://oe")
    body = {"resourceType": "Bundle", "entry": [{"resource": {"resourceType": "Condition", "id": "c1",
                                                             "code": {"text": "HTN"}}}]}
    fhir = FhirClient(s, transport=httpx.MockTransport(lambda r: httpx.Response(fhir_status, json=body)))
    gw = Gateway(s, audit, clock=lambda: NOW)
    app = FastAPI()
    app.include_router(build_router(gw, ToolRunner(authz, fhir, audit=audit)))
    return TestClient(app), audit, authz


def signed(path="/v1/tools/get_problems", payload=None, secret=SECRET, ts=NOW, nonce="nonce-0123456789ab", user=USER,
           token="user-token", sign_token=None, sign_body=None):
    body = json.dumps(payload if payload is not None else {"patient_uuid": PID}).encode()
    sig = signing.sign(secret, "POST", path, str(ts), nonce, sign_body if sign_body is not None else body, user,
                       sign_token or token)
    headers = {"x-copilot-timestamp": str(ts), "x-copilot-nonce": nonce, "x-copilot-signature": sig,
               "x-copilot-user": user, "x-copilot-token": token, "content-type": "application/json"}
    return path, body, headers


def post(client, **kw):
    path, body, headers = signed(**kw)
    return client.post(path, content=body, headers=headers)


def test_valid_signed_request_runs_tool_and_is_audited():
    c, audit, _ = build()
    r = post(c)
    assert r.status_code == 200 and r.json()["records"][0]["title"] == "HTN"
    assert [e for e, _ in audit.events] == ["tool_call"]


def test_authorization_receives_the_token_and_claimed_user():
    c, _, authz = build()
    post(c)
    assert authz.calls == [("user-token", PID, USER)]


def test_valid_signature_does_not_grant_access():
    c, audit, _ = build(allow=False)
    r = post(c)
    assert r.status_code == 403 and r.json() == {"error": "forbidden", "message": "You do not have access to this patient."}
    assert audit.events[0][0] == "tool_denied" and audit.events[0][1]["outcome"] == "not_on_care_team"


@pytest.mark.parametrize("kw", [
    {"secret": "wrong-secret"},
    {"ts": NOW - 61},
    {"ts": NOW + 61},
    {"sign_token": "a-different-token"},
    {"sign_body": b'{"patient_uuid": "someone-else"}'},
])
def test_bad_signature_or_stale_timestamp_is_rejected_generically(kw):
    c, audit, authz = build()
    r = post(c, **kw)
    assert r.status_code == 401 and r.json()["detail"]["error"] == "unauthenticated"
    assert authz.calls == []
    assert audit.events[0][0] == "gateway_rejected"


def test_replayed_nonce_is_rejected():
    c, _, authz = build()
    assert post(c).status_code == 200
    assert post(c).status_code == 401
    assert len(authz.calls) == 1


def test_forged_request_does_not_burn_a_nonce():
    c, _, _ = build()
    assert post(c, secret="wrong").status_code == 401
    assert post(c).status_code == 200


@pytest.mark.parametrize("drop", ["x-copilot-timestamp", "x-copilot-nonce", "x-copilot-signature",
                                  "x-copilot-user", "x-copilot-token"])
def test_missing_header_is_rejected(drop):
    c, _, authz = build()
    path, body, headers = signed()
    headers.pop(drop)
    assert c.post(path, content=body, headers=headers).status_code == 401 and authz.calls == []


def test_unconfigured_secret_fails_closed():
    c, audit, _ = build(secret="")
    assert post(c, secret="").status_code == 401
    assert audit.events[0][1]["why"] == "signing_not_configured"


def test_malformed_user_or_nonce_headers_rejected():
    c, _, _ = build()
    assert post(c, user="not a uuid!").status_code == 401
    assert post(c, nonce="short").status_code == 401


def test_unsigned_request_is_rejected():
    c, _, _ = build()
    assert c.post("/v1/tools/get_problems", json={"patient_uuid": PID}).status_code == 401


def test_path_is_bound_into_the_signature():
    c, _, _ = build()
    path, body, headers = signed(path="/v1/tools/get_problems")
    assert c.post("/v1/tools/get_labs", content=body, headers=headers).status_code == 401


def test_unknown_tool_and_bad_payloads_are_400_after_verification():
    c, _, _ = build()
    assert post(c, path="/v1/tools/drop_table", nonce="nonce-aaaaaaaaaaaa").status_code == 400
    assert post(c, payload={"nope": 1}, nonce="nonce-cccccccccccc").status_code == 400
    assert post(c, payload={"patient_uuid": 5}, nonce="nonce-bbbbbbbbbbbb").status_code == 400


def test_upstream_failure_is_a_generic_502_without_internal_text():
    c, audit, _ = build(fhir_status=500)
    r = post(c)
    assert r.status_code == 502 and r.json()["error"] == "upstream_error"
    assert audit.events[-1][0] == "tool_error"


def test_signing_verify_is_constant_time_api_and_roundtrips():
    sig = signing.sign("k", "POST", "/p", "1", "n", b"b", "u", "t")
    assert signing.verify(sig, "k", "POST", "/p", "1", "n", b"b", "u", "t")
    assert not signing.verify(sig, "k", "POST", "/p", "1", "n", b"b2", "u", "t")


def test_signing_known_vector_for_php_parity():
    """The PHP module must produce this exact value (see the module's isolated test)."""
    sig = signing.sign("secret", "POST", "/v1/tools/get_problems", "1800000000", "nonce-0123456789ab",
                       b'{"patient_uuid":"p"}', "user-uuid", "tok")
    assert sig == "cf88ef48d4086ff34f1c1477a273b6f76b4e97b2d51d7a24e1c351d0048700ca"
