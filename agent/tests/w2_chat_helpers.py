"""Harness for /v1/w2/chat tests: signed requests, a two-stage stub model, a FHIR mock, and SSE parsing."""
import base64
import json

import httpx
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr

from app import signing
from app.authz import AuthzDecision, Reason
from app.config import Settings
from app.conversation import ConversationStore
from app.fhir_client import FhirClient
from app.gateway import Gateway
from app.llm import DemoStubLLM, LLMResponse, Usage
from app.tools import ToolRunner
from app.w2.api import build_w2_router
from app.w2.chat import build_w2_chat_router
from app.w2.rag import build_retriever
from contracts import VerifiedAnswer, VerifiedClaim
from tests.w2_helpers import PATIENT, fx, make_ingestor

SECRET = "test-signing-secret"
USER = "aaaaaaaa-1111-2222-3333-444444444444"
NOW = 1_800_000_000
_n = [0]
OTHER_PATIENT = "99999999-8888-7777-6666-555555555555"
Q = "What changed, and what do the guidelines say about A1c frequency?"


class Authz:
    async def authorize(self, token, patient_uuid, claimed_user_uuid=None):
        if patient_uuid == PATIENT:
            return AuthzDecision(True, Reason.ALLOWED, USER, patient_uuid)
        return AuthzDecision(False, Reason.NOT_ON_CARE_TEAM, USER)


class Audit:
    def __init__(self):
        self.events = []

    def record(self, event, **f):
        self.events.append((event, f))


class TwoStageLLM:
    """Stage A (tools offered) is the Week 1 demo model. Stage B (no tools) is the scripted reply."""

    def __init__(self, stage_b):
        self.stage_b, self.b_calls = stage_b, []

    async def complete(self, messages, tools):
        if tools:
            return await DemoStubLLM().complete(messages, tools)
        self.b_calls.append(messages)
        if isinstance(self.stage_b, Exception):
            raise self.stage_b
        return self.stage_b


def stage_b(*claims):
    body = {"claims": [{"text": t, "source_ids": list(s)} for t, s in claims]}
    return LLMResponse(text=json.dumps(body), usage=Usage(10, 5, 0.001))


def passthrough(draft, envs):
    return VerifiedAnswer(claims=[VerifiedClaim(text=c.text, source_ids=c.source_ids, kind=c.kind) for c in draft.claims])


def post(client, path, payload):
    _n[0] += 1
    body = json.dumps(payload).encode()
    nonce = f"w2chat-nonce-{_n[0]:08d}"
    sig = signing.sign(SECRET, "POST", path, str(NOW), nonce, body, USER, "tok")
    return client.post(path, content=body, headers={
        "x-copilot-timestamp": str(NOW), "x-copilot-nonce": nonce, "x-copilot-signature": sig,
        "x-copilot-user": USER, "x-copilot-token": "tok", "content-type": "application/json"})


def build(tmp_path, llm, finalizer=passthrough, upload=True, fhir_down=False, traces=None):
    def h(req):
        if fhir_down:
            return httpx.Response(503)
        res = req.url.path.rsplit("/", 1)[-1]
        return httpx.Response(200, json={"resourceType": "Bundle", "entry": [
            {"resource": {"resourceType": res, "id": "r1", "code": {"text": f"{res} item"}}}]})

    tmp_path.mkdir(parents=True, exist_ok=True)
    s = Settings(module_signing_secret=SecretStr(SECRET), openemr_base_url="http://oe", data_dir=str(tmp_path))
    audit = Audit()
    gw = Gateway(s, audit, clock=lambda: NOW)
    runner = ToolRunner(Authz(), FhirClient(s, transport=httpx.MockTransport(h)))
    ingestor, store = make_ingestor(tmp_path)
    retr = build_retriever(s, offline=True)
    app = FastAPI()
    app.include_router(build_w2_router(gw, Authz(), ingestor, store, retr, audit, 1_000_000))
    app.include_router(build_w2_chat_router(gw, runner, lambda: llm, finalizer, ConversationStore(), store, retr, audit, traces))
    client = TestClient(app)
    if upload:
        r = post(client, "/v1/w2/documents", {"patient_uuid": PATIENT, "document_id": 7, "doc_type": "lab_pdf",
                                              "content_b64": base64.b64encode(fx("lab_report.pdf")).decode()})
        assert r.status_code == 200, r.text
    return client, audit


def ask(client, question, patient=PATIENT):
    return post(client, "/v1/w2/chat", {"patient_uuid": patient, "question": question})


def events(r):
    out = []
    for block in r.text.strip().split("\n\n"):
        name, data = block.split("\n")
        out.append((name.removeprefix("event: "), json.loads(data.removeprefix("data: "))))
    return out
