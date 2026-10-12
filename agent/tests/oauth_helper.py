"""Live-stack helpers: real authorization-code flow (no password grant) against local OpenEMR.

Only used by tests marked `live`. Synthetic users only; credentials come from agent/.env.
"""
from __future__ import annotations

import base64
import os
import hashlib
import json
import re
import secrets
import subprocess
import urllib.parse as up
from pathlib import Path

import httpx

BASE = "https://localhost:9300"
REDIRECT = "http://localhost:8400/oauth/callback"
AGENT_DIR = Path(__file__).resolve().parent.parent
MYSQL = ["docker", "exec", "development-easy-light-mysql-1", "mariadb", "-uroot", f"-p{os.environ.get('DB_ROOT_PASS', 'root')}", "openemr", "-N", "-e"]


def load_env() -> dict[str, str]:
    out: dict[str, str] = {}
    for line in (AGENT_DIR / ".env").read_text().splitlines():
        if "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip()
    return out


def load_fixture() -> dict:
    return json.loads((AGENT_DIR / "data" / "fixture.json").read_text())


def sql(statement: str) -> str:
    return subprocess.check_output([*MYSQL, statement], text=True).strip()


def login_and_consent(username: str, scopes: str) -> tuple[httpx.Client, str, str]:
    """Run authorize -> login -> returns (client, consent_html, pkce_verifier)."""
    env = load_env()
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    q = dict(
        response_type="code", client_id=env["OAUTH_CLIENT_ID"], redirect_uri=REDIRECT, scope=scopes,
        state="s1", code_challenge=challenge, code_challenge_method="S256",
    )
    h = httpx.Client(verify=False, follow_redirects=True)
    r = h.get(f"{BASE}/oauth2/default/authorize", params=q)
    csrf = re.search(r'name="csrf_token_form" value="([^"]+)"', r.text).group(1)
    r = h.post(
        f"{BASE}/oauth2/default/login",
        data=dict(csrf_token_form=csrf, username=username, password=env["COPILOT_FIXTURE_PASSWORD"],
                  email="", user_role="api"),
    )
    return h, r.text, verifier


def get_token(username: str, scopes: str = "openid fhirUser user/Patient.rs user/CareTeam.rs") -> dict:
    """Authorization-code flow as `username`; returns the token response (raises if refused)."""
    env = load_env()
    h, consent, verifier = login_and_consent(username, scopes)
    csrf = re.search(r'name="csrf_token_form" value="([^"]+)"', consent).group(1)
    data = {"csrf_token_form": csrf, "proceed": "1"}
    data.update({f"scope[{s}]": s for s in scopes.split()})
    r = h.post(f"{BASE}/oauth2/default/device/code", data=data, follow_redirects=False)
    code = up.parse_qs(up.urlparse(r.headers["location"]).query)["code"][0]
    t = httpx.post(
        f"{BASE}/oauth2/default/token", verify=False,
        data=dict(grant_type="authorization_code", code=code, redirect_uri=REDIRECT, code_verifier=verifier,
                  client_id=env["OAUTH_CLIENT_ID"], client_secret=env["OAUTH_CLIENT_SECRET"]),
    )
    t.raise_for_status()
    return t.json()


def password_grant_attempt(username: str) -> int:
    """The password grant must be refused for the Co-Pilot client."""
    env = load_env()
    r = httpx.post(
        f"{BASE}/oauth2/default/token", verify=False,
        data=dict(grant_type="password", username=username, password=env["COPILOT_FIXTURE_PASSWORD"],
                  client_id=env["OAUTH_CLIENT_ID"], client_secret=env["OAUTH_CLIENT_SECRET"],
                  scope="openid fhirUser user/Patient.rs", user_role="users"),
    )
    return r.status_code
