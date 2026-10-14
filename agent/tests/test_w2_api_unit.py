"""Week 2 API: signing, authorization, the W2_ENABLED switch, upload to retrieve, and log hygiene."""
import base64
import json
import logging

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from app import signing
from app.authz import AuthzDecision, Reason
from app.config import Settings
from app.gateway import Gateway
from app.main import create_app
from app.w2.api import build_w2_router
from app.w2.rag import build_retriever
from tests.w2_helpers import OTHER, PATIENT, fx, make_ingestor
from fastapi import FastAPI

SECRET = "test-signing-secret"
USER = "aaaaaaaa-1111-2222-3333-444444444444"
NOW = 1_800_000_000


class Audit:
    def __init__(self):
        self.events = []

    def record(self, event, **fields):
        self.events.append((event, fields))


class Authz:
    """Allows only PATIENT, as the care-team check would."""

    async def authorize(self, token, patient_uuid, claimed_user_uuid=None):
        if patient_uuid == PATIENT:
            return AuthzDecision(True, Reason.ALLOWED, USER, patient_uuid)
        return AuthzDecision(False, Reason.NOT_ON_CARE_TEAM, USER)


def build(tmp_path):
    s = Settings(module_signing_secret=SecretStr(SECRET), data_dir=str(tmp_path))
    audit = Audit()
    ingestor, store = make_ingestor(tmp_path)
    app = FastAPI()
    app.include_router(build_w2_router(Gateway(s, audit, clock=lambda: NOW), Authz(), ingestor, store,
                                       build_retriever(s, offline=True), audit, 1_000_000))
    return TestClient(app), audit, store


_n = [0]


def call(client, method, url, payload=None, secret=SECRET):
    _n[0] += 1
    nonce = f"nonce-{_n[0]:012d}"
    body = json.dumps(payload).encode() if payload is not None else b""
    path = url.split("?")[0]  # the gateway signs the path, never the query string
    sig = signing.sign(secret, method, path, str(NOW), nonce, body, USER, "tok")
    headers = {"x-copilot-timestamp": str(NOW), "x-copilot-nonce": nonce, "x-copilot-signature": sig,
               "x-copilot-user": USER, "x-copilot-token": "tok", "content-type": "application/json"}
    return client.request(method, url, content=body, headers=headers)


def upload_payload(name="lab_report.pdf", doc_id=7, patient=PATIENT, doc_type="lab_pdf"):
    return {"patient_uuid": patient, "document_id": doc_id, "doc_type": doc_type,
            "content_b64": base64.b64encode(fx(name)).decode()}


def test_upload_extracts_cited_rows_and_audits_ids_only(tmp_path):
    c, audit, _ = build(tmp_path)
    r = call(c, "POST", "/v1/w2/documents", upload_payload())
    assert r.status_code == 200 and r.json()["duplicate"] is False
    rows = r.json()["document"]["lab"]["rows"]
    assert len(rows) == 5 and all(x["citation"]["bbox"] for x in rows if x["support"] == "supported")
    assert "7.9" not in json.dumps(audit.events)


def test_repeat_upload_is_a_duplicate_with_one_row(tmp_path):
    c, _, store = build(tmp_path)
    call(c, "POST", "/v1/w2/documents", upload_payload())
    r = call(c, "POST", "/v1/w2/documents", upload_payload(doc_id=8))
    assert r.json()["duplicate"] is True and store.get(PATIENT, 8) is None


def test_unsigned_and_wrong_secret_and_tampered_body_are_401(tmp_path):
    c, _, _ = build(tmp_path)
    assert c.post("/v1/w2/documents", json=upload_payload()).status_code == 401
    assert call(c, "POST", "/v1/w2/documents", upload_payload(), secret="nope").status_code == 401
    body = json.dumps(upload_payload()).encode()
    sig = signing.sign(SECRET, "POST", "/v1/w2/documents", str(NOW), "nonce-tamper000001", b"{}", USER, "tok")
    h = {"x-copilot-timestamp": str(NOW), "x-copilot-nonce": "nonce-tamper000001", "x-copilot-signature": sig,
         "x-copilot-user": USER, "x-copilot-token": "tok"}
    assert c.post("/v1/w2/documents", content=body, headers=h).status_code == 401


def test_another_patients_upload_gets_the_generic_denial_and_nothing_is_stored(tmp_path):
    c, audit, store = build(tmp_path)
    r = call(c, "POST", "/v1/w2/documents", upload_payload(patient=OTHER))
    assert r.status_code == 403 and r.json() == {"error": "forbidden",
                                                  "message": "You do not have access to this patient."}
    assert store.get(OTHER, 7) is None and audit.events[-1][0] == "w2_denied"


def test_reading_another_patients_document_is_denied_not_leaked(tmp_path):
    c, _, _ = build(tmp_path)
    call(c, "POST", "/v1/w2/documents", upload_payload())
    assert call(c, "GET", f"/v1/w2/documents/7?patient_uuid={OTHER}").status_code == 403
    ok = call(c, "GET", f"/v1/w2/documents/7?patient_uuid={PATIENT}")
    assert ok.status_code == 200 and ok.json()["document"]["document_id"] == 7
    assert call(c, "GET", f"/v1/w2/documents/999?patient_uuid={PATIENT}").status_code == 404


def test_links_are_stored_once_and_only_for_supported_rows(tmp_path):
    c, _, store = build(tmp_path)
    call(c, "POST", "/v1/w2/documents", upload_payload())
    r = call(c, "POST", "/v1/w2/documents/7/links", {"patient_uuid": PATIENT, "links": {"0": "uuid-a", "1": "uuid-b"}})
    assert r.json() == {"updated": 2}
    again = call(c, "POST", "/v1/w2/documents/7/links", {"patient_uuid": PATIENT, "links": {"0": "uuid-z"}})
    assert again.json() == {"updated": 0}  # a written link is never overwritten
    assert store.get(PATIENT, 7).links["0"] == "uuid-a"
    assert call(c, "POST", "/v1/w2/documents/7/links",
                {"patient_uuid": OTHER, "links": {"0": "x"}}).status_code == 403


