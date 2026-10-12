"""GATE: user-scoped OAuth + patient-level authorization, proven against the live local OpenEMR.

Run: OE_LIVE=1 .venv/bin/python -m pytest tests/test_authz_live.py
Findings are written up in agent/AUTHZ_PROOF.md.
"""
import httpx
import pytest

from app.authz import PatientAuthorizer, Reason
from app.config import Settings

from .oauth_helper import BASE, get_token, load_env, load_fixture, password_grant_attempt, sql

pytestmark = pytest.mark.live

FHIR = f"{BASE}/apis/default/fhir"


@pytest.fixture(scope="module")
def fx():
    return load_fixture()


@pytest.fixture(scope="module")
def authorizer():
    env = load_env()
    return PatientAuthorizer(Settings(
        openemr_base_url=BASE, openemr_verify_tls=False,
        oauth_client_id=env["OAUTH_CLIENT_ID"], oauth_client_secret=env["OAUTH_CLIENT_SECRET"]))


@pytest.fixture(scope="module")
def tokens():
    return {u: get_token(u) for u in ("copilot_doc_a", "copilot_doc_b", "copilot_nurse")}


def bearer(tok):
    return {"Authorization": f"Bearer {tok['access_token']}"}


def test_token_belongs_to_the_logged_in_user(tokens, fx, authorizer):
    import asyncio
    for user in ("copilot_doc_a", "copilot_doc_b", "copilot_nurse"):
        info = asyncio.run(authorizer.introspect(tokens[user]["access_token"]))
        assert info["active"] is True
        assert info["sub"] == fx["user_uuids"][user]


def test_password_grant_is_refused_for_the_copilot_client():
    assert password_grant_attempt("copilot_doc_a") in (400, 401, 403)


def test_openemr_alone_does_not_enforce_patient_level_access(tokens, fx):
    """Documented finding (AUDIT S3): physician A's valid token can read physician B's patient."""
    b_patient = fx["patient_uuids"]["101"]
    assert fx["patient_owner"]["101"] == fx["user_ids"]["copilot_doc_b"]
    r = httpx.get(f"{FHIR}/Patient/{b_patient}", headers=bearer(tokens["copilot_doc_a"]), verify=False)
    assert r.status_code == 200, "if this starts failing OpenEMR now enforces it; update AUTHZ_PROOF.md"


async def test_care_team_gate_allows_owner_and_denies_other_physician(tokens, fx, authorizer):
    a, b = tokens["copilot_doc_a"]["access_token"], tokens["copilot_doc_b"]["access_token"]
    own_a, own_b = fx["patient_uuids"]["100"], fx["patient_uuids"]["101"]
    assert (await authorizer.authorize(a, own_a, fx["user_uuids"]["copilot_doc_a"])).allowed
    assert (await authorizer.authorize(b, own_b, fx["user_uuids"]["copilot_doc_b"])).allowed
    assert (await authorizer.authorize(a, own_b)).reason is Reason.NOT_ON_CARE_TEAM
    assert (await authorizer.authorize(b, own_a)).reason is Reason.NOT_ON_CARE_TEAM


async def test_nurse_not_on_any_care_team_is_denied_everywhere(tokens, fx, authorizer):
    for pid in ("100", "101", "104"):
        d = await authorizer.authorize(tokens["copilot_nurse"]["access_token"], fx["patient_uuids"][pid])
        assert not d.allowed and d.reason is Reason.NOT_ON_CARE_TEAM


async def test_user_without_clinical_acl_is_denied(fx, authorizer):
    tok = get_token("copilot_front")
    d = await authorizer.authorize(tok["access_token"], fx["patient_uuids"]["100"])
    assert not d.allowed


async def test_token_for_different_user_than_claimed_is_denied(tokens, fx, authorizer):
    d = await authorizer.authorize(
        tokens["copilot_doc_b"]["access_token"], fx["patient_uuids"]["101"], fx["user_uuids"]["copilot_doc_a"])
    assert d.reason is Reason.USER_MISMATCH


async def test_garbage_and_missing_tokens_are_denied(fx, authorizer):
    assert not (await authorizer.authorize("abc.def.ghi", fx["patient_uuids"]["100"])).allowed
    assert not (await authorizer.authorize(None, fx["patient_uuids"]["100"])).allowed


async def test_missing_required_scope_is_denied(fx, authorizer):
    tok = get_token("copilot_doc_a", "openid fhirUser user/Patient.rs")
    d = await authorizer.authorize(tok["access_token"], fx["patient_uuids"]["100"])
    assert d.reason is Reason.MISSING_SCOPE


async def test_expired_then_revoked_tokens_are_refused(fx, authorizer):
    tok = get_token("copilot_doc_a")
    t, own = tok["access_token"], fx["patient_uuids"]["100"]
    assert (await authorizer.authorize(t, own)).allowed
    row = sql("SELECT id FROM api_token WHERE user_id='%s' ORDER BY id DESC LIMIT 1" % fx["user_uuids"]["copilot_doc_a"])
    try:
        sql(f"UPDATE api_token SET revoked=1 WHERE id={row}")
        d = await authorizer.authorize(t, own)
        assert not d.allowed and d.reason is Reason.TOKEN_INACTIVE
        assert httpx.get(f"{FHIR}/Patient/{own}", headers={"Authorization": f"Bearer {t}"}, verify=False).status_code == 401
    finally:
        sql(f"UPDATE api_token SET revoked=0 WHERE id={row}")
    sql(f"UPDATE api_token SET expiry = NOW() - INTERVAL 1 MINUTE WHERE id={row}")
    # Introspection trusts the JWT exp, not the DB expiry, so it may still say "active". The CareTeam read made
    # with the user's own token is what refuses it; the decision must still be a denial.
    d = await authorizer.authorize(t, own)
    assert not d.allowed and d.reason in (Reason.TOKEN_INACTIVE, Reason.LOOKUP_FAILED)


async def test_fails_closed_when_openemr_is_unreachable(tokens, fx):
    env = load_env()
    dead = PatientAuthorizer(Settings(
        openemr_base_url="http://127.0.0.1:9", oauth_client_id=env["OAUTH_CLIENT_ID"],
        oauth_client_secret=env["OAUTH_CLIENT_SECRET"]))
    d = await dead.authorize(tokens["copilot_doc_a"]["access_token"], fx["patient_uuids"]["100"])
    assert not d.allowed and d.reason is Reason.LOOKUP_FAILED
