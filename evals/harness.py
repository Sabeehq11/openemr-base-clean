"""Eval harness. Runs the real gateway, authorization check, tools, loop, verifier, audit chain and traces.
Only two things are faked: the FHIR server (a Chart of synthetic resources) and the model (a script of replies).
No network, no real PHI."""
from __future__ import annotations

import json
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

import httpx
from fastapi.testclient import TestClient
from pydantic import SecretStr

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "agent"))

from app import signing  # noqa: E402
from app.authz import AuthzDecision, Reason  # noqa: E402
from app.config import Settings  # noqa: E402
from app.llm import LLMResponse, StubLLM, ToolCall, Usage  # noqa: E402
from app.main import create_app  # noqa: E402

NOW = 1_800_000_000
SECRET = "eval-secret"
DOC = "aaaaaaaa-1111-2222-3333-444444444444"
PID = "11111111-2222-3333-4444-555555555555"
OTHER_PID = "99999999-2222-3333-4444-555555555555"


@dataclass
class Chart:
    """Synthetic FHIR resources by type. `fail` maps a resource type to 'timeout' | 'error' | 'malformed'."""
    resources: dict[str, list[dict]] = field(default_factory=dict)
    fail: dict[str, str] = field(default_factory=dict)
    patient: dict | None = None

    def handler(self, req: httpx.Request) -> httpx.Response:
        parts = req.url.path.split("/")
        rtype = parts[-2] if parts[-2] in ("Patient",) else parts[-1]
        mode = self.fail.get(rtype)
        if mode == "timeout":
            raise httpx.ReadTimeout("t", request=req)
        if mode == "error":
            return httpx.Response(500)
        if mode == "malformed":
            return httpx.Response(200, text="<html>not json")
        if rtype == "Patient":
            body = self.patient or {"resourceType": "Patient", "id": PID, "gender": "female", "birthDate": "1960-05-01",
                                    "name": [{"given": ["Test"], "family": "Patient"}]}
            return httpx.Response(200, json=body)
        entries = [{"resource": {"resourceType": rtype, **r}} for r in self.resources.get(rtype, [])]
        return httpx.Response(200, json={"resourceType": "Bundle", "entry": entries})


class FakeAuthz:
    def __init__(self, allowed: set[str], user: str = DOC) -> None:
        self.allowed, self.user = allowed, user

    async def authorize(self, token, patient_uuid, claimed_user_uuid=None):
        if token and patient_uuid in self.allowed:
            return AuthzDecision(True, Reason.ALLOWED, self.user, patient_uuid)
        return AuthzDecision(False, Reason.NOT_ON_CARE_TEAM, self.user)


def call(name: str, /, **args) -> LLMResponse:
    return LLMResponse(tool_calls=[ToolCall(f"c-{name}", name, args)], usage=Usage(10, 5, 0.001))


def answer(*claims: dict) -> LLMResponse:
    return LLMResponse(text=json.dumps({"claims": list(claims)}), usage=Usage(10, 5, 0.001))


def claim(text: str, *sources: str, kind: str = "fact") -> dict:
    return {"text": text, "kind": kind, "source_ids": list(sources)}


class Harness:
    def __init__(self, chart: Chart, script: list | None = None, allowed: set[str] | None = None,
                 llm=None, settings_overrides: dict | None = None) -> None:
        self.tmp = tempfile.mkdtemp(prefix="eval-")
        base = dict(data_dir=self.tmp, audit_hmac_key=SecretStr("k" * 32), module_signing_secret=SecretStr(SECRET),
                    llm_provider="stub", openemr_base_url="http://oe", tool_timeout_s=1.0)
        s = Settings(**{**base, **(settings_overrides or {})})  # overrides win (the live tier switches the provider)
        self.app = create_app(s)
        self.app.state.gateway._clock = lambda: NOW
        runner = self.app.state.runner
        runner._authz = FakeAuthz(PID_SET if allowed is None else allowed)
        runner._fhir._transport = httpx.MockTransport(chart.handler)
        self.llm = llm or StubLLM(script or [])
        self.app.router.routes  # routes already include chat; swap the factory used per request
        self._patch_llm()
        self.client = TestClient(self.app)
        self._n = 0

    def _patch_llm(self) -> None:
        import app.main as m
        m.make_llm = lambda cfg: self.llm  # the chat router's lambda resolves make_llm from app.main at call time

    def chat(self, question: str, patient: str = PID, conv: str | None = None, token: str = "tok",
             secret: str = SECRET) -> "Result":
        self._n += 1
        body = json.dumps({"patient_uuid": patient, "question": question, "conversation_id": conv}).encode()
        nonce = f"eval-nonce-{self._n:016d}"
        sig = signing.sign(secret, "POST", "/v1/chat", str(NOW), nonce, body, DOC, token)
        r = self.client.post("/v1/chat", content=body, headers={
            "x-copilot-timestamp": str(NOW), "x-copilot-nonce": nonce, "x-copilot-signature": sig,
            "x-copilot-user": DOC, "x-copilot-token": token, "content-type": "application/json"})
        return Result(r, self)


PID_SET = {PID}


class Result:
    def __init__(self, resp: httpx.Response, h: Harness) -> None:
        self.resp, self.h = resp, h
        self.events: list[tuple[str, dict]] = []
        if resp.headers.get("content-type", "").startswith("text/event-stream"):
            for block in resp.text.strip().split("\n\n"):
                name, data = block.split("\n")[:2]
                self.events.append((name.removeprefix("event: "), json.loads(data.removeprefix("data: "))))

    @property
    def names(self) -> list[str]:
        return [n for n, _ in self.events]

    def get(self, name: str) -> dict | None:
        return next((d for n, d in self.events if n == name), None)

    @property
    def claims(self) -> list[dict]:
        a = self.get("answer")
        return a["claims"] if a else []

    @property
    def text(self) -> str:
        return " ".join(c["text"] for c in self.claims)

    @property
    def tools_called(self) -> list[str]:
        return [d["tool"] for n, d in self.events if n == "step"]
