"""Signed-request gateway (defense in depth). Verifies integrity and freshness, then hands a typed CallerContext on.

It does not decide who may see which patient: that is PatientAuthorizer, called by ToolRunner on every tool call.
All failures here return the same generic 401 so a caller learns nothing about which check failed.
"""
from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass
from typing import Protocol

from fastapi import HTTPException, Request

from . import signing
from .config import Settings
from .logging_setup import correlation_id_var, log_event

logger = logging.getLogger("agent.gateway")

MAX_SKEW_S = 60
_NONCE_RE = re.compile(r"^[A-Za-z0-9\-]{16,64}$")
_UUID_RE = re.compile(r"^[0-9a-fA-F\-]{32,36}$")
GENERIC_401 = {"error": "unauthenticated", "message": "Request could not be verified."}


@dataclass(frozen=True)
class CallerContext:
    user_uuid: str
    token: str
    correlation_id: str
    body: bytes


class AuditSink(Protocol):
    def record(self, event: str, **fields: object) -> None: ...


class LogAuditSink:
    """Default sink: structured log line. Replaced by the HMAC-chained store in the observability step."""

    def record(self, event: str, **fields: object) -> None:
        log_event(logger, event, **fields)


class NonceCache:
    def __init__(self, ttl_s: int = MAX_SKEW_S * 2, clock=time.time) -> None:
        self._ttl, self._clock, self._seen = ttl_s, clock, {}

    def first_use(self, nonce: str) -> bool:
        now = self._clock()
        self._seen = {n: t for n, t in self._seen.items() if now - t < self._ttl}
        if nonce in self._seen:
            return False
        self._seen[nonce] = now
        return True


class RateLimiter:
    """Sliding one-minute window per key. In memory, per process: enough to blunt a runaway client or a replay flood."""

    def __init__(self, limit: int, window_s: int = 60, clock=time.time) -> None:
        self._limit, self._window, self._clock = limit, window_s, clock
        self._hits: dict[str, list[float]] = {}

    def allow(self, key: str) -> bool:
        if self._limit <= 0:
            return True
        now = self._clock()
        recent = [t for t in self._hits.get(key, []) if now - t < self._window]
        if len(recent) >= self._limit:
            self._hits[key] = recent
            return False
        recent.append(now)
        self._hits[key] = recent
        if len(self._hits) > 10_000:  # bound memory
            self._hits = {k: v for k, v in self._hits.items() if v and now - v[-1] < self._window}
        return True


GENERIC_429 = {"error": "rate_limited", "message": "Too many requests. Please wait a moment."}


class Gateway:
    def __init__(self, settings: Settings, audit: AuditSink | None = None, clock=time.time) -> None:
        self._s, self._audit, self._clock = settings, audit or LogAuditSink(), clock
        self._nonces = NonceCache(clock=clock)
        self._limiter = RateLimiter(settings.rate_limit_per_min, clock=clock)

    def _reject(self, why: str) -> HTTPException:
        self._audit.record("gateway_rejected", why=why, correlation_id=correlation_id_var.get())
        return HTTPException(status_code=401, detail=GENERIC_401)

    async def verify(self, request: Request) -> CallerContext:
        secret = self._s.module_signing_secret.get_secret_value()
        if not secret:
            raise self._reject("signing_not_configured")  # fail closed: no secret, nothing is accepted
        h = request.headers
        ts, nonce, sig = h.get("x-copilot-timestamp", ""), h.get("x-copilot-nonce", ""), h.get("x-copilot-signature", "")
        user, token = h.get("x-copilot-user", ""), h.get("x-copilot-token", "")
        if not (ts and nonce and sig and user and token):
            raise self._reject("missing_header")
        if not _NONCE_RE.match(nonce) or not _UUID_RE.match(user):
            raise self._reject("malformed_header")
        try:
            skew = abs(self._clock() - int(ts))
        except ValueError:
            raise self._reject("malformed_timestamp") from None
        if skew > MAX_SKEW_S:
            raise self._reject("stale_timestamp")
        body = await request.body()
        if not signing.verify(sig, secret, request.method, request.url.path, ts, nonce, body, user, token):
            raise self._reject("bad_signature")
        if not self._nonces.first_use(nonce):  # only consumed after the signature is valid
            raise self._reject("replay")
        if not self._limiter.allow(user.lower()):  # only verified callers are counted, so it cannot be used to lock others out
            self._audit.record("gateway_rate_limited", user=user, correlation_id=correlation_id_var.get())
            raise HTTPException(status_code=429, detail=GENERIC_429, headers={"Retry-After": "30"})
        return CallerContext(user_uuid=user, token=token, correlation_id=correlation_id_var.get(), body=body)