def test_bad_inputs_are_rejected_cleanly(tmp_path):
    c, _, _ = build(tmp_path)
    p = upload_payload()
    assert call(c, "POST", "/v1/w2/documents", {**p, "extra": 1}).status_code == 400
    assert call(c, "POST", "/v1/w2/documents", {**p, "content_b64": "***"}).status_code == 400
    assert call(c, "POST", "/v1/w2/documents", upload_payload("wrong_type.pdf")).status_code == 415
    assert call(c, "POST", "/v1/w2/documents", {**p, "doc_type": "xray"}).status_code == 400


def test_retrieve_returns_cited_snippet_and_empty_for_off_topic(tmp_path):
    c, _, _ = build(tmp_path)
    ok = call(c, "POST", "/v1/w2/retrieve", {"patient_uuid": PATIENT, "question": "A1c goal for diabetes"})
    assert ok.status_code == 200 and ok.json()["status"] == "ok" and ok.json()["snippets"][0]["publisher"]
    none = call(c, "POST", "/v1/w2/retrieve", {"patient_uuid": PATIENT, "question": "pizza toppings"})
    assert none.json()["status"] == "empty"
    assert call(c, "POST", "/v1/w2/retrieve", {"patient_uuid": OTHER, "question": "A1c"}).status_code == 403


def test_logs_contain_no_fixture_values(tmp_path, caplog):
    c, _, _ = build(tmp_path)
    with caplog.at_level(logging.DEBUG):
        call(c, "POST", "/v1/w2/documents", upload_payload())
        call(c, "POST", "/v1/w2/documents", upload_payload("intake_form.pdf", 8, doc_type="intake_form"))
    text = caplog.text
    for secret_value in ("7.9", "Hemoglobin", "1.8 mg/dL", PATIENT[:8] + "x"):
        assert secret_value not in text


def test_routes_do_not_exist_when_w2_is_disabled(tmp_path):
    s = Settings(data_dir=str(tmp_path), w2_enabled=False)
    c = TestClient(create_app(s))
    assert c.post("/v1/w2/documents", json={}).status_code == 404
    assert c.post("/v1/w2/retrieve", json={}).status_code == 404


def test_routes_exist_when_enabled_and_still_require_a_signature(tmp_path):
    s = Settings(data_dir=str(tmp_path), w2_enabled=True, w2_use_stub_vlm=True,
                 module_signing_secret=SecretStr(SECRET))
    c = TestClient(create_app(s))
    assert c.post("/v1/w2/documents", json={}).status_code == 401
    assert c.get("/health").status_code == 200


def _row_counts(tmp_path):
    import sqlite3
    c = sqlite3.connect(str(tmp_path / "w2.db"))
    return [c.execute(f"select count(*) from {t}").fetchone()[0] for t in ("w2_documents", "w2_facts", "w2_citations")]


def test_preflight_allows_a_valid_file_and_stores_nothing(tmp_path):
    c, audit, store = build(tmp_path)
    r = call(c, "POST", "/v1/w2/preflight", {k: v for k, v in upload_payload().items() if k != "document_id"})
    assert r.status_code == 200 and r.json() == {"ok": True}
    assert _row_counts(tmp_path) == [0, 0, 0]  # preflight never writes an extraction


def test_preflight_denies_another_patient_with_403_and_stores_nothing(tmp_path):
    """Guards an unauthorized upload reaching OpenEMR: the module asks this before it writes any document."""
    c, audit, store = build(tmp_path)
    r = call(c, "POST", "/v1/w2/preflight", {k: v for k, v in upload_payload(patient=OTHER).items() if k != "document_id"})
    assert r.status_code == 403 and r.json()["error"] == "forbidden"
    assert _row_counts(tmp_path) == [0, 0, 0] and audit.events[-1][0] == "w2_denied"


def test_preflight_rejects_bad_files_before_anything_is_stored(tmp_path):
    c, audit, store = build(tmp_path)
    base = {k: v for k, v in upload_payload().items() if k != "document_id"}
    wrong = call(c, "POST", "/v1/w2/preflight", {**base, "content_b64": base64.b64encode(b"hello world").decode()})
    assert wrong.status_code == 415
    big = call(c, "POST", "/v1/w2/preflight", {**base, "content_b64": base64.b64encode(b"%PDF-" + b"a" * 1_100_000).decode()})
    assert big.status_code == 413
    assert call(c, "POST", "/v1/w2/preflight", {**base, "content_b64": "***"}).status_code == 400
    assert _row_counts(tmp_path) == [0, 0, 0]


def test_preflight_requires_a_valid_signature(tmp_path):
    c, _, _ = build(tmp_path)
    r = call(c, "POST", "/v1/w2/preflight", {"patient_uuid": PATIENT, "doc_type": "lab_pdf", "content_b64": "AAAA"},
             secret="wrong-secret-wrong-secret-wrong-secret")
    assert r.status_code == 401


def test_a_rejected_upload_leaves_no_extraction_record(tmp_path):
    """Direct upload of a bad file or another patient's file: no document, fact or citation row is created."""
    c, _, _ = build(tmp_path)
    assert call(c, "POST", "/v1/w2/documents", upload_payload(patient=OTHER)).status_code == 403
    assert call(c, "POST", "/v1/w2/documents", {**upload_payload(), "content_b64": base64.b64encode(b"nope").decode()}).status_code == 415
    assert _row_counts(tmp_path) == [0, 0, 0]
