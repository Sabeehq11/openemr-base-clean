"""Fail-closed authorization, offline. Each test guards one denial path; none may fall through to allow."""
import time

import httpx
import pytest

from app.authz import PatientAuthorizer, Reason
from app.config import Settings

PAT = "11111111-1111-1111-1111-111111111111"
OTHER_PAT = "22222222-2222-2222-2222-222222222222"
DOC = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
OTHER_DOC = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
SCOPES = "openid fhirUser user/Patient.rs user/CareTeam.rs"


def team_bundle(patient, members, status="active"):
    return {"entry": [{"resource": {
        "status": status, "subject": {"reference": f"Patient/{patient}"},
        "participant": [{"member": {"reference": f"Practitioner/{m}"}} for m in members]}}]}


def make(introspect=None, careteam=None, careteam_status=200, introspect_exc=None, careteam_exc=None):
    calls = {"careteam": 0, "introspect": 0}

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path.endswith("/introspect"):
            calls["introspect"] += 1
            if introspect_exc:
                raise introspect_exc
            return httpx.Response(200, json=introspect)
        calls["careteam"] += 1
        if careteam_exc:
            raise careteam_exc
        return httpx.Response(careteam_status, json=careteam if careteam is not None else {})

    s = Settings(openemr_base_url="http://oe", oauth_client_id="c", oauth_client_secret="s")
    return PatientAuthorizer(s, httpx.MockTransport(handler)), calls


def live_token(sub=DOC, scope=SCOPES, active=True, exp=None):
    return {"active": active, "sub": sub, "scope": scope, "exp": exp or int(time.time()) + 600}


async def test_allows_care_team_member():
    a, _ = make(live_token(), team_bundle(PAT, [DOC]))
    d = await a.authorize("tok", PAT, claimed_user_uuid=DOC)
    assert d.allowed and d.reason is Reason.ALLOWED and d.user_uuid == DOC


async def test_denies_physician_not_on_care_team():
    a, _ = make(live_token(), team_bundle(PAT, [OTHER_DOC]))
    d = await a.authorize("tok", PAT)
    assert not d.allowed and d.reason is Reason.NOT_ON_CARE_TEAM


async def test_denies_when_care_team_is_for_a_different_patient():
    a, _ = make(live_token(), team_bundle(OTHER_PAT, [DOC]))
    assert (await a.authorize("tok", PAT)).reason is Reason.NOT_ON_CARE_TEAM


async def test_denies_inactive_care_team():
    a, _ = make(live_token(), team_bundle(PAT, [DOC], status="inactive"))
    assert (await a.authorize("tok", PAT)).reason is Reason.NOT_ON_CARE_TEAM


async def test_denies_missing_token_without_any_network_call():
    a, calls = make(live_token(), team_bundle(PAT, [DOC]))
    assert (await a.authorize(None, PAT)).reason is Reason.NO_TOKEN
    assert (await a.authorize("", PAT)).reason is Reason.NO_TOKEN
    assert calls == {"careteam": 0, "introspect": 0}


async def test_denies_inactive_or_expired_token_and_never_reads_patient_data():
    a, calls = make({"active": False}, team_bundle(PAT, [DOC]))
    assert (await a.authorize("tok", PAT)).reason is Reason.TOKEN_INACTIVE
    a2, calls2 = make(live_token(exp=int(time.time()) - 5), team_bundle(PAT, [DOC]))
    assert (await a2.authorize("tok", PAT)).reason is Reason.TOKEN_INACTIVE
    assert calls["careteam"] == 0 and calls2["careteam"] == 0


async def test_denies_token_of_a_different_user_than_claimed():
    a, calls = make(live_token(sub=OTHER_DOC), team_bundle(PAT, [OTHER_DOC]))
    d = await a.authorize("tok", PAT, claimed_user_uuid=DOC)
    assert d.reason is Reason.USER_MISMATCH and calls["careteam"] == 0


@pytest.mark.parametrize("scope", ["openid fhirUser", "user/Patient.rs", "user/CareTeam.rs openid"])
async def test_denies_missing_scope(scope):
    a, calls = make(live_token(scope=scope), team_bundle(PAT, [DOC]))
    assert (await a.authorize("tok", PAT)).reason is Reason.MISSING_SCOPE
    assert calls["careteam"] == 0


@pytest.mark.parametrize(
    "kwargs",
    [
        dict(introspect_exc=httpx.ConnectTimeout("t")),
        dict(introspect_exc=httpx.ConnectError("c")),
        dict(careteam_exc=httpx.ReadTimeout("t")),
        dict(careteam_status=500),
        dict(careteam_status=401),
        dict(careteam_status=403),
    ],
)
async def test_fails_closed_when_lookup_errors_or_times_out(kwargs):
    base = dict(introspect=live_token(), careteam=team_bundle(PAT, [DOC]))
    base.update(kwargs)
    a, _ = make(**base)
    d = await a.authorize("tok", PAT)
    assert not d.allowed and d.reason is Reason.LOOKUP_FAILED


@pytest.mark.parametrize("body", [None, [], "nope", {"active": "true", "sub": DOC}, {"active": True}, {"active": True, "sub": ""}])
async def test_fails_closed_on_malformed_introspection(body):
    a, _ = make(body, team_bundle(PAT, [DOC]))
    assert not (await a.authorize("tok", PAT)).allowed


async def test_denies_on_empty_or_malformed_care_team_bundle():
    for bundle in ({}, {"entry": []}, {"entry": [{"resource": {}}]}, []):
        a, _ = make(live_token(), bundle)
        assert not (await a.authorize("tok", PAT)).allowed


async def test_provider_id_style_claims_are_ignored():
    """A caller asserting 'I am the provider' (e.g. an HMAC-signed claim) cannot substitute for the token check."""
    a, _ = make(live_token(sub=OTHER_DOC), team_bundle(PAT, [DOC]))
    d = await a.authorize("tok", PAT, claimed_user_uuid=OTHER_DOC)
    assert not d.allowed and d.reason is Reason.NOT_ON_CARE_TEAM
