"""Fail-closed, patient-level authorization backed by OpenEMR data.

Findings that shape this module (see agent/AUTHZ_PROOF.md):
  * OpenEMR validates the OAuth token and the scope, but does NOT check patient-level access:
    any authenticated clinician token can read any patient through FHIR.
  * So the agent makes its own decision, from OpenEMR data, on every call:
      1. the token must be active (RFC 7662 introspection by OpenEMR itself),
      2. it must carry the required scopes,
      3. its subject must be the user the caller claims (when a claim is made),
      4. that user must be an active member of the patient's CareTeam, read with the USER's own token.
  * Any error, timeout, malformed response or missing piece is a denial.
HMAC signing of module requests is an integrity check elsewhere; it never grants access here.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from enum import Enum

import httpx

from .config import Settings
from .logging_setup import log_event

logger = logging.getLogger("agent.authz")

REQUIRED_SCOPES = frozenset({"user/Patient.rs", "user/CareTeam.rs"})
AUTHZ_TIMEOUT_S = 3.0


class Reason(str, Enum):
    ALLOWED = "allowed"
    NO_TOKEN = "no_token"
    TOKEN_INACTIVE = "token_inactive"
    USER_MISMATCH = "user_mismatch"
    MISSING_SCOPE = "missing_scope"
    NOT_ON_CARE_TEAM = "not_on_care_team"
    LOOKUP_FAILED = "lookup_failed"


@dataclass(frozen=True)
class AuthzDecision:
    allowed: bool
    reason: Reason
    user_uuid: str | None = None
    patient_uuid: str | None = None


def _deny(reason: Reason, user: str | None = None) -> AuthzDecision:
    return AuthzDecision(False, reason, user)


class PatientAuthorizer:
    def __init__(self, settings: Settings, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._s = settings
        self._transport = transport

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            verify=self._s.openemr_verify_tls, timeout=AUTHZ_TIMEOUT_S, transport=self._transport
        )

    async def introspect(self, token: str) -> dict | None:
        """Ask OpenEMR whether the token is live. None means 'could not verify' (treated as denial)."""
        try:
            async with self._client() as c:
                r = await c.post(
                    f"{self._s.openemr_base_url}/oauth2/default/introspect",
                    data={
                        "token": token,
                        "client_id": self._s.oauth_client_id,
                        "client_secret": self._s.oauth_client_secret.get_secret_value(),
                    },
                )
            body = r.json()
        except (httpx.HTTPError, ValueError):
            return None
        if r.status_code != 200 or not isinstance(body, dict):
            return None
        return body

    async def care_team_members(self, token: str, patient_uuid: str) -> set[str] | None:
        """Practitioner UUIDs on the patient's active care teams, read with the user's own token."""
        try:
            async with self._client() as c:
                r = await c.get(
                    f"{self._s.openemr_base_url}/apis/default/fhir/CareTeam",
                    params={"patient": patient_uuid},
                    headers={"Authorization": f"Bearer {token}"},
                )
            if r.status_code != 200:
                return None
            bundle = r.json()
        except (httpx.HTTPError, ValueError):
            return None
        members: set[str] = set()
        for entry in bundle.get("entry", []) if isinstance(bundle, dict) else []:
            res = entry.get("resource", {})
            subject = (res.get("subject") or {}).get("reference", "")
            if subject and not subject.endswith(f"/{patient_uuid}"):
                continue  # a team for some other patient never grants access
            if res.get("status", "active") != "active":
                continue
            for p in res.get("participant", []):
                ref = (p.get("member") or {}).get("reference", "")
                if ref.startswith("Practitioner/"):
                    members.add(ref.split("/", 1)[1])
        return members

    async def authorize(
        self, token: str | None, patient_uuid: str, claimed_user_uuid: str | None = None
    ) -> AuthzDecision:
        decision = await self._decide(token, patient_uuid, claimed_user_uuid)
        log_event(
            logger,
            "authz_decision",
            allowed=decision.allowed,
            reason=decision.reason.value,
            user=decision.user_uuid,
            patient=patient_uuid,
            ts=time.time(),
        )
        return decision

    async def _decide(self, token: str | None, patient_uuid: str, claimed: str | None) -> AuthzDecision:
        if not token or not patient_uuid:
            return _deny(Reason.NO_TOKEN)
        info = await self.introspect(token)
        if info is None:
            return _deny(Reason.LOOKUP_FAILED)
        sub = info.get("sub")
        if info.get("active") is not True or not isinstance(sub, str) or not sub:
            return _deny(Reason.TOKEN_INACTIVE)
        exp = info.get("exp")
        if isinstance(exp, (int, float)) and exp <= time.time():
            return _deny(Reason.TOKEN_INACTIVE, sub)
        if claimed is not None and claimed != sub:
            return _deny(Reason.USER_MISMATCH, sub)
        granted = set(str(info.get("scope", "")).split())
        if not REQUIRED_SCOPES <= granted:
            return _deny(Reason.MISSING_SCOPE, sub)
        members = await self.care_team_members(token, patient_uuid)
        if members is None:
            return _deny(Reason.LOOKUP_FAILED, sub)
        if sub not in members:
            return _deny(Reason.NOT_ON_CARE_TEAM, sub)
        return AuthzDecision(True, Reason.ALLOWED, sub, patient_uuid)
