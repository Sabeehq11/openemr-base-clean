from datetime import date

import httpx
import pytest

from app.authz import AuthzDecision, Reason
from app.config import Settings
from app.fhir_client import FhirClient
from app.tools import DeniedError, ToolRunner
from contracts import ToolError, ToolName

PID = "11111111-2222-3333-4444-555555555555"
OTHER = "99999999-2222-3333-4444-555555555555"


class FakeAuthz:
    def __init__(self, allow=True, reason=Reason.NOT_ON_CARE_TEAM):
        self.allow, self.reason, self.calls = allow, reason, []

    async def authorize(self, token, patient_uuid, claimed_user_uuid=None):
        self.calls.append((token, patient_uuid))
        if self.allow:
            return AuthzDecision(True, Reason.ALLOWED, "u1", patient_uuid)
        return AuthzDecision(False, self.reason)


def bundle(rt, *resources):
    return {"resourceType": "Bundle", "entry": [{"resource": {"resourceType": rt, **r}} for r in resources]}


def make(authz, fhir_calls=None):
    def handler(req):
        if fhir_calls is not None:
            fhir_calls.append(str(req.url))
        path = req.url.path.rsplit("/", 1)[-1]
        if path == PID:
            return httpx.Response(200, json={"resourceType": "Patient", "id": PID, "birthDate": "1980-01-01",
                                             "name": [{"given": ["A"], "family": "B"}], "gender": "male"})
        data = {
            "Condition": bundle("Condition", {"id": "c1", "code": {"text": "HTN"}, "onsetDateTime": "2026-09-20"}),
            "MedicationRequest": bundle("MedicationRequest", {"id": "m1", "status": "active", "authoredOn": "2026-09-25",
                                                               "medicationCodeableConcept": {"text": "Lisinopril"}}),
            "AllergyIntolerance": bundle("AllergyIntolerance", {"id": "a1", "code": {"text": "Penicillin"}}),
            "Observation": bundle("Observation", {"id": "o1", "code": {"text": "K"}, "valueQuantity": {"value": 4.1},
                                                  "effectiveDateTime": "2026-09-30"},
                                  {"id": "o0", "code": {"text": "Old"}, "effectiveDateTime": "2025-01-01"}),
            "Encounter": bundle("Encounter", {"id": "e1", "period": {"start": "2026-09-28"}}),
        }
        return httpx.Response(200, json=data[path])

    fhir = FhirClient(Settings(openemr_base_url="http://oe", openemr_verify_tls=False),
                      transport=httpx.MockTransport(handler))
    return ToolRunner(authz, fhir, today=lambda: date(2026, 10, 12))


ALL_TOOLS = [t for t in ToolName]


def args(tool):
    return {"since": "2026-09-01"} if tool is ToolName.GET_CHANGES_SINCE else {}


@pytest.mark.parametrize("tool", ALL_TOOLS)
async def test_every_tool_succeeds_when_allowed(tool):
    env = await make(FakeAuthz()).run(tool, "tok", PID, **args(tool))
    assert env.tool is tool and env.patient_uuid == PID
    assert env.source_ids == [r.source_id for r in env.records]


@pytest.mark.parametrize("tool", ALL_TOOLS)
async def test_every_tool_refuses_and_never_calls_fhir_when_denied(tool):
    calls = []
    runner = make(FakeAuthz(allow=False), calls)
    with pytest.raises(DeniedError) as e:
        await runner.run(tool, "tok", PID, **args(tool))
    assert calls == [] and e.value.code == "unauthorized"


@pytest.mark.parametrize("tool", ALL_TOOLS)
async def test_every_tool_refuses_without_token(tool):
    calls = []
    authz = FakeAuthz(allow=False, reason=Reason.NO_TOKEN)
    with pytest.raises(DeniedError):
        await make(authz, calls).run(tool, None, PID, **args(tool))
    assert calls == []


async def test_decision_for_a_different_patient_is_rejected():
    class Mismatch(FakeAuthz):
        async def authorize(self, token, patient_uuid, claimed_user_uuid=None):
            return AuthzDecision(True, Reason.ALLOWED, "u1", OTHER)

    calls = []
    with pytest.raises(DeniedError):
        await make(Mismatch(), calls).run(ToolName.GET_PROBLEMS, "tok", PID)
    assert calls == []


async def test_authorization_is_rechecked_on_every_call():
    authz = FakeAuthz()
    runner = make(authz)
    await runner.run(ToolName.GET_PROBLEMS, "tok", PID)
    await runner.run(ToolName.GET_PROBLEMS, "tok", PID)
    assert len(authz.calls) == 2


@pytest.mark.parametrize("bad", ["", "x", "../etc/passwd", PID + "&_id=other", "g" * 36])
async def test_malformed_patient_id_is_rejected_before_authz(bad):
    authz = FakeAuthz()
    with pytest.raises(ToolError) as e:
        await make(authz).run(ToolName.GET_PROBLEMS, "tok", bad)
    assert e.value.code == "invalid_input" and authz.calls == []


async def test_labs_sorted_newest_first():
    env = await make(FakeAuthz()).run(ToolName.GET_LABS, "tok", PID)
    assert [r.test for r in env.records] == ["K", "Old"]


async def test_empty_result_is_explicit():
    class Empty(FakeAuthz):
        pass

    runner = make(Empty())
    runner._fhir.search = lambda *a, **k: _empty()  # type: ignore[assignment]
    env = await runner.run(ToolName.GET_ALLERGIES, "tok", PID)
    assert env.empty is True and env.source_ids == []


async def _empty():
    return []


async def test_medications_note_states_lists_store_gap():
    env = await make(FakeAuthz()).run(ToolName.GET_MEDICATIONS, "tok", PID)
    assert any("lists" in n for n in env.notes)


async def test_changes_since_filters_by_window_and_sorts():
    env = await make(FakeAuthz()).run(ToolName.GET_CHANGES_SINCE, "tok", PID, since="2026-09-26")
    assert [i.source_id for i in env.records] == ["Observation/o1", "Encounter/e1"]


async def test_changes_since_requires_valid_date():
    runner = make(FakeAuthz())
    with pytest.raises(ToolError) as e:
        await runner.run(ToolName.GET_CHANGES_SINCE, "tok", PID, since="yesterday")
    assert e.value.code == "invalid_input"
    with pytest.raises(ToolError):
        await runner.run(ToolName.GET_CHANGES_SINCE, "tok", PID)
