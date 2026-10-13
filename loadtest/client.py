"""Signed /v1/chat client for the load test. Mirrors what oe-module-copilot sends; synthetic users only."""
from __future__ import annotations

import json
import sys
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT / "agent"), str(ROOT)]

from app import signing  # noqa: E402

TERMINAL = {"answer", "declined", "degraded", "denied", "error"}
SCOPES = ("openid fhirUser user/Patient.rs user/CareTeam.rs user/Condition.rs user/MedicationRequest.rs "
          "user/AllergyIntolerance.rs user/Observation.rs user/Encounter.rs")


@dataclass
class Result:
    outcome: str  # terminal SSE event, or http_<status> / transport_error
    total_ms: float
    first_event_ms: float | None
    conversation_id: str | None
    correlation_id: str


def fetch_tokens(users: list[str]) -> dict[str, str]:
    """Real authorization-code tokens (no password grant). Held in memory only, never written out."""
    from tests.oauth_helper import get_token
    out: dict[str, str] = {}
    for u in users:
        for attempt in range(5):  # OpenEMR may still be busy from a previous run
            try:
                out[u] = get_token(u, SCOPES)["access_token"]
                break
            except (httpx.HTTPError, AttributeError):
                if attempt == 4:
                    raise
                time.sleep(10)
    return out


async def chat(client: httpx.AsyncClient, base: str, secret: str, user_uuid: str, token: str, patient: str,
               question: str, conversation_id: str | None = None) -> Result:
    body = json.dumps({"patient_uuid": patient, "question": question, "conversation_id": conversation_id}).encode()
    path, ts, nonce, cid = "/v1/chat", str(int(time.time())), str(uuid.uuid4()), str(uuid.uuid4())
    headers = {
        "x-copilot-timestamp": ts, "x-copilot-nonce": nonce, "x-copilot-user": user_uuid,
        "x-copilot-token": token, "x-correlation-id": cid, "content-type": "application/json",
        "x-copilot-signature": signing.sign(secret, "POST", path, ts, nonce, body, user_uuid, token),
    }
    t0, first, outcome, conv = time.perf_counter(), None, "no_terminal_event", conversation_id
    try:
        async with client.stream("POST", f"{base}{path}", content=body, headers=headers) as r:
            if r.status_code != 200:
                await r.aread()
                return Result(f"http_{r.status_code}", _ms(t0), None, conv, cid)
            event = ""
            async for line in r.aiter_lines():
                if line.startswith("event: "):
                    event = line[7:]
                    if first is None:
                        first = _ms(t0)
                    if event in TERMINAL:
                        outcome = event
                elif line.startswith("data: ") and event == "conversation":
                    conv = json.loads(line[6:]).get("id", conv)
    except httpx.HTTPError as e:
        return Result(f"transport_{type(e).__name__}", _ms(t0), first, conv, cid)
    return Result(outcome, _ms(t0), first, conv, cid)


def _ms(t0: float) -> float:
    return round((time.perf_counter() - t0) * 1000, 1)
