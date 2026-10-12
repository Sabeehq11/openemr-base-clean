"""Request signing shared by the OpenEMR module (PHP mirrors this) and the agent.

The signature is an INTEGRITY check only: it proves the module sent exactly these bytes recently. It never grants
access; every tool call still re-checks the user's OAuth token and the patient-level decision.

Canonical string (fields joined by "\\n"):
    v1, METHOD, path, timestamp, nonce, sha256(body), claimed_user_uuid, sha256(user_token)
"""
from __future__ import annotations

import hashlib
import hmac

VERSION = "v1"


def _sha(data: bytes | str) -> str:
    return hashlib.sha256(data.encode() if isinstance(data, str) else data).hexdigest()


def canonical(method: str, path: str, timestamp: str, nonce: str, body: bytes, user: str, token: str) -> str:
    return "\n".join([VERSION, method.upper(), path, timestamp, nonce, _sha(body), user, _sha(token)])


def sign(secret: str, method: str, path: str, timestamp: str, nonce: str, body: bytes, user: str, token: str) -> str:
    msg = canonical(method, path, timestamp, nonce, body, user, token).encode()
    return hmac.new(secret.encode(), msg, hashlib.sha256).hexdigest()


def verify(signature: str, secret: str, method: str, path: str, timestamp: str, nonce: str, body: bytes,
           user: str, token: str) -> bool:
    expected = sign(secret, method, path, timestamp, nonce, body, user, token)
    return hmac.compare_digest(expected, signature)
