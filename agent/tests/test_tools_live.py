"""Tools against the live seeded OpenEMR, using real user-scoped tokens. Run with OE_LIVE=1."""
import pytest

from app.authz import PatientAuthorizer
from app.config import Settings
from app.fhir_client import FhirClient
from app.tools import DeniedError, ToolRunner
from contracts import ToolName

from .oauth_helper import BASE, get_token, load_env, load_fixture

pytestmark = pytest.mark.live

SCOPES = ("openid fhirUser user/Patient.rs user/CareTeam.rs user/Condition.rs user/MedicationRequest.rs "
          "user/AllergyIntolerance.rs user/Observation.rs user/Encounter.rs")


@pytest.fixture(scope="module")
def fx():
    return load_fixture()


@pytest.fixture(scope="module")
def runner():
    env = load_env()
    s = Settings(openemr_base_url=BASE, openemr_verify_tls=False, tool_timeout_s=15,
                 oauth_client_id=env["OAUTH_CLIENT_ID"], oauth_client_secret=env["OAUTH_CLIENT_SECRET"])
    return ToolRunner(PatientAuthorizer(s), FhirClient(s))


@pytest.fixture(scope="module")
def tok_a():
    return get_token("copilot_doc_a", SCOPES)["access_token"]


@pytest.fixture(scope="module")
def tok_b():
    return get_token("copilot_doc_b", SCOPES)["access_token"]


@pytest.fixture(scope="module")
def tok_nurse():
    return get_token("copilot_nurse", SCOPES)["access_token"]


async def test_summary_for_own_patient(runner, fx, tok_a):
    env = await runner.run(ToolName.GET_PATIENT_SUMMARY, tok_a, fx["patient_uuids"]["100"])
    p = env.records[0]
    assert p.patient_uuid == fx["patient_uuids"]["100"] and p.name


@pytest.mark.parametrize("tool", [t for t in ToolName if t is not ToolName.GET_CHANGES_SINCE])
async def test_each_tool_returns_typed_envelope_for_own_patient(runner, fx, tok_a, tool):
    env = await runner.run(tool, tok_a, fx["patient_uuids"]["100"])
    assert env.patient_uuid == fx["patient_uuids"]["100"]
    assert env.empty == (not env.records)
    assert all(sid.split("/")[1] for sid in env.source_ids)


async def test_changes_since_over_wide_window(runner, fx, tok_a):
    env = await runner.run(ToolName.GET_CHANGES_SINCE, tok_a, fx["patient_uuids"]["100"], since="2000-01-01")
    assert env.tool is ToolName.GET_CHANGES_SINCE


async def test_coverage_across_seeded_patients_has_clinical_data(runner, fx, tok_a, tok_b):
    """Verify FHIR coverage: across the seeded cohort at least one patient has each record kind."""
    seen = {t: 0 for t in ToolName if t not in (ToolName.GET_PATIENT_SUMMARY, ToolName.GET_CHANGES_SINCE)}
    for pid, owner in fx["patient_owner"].items():
        tok = tok_a if owner == fx["user_ids"]["copilot_doc_a"] else tok_b if owner == fx["user_ids"]["copilot_doc_b"] else None
        if tok is None:
            continue
        for tool in seen:
            env = await runner.run(tool, tok, fx["patient_uuids"][pid])
            seen[tool] += len(env.records)
    print("FHIR coverage counts:", {k.value: v for k, v in seen.items()})
    assert all(v > 0 for v in seen.values()), seen


async def test_physician_cannot_read_other_physicians_patient_through_any_tool(runner, fx, tok_a):
    b_patient = fx["patient_uuids"]["101"]
    for tool in ToolName:
        kwargs = {"since": "2000-01-01"} if tool is ToolName.GET_CHANGES_SINCE else {}
        with pytest.raises(DeniedError):
            await runner.run(tool, tok_a, b_patient, **kwargs)


async def test_nurse_is_denied_for_every_tool(runner, fx, tok_nurse):
    for tool in ToolName:
        kwargs = {"since": "2000-01-01"} if tool is ToolName.GET_CHANGES_SINCE else {}
        with pytest.raises(DeniedError):
            await runner.run(tool, tok_nurse, fx["patient_uuids"]["100"], **kwargs)
