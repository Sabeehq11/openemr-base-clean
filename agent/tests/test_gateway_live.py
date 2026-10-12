"""Signed requests through the whole app against live OpenEMR with real user tokens. Run with OE_LIVE=1."""
import json

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from app import signing
from app.config import Settings
from app.main import create_app

from .oauth_helper import BASE, get_token, load_env, load_fixture

pytestmark = pytest.mark.live

SECRET = "live-test-signing-secret"
SCOPES = ("openid fhirUser user/Patient.rs user/CareTeam.rs user/Condition.rs user/MedicationRequest.rs "
          "user/AllergyIntolerance.rs user/Observation.rs user/Encounter.rs")
_n = [0]


@pytest.fixture(scope="module")
def fx():
    return load_fixture()


@pytest.fixture(scope="module")
def client():
    env = load_env()
    s = Settings(openemr_base_url=BASE, openemr_verify_tls=False, tool_timeout_s=15, module_signing_secret=SecretStr(SECRET),
                 oauth_client_id=env["OAUTH_CLIENT_ID"], oauth_client_secret=env["OAUTH_CLIENT_SECRET"])
    return TestClient(create_app(s))


@pytest.fixture(scope="module")
def tokens():
    return {u: get_token(u, SCOPES)["access_token"] for u in ("copilot_doc_a", "copilot_doc_b", "copilot_nurse")}


def call(client, tool, user_uuid, token, patient, import_time=None):
    import time
    _n[0] += 1
    path = f"/v1/tools/{tool}"
    body = json.dumps({"patient_uuid": patient}).encode()
    ts, nonce = str(int(import_time or time.time())), f"live-nonce-{_n[0]:08d}"
    sig = signing.sign(SECRET, "POST", path, ts, nonce, body, user_uuid, token)
    return client.post(path, content=body, headers={
        "x-copilot-timestamp": ts, "x-copilot-nonce": nonce, "x-copilot-signature": sig,
        "x-copilot-user": user_uuid, "x-copilot-token": token, "content-type": "application/json"})


def test_physician_reads_own_patient(client, fx, tokens):
    r = call(client, "get_problems", fx["user_uuids"]["copilot_doc_a"], tokens["copilot_doc_a"], fx["patient_uuids"]["100"])
    assert r.status_code == 200 and not r.json()["empty"]


def test_physician_denied_other_physicians_patient_with_valid_signature(client, fx, tokens):
    r = call(client, "get_problems", fx["user_uuids"]["copilot_doc_a"], tokens["copilot_doc_a"], fx["patient_uuids"]["101"])
    assert r.status_code == 403 and "records" not in r.json()


def test_nurse_denied(client, fx, tokens):
    r = call(client, "get_allergies", fx["user_uuids"]["copilot_nurse"], tokens["copilot_nurse"], fx["patient_uuids"]["100"])
    assert r.status_code == 403


def test_signed_claim_of_another_user_with_my_token_is_denied(client, fx, tokens):
    """Module signs 'I am A' but forwards B's token: subject mismatch, no data."""
    r = call(client, "get_problems", fx["user_uuids"]["copilot_doc_a"], tokens["copilot_doc_b"], fx["patient_uuids"]["101"])
    assert r.status_code == 403


def test_unsigned_request_to_agent_is_refused(client, fx):
    r = client.post("/v1/tools/get_problems", json={"patient_uuid": fx["patient_uuids"]["100"]})
    assert r.status_code == 401
