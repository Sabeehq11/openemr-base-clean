"""Per-user rate limit in the gateway: counts only verified callers, answers 429 generically, and is audited."""
from __future__ import annotations

import httpx
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.api import build_router
from app.config import Settings
from app.fhir_client import FhirClient
from app.gateway import Gateway, RateLimiter
from app.tools import ToolRunner

from .test_gateway_unit import NOW, SECRET, USER, Audit, FakeAuthz, signed


def test_limiter_window_slides():
    t = [1000.0]
    rl = RateLimiter(2, window_s=60, clock=lambda: t[0])
    assert rl.allow("u") and rl.allow("u") and not rl.allow("u")
    assert rl.allow("other")  # keys are independent
    t[0] += 61
    assert rl.allow("u")


def test_limit_zero_disables():
    rl = RateLimiter(0)
    assert all(rl.allow("u") for _ in range(500))


def _client(limit: int):
    audit = Audit()
    s = Settings(module_signing_secret=SecretStr(SECRET), openemr_base_url="http://oe", rate_limit_per_min=limit)
    body = {"resourceType": "Bundle", "entry": []}
    fhir = FhirClient(s, transport=httpx.MockTransport(lambda r: httpx.Response(200, json=body)))
    app = FastAPI()
    app.include_router(build_router(Gateway(s, audit, clock=lambda: NOW), ToolRunner(FakeAuthz(True), fhir, audit=audit)))
    return TestClient(app), audit


def test_gateway_returns_generic_429_and_audits():
    c, audit = _client(2)
    codes = []
    for i in range(3):
        path, body, headers = signed(nonce=f"nonce-0123456789a{i}")
        codes.append(c.post(path, content=body, headers=headers).status_code)
    assert codes == [200, 200, 429]
    path, body, headers = signed(nonce="nonce-0123456789zz")
    r = c.post(path, content=body, headers=headers)
    assert r.status_code == 429 and r.headers["retry-after"] == "30"
    assert r.json() == {"detail": {"error": "rate_limited", "message": "Too many requests. Please wait a moment."}}
    assert "gateway_rate_limited" in [e for e, _ in audit.events]


def test_unverified_requests_do_not_consume_the_users_budget():
    c, _ = _client(1)
    path, body, headers = signed(secret="wrong-secret-wrong-secret-wrong!", nonce="nonce-0123456789b1")
    assert [c.post(path, content=body, headers=headers).status_code for _ in range(3)] == [401, 401, 401]
    path, body, headers = signed(nonce="nonce-0123456789b2")
    assert c.post(path, content=body, headers=headers).status_code == 200
    assert USER  # the legitimate user was never locked out by forged traffic
