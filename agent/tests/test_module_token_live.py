"""The module-issued token (UserTokenIssuer) must pass the same fail-closed gate as an authorization-code token."""
import subprocess

import pytest

from app.authz import PatientAuthorizer, Reason
from app.config import Settings

from .oauth_helper import BASE, load_env, load_fixture

pytestmark = pytest.mark.live


def issue(user_uuid: str) -> str:
    env = load_env()
    out = subprocess.check_output([
        "docker", "exec", "-u", "apache", "development-easy-light-openemr-1", "php",
        "/var/www/localhost/htdocs/openemr/scripts/copilot_issue_probe.php", env["OAUTH_CLIENT_ID"], user_uuid,
    ], text=True)
    return out.strip().splitlines()[-1]


@pytest.fixture(scope="module")
def fx():
    return load_fixture()


@pytest.fixture(scope="module")
def authorizer():
    env = load_env()
    return PatientAuthorizer(Settings(openemr_base_url=BASE, openemr_verify_tls=False,
                                      oauth_client_id=env["OAUTH_CLIENT_ID"],
                                      oauth_client_secret=env["OAUTH_CLIENT_SECRET"]))


async def test_issued_token_subject_is_the_session_user_and_is_active(fx, authorizer):
    tok = issue(fx["user_uuids"]["copilot_doc_a"])
    info = await authorizer.introspect(tok)
    assert info and info["active"] is True and info["sub"] == fx["user_uuids"]["copilot_doc_a"]


async def test_issued_token_passes_gate_for_own_patient_and_fails_for_others(fx, authorizer):
    a = fx["user_uuids"]["copilot_doc_a"]
    tok = issue(a)
    assert (await authorizer.authorize(tok, fx["patient_uuids"]["100"], a)).allowed
    assert (await authorizer.authorize(tok, fx["patient_uuids"]["101"], a)).reason is Reason.NOT_ON_CARE_TEAM


async def test_issued_token_for_nurse_is_denied(fx, authorizer):
    n = fx["user_uuids"]["copilot_nurse"]
    assert not (await authorizer.authorize(issue(n), fx["patient_uuids"]["100"], n)).allowed


async def test_issued_token_is_read_only_scoped(fx, authorizer):
    info = await authorizer.introspect(issue(fx["user_uuids"]["copilot_doc_a"]))
    assert info and not any(s.endswith((".write", ".cud", ".c", ".u", ".d")) for s in info["scope"].split())
