import json

import httpx
import pytest

from app.authz import AuthzDecision, Reason
from app.config import Settings
from app.conversation import ConversationStore, Snapshot
from app.fhir_client import FhirClient
from app.llm import LLMError, LLMResponse, StubLLM, ToolCall, Usage
from app.loop import AgentLoop, Caller, parse_draft
from app.tools import ToolRunner
from contracts import ToolName

USER = "aaaaaaaa-1111-2222-3333-444444444444"
PID = "11111111-2222-3333-4444-555555555555"
GOOD = json.dumps({"claims": [{"text": "Problem: HTN", "kind": "fact", "source_ids": ["Condition/c1"]}]})


class Authz:
    def __init__(self, allow=True):
        self.allow, self.calls = allow, 0

    async def authorize(self, token, patient_uuid, claimed_user_uuid=None):
        self.calls += 1
        if self.allow:
            return AuthzDecision(True, Reason.ALLOWED, USER, patient_uuid)
        return AuthzDecision(False, Reason.NOT_ON_CARE_TEAM, USER)


def build(script, allow=True, fhir_status=200, **kw):
    fhir_calls = []

    def h(req):
        fhir_calls.append(req.url.path)
        res = req.url.path.rsplit("/", 1)[-1]
        if res == PID:
            return httpx.Response(200, json={"resourceType": "Patient", "id": PID, "birthDate": "1970-01-01",
                                             "name": [{"given": ["Real"], "family": "Name"}]})
        if fhir_status != 200:
            return httpx.Response(fhir_status)
        return httpx.Response(200, json={"resourceType": "Bundle", "entry": [
            {"resource": {"resourceType": res, "id": "c1", "code": {"text": "HTN"}}}]})

    s = Settings(openemr_base_url="http://oe", tool_timeout_s=1)
    authz = Authz(allow)
    runner = ToolRunner(authz, FhirClient(s, transport=httpx.MockTransport(h)))
    llm = StubLLM(script)
    conv = ConversationStore().open(None, USER, PID)
    return AgentLoop(runner, llm, **kw), llm, conv, authz, fhir_calls


async def run(loop, conv, q="brief"):
    return [e async for e in loop.run(conv, Caller(USER, "tok"), q)]


def call(name, i=1, **args):
    return LLMResponse(tool_calls=[ToolCall(f"t{i}", name, args)])


def final(text=GOOD):
    return LLMResponse(text=text, usage=Usage(10, 5, 0.001))


async def test_tool_call_then_final_answer():
    loop, llm, conv, _, _ = build([call("get_problems"), final()])
    ev = await run(loop, conv)
    assert [e["type"] for e in ev] == ["step", "done"]
    done = ev[-1]
    assert done["draft"].claims[0].source_ids == ["Condition/c1"]
    assert [e.tool for e in done["envelopes"]] == [ToolName.GET_PROBLEMS]
    assert done["usage"].cost_usd == pytest.approx(0.001)


async def test_model_sees_tool_data_but_not_name_or_birth_date():
    loop, llm, conv, _, _ = build([call("get_patient_summary"), final()])
    await run(loop, conv)
    tool_msg = [m for m in llm.calls[1] if m["role"] == "tool"][0]["content"]
    assert "Real" not in tool_msg and "1970" not in tool_msg
    # The opaque record id stays (the model must cite it) but only inside source ids, never as a standalone field.
    assert tool_msg.count(PID) == 2 and '"patient_uuid"' not in tool_msg


async def test_tool_arguments_cannot_change_the_patient():
    loop, llm, conv, _, calls = build([call("get_problems", patient_uuid="99999999-0000-0000-0000-000000000000"), final()])
    await run(loop, conv)
    assert calls and all("99999999" not in c for c in calls)


async def test_unknown_tool_is_reported_to_model_not_executed():
    loop, llm, conv, _, calls = build([call("delete_patient"), final()])
    ev = await run(loop, conv)
    assert calls == [] and ev[-1]["type"] == "done"


async def test_step_cap_forces_final_answer_and_notes_it():
    script = [call("get_problems", i) for i in range(1, 4)] + [final()]
    loop, llm, conv, _, _ = build(script, max_steps=2)
    ev = await run(loop, conv)
    done = ev[-1]
    assert done["type"] == "done" and done["steps"] == 2 and done["notes"]
    assert llm.calls[-1][-1]["role"] == "system"


async def test_deadline_forces_final_answer():
    t = iter([0, 0, 100, 100, 100, 100, 100, 100])
    loop, _, conv, _, _ = build([call("get_problems"), final()], clock=lambda: next(t))
    ev = await run(loop, conv)
    assert ev[-1]["type"] == "done" and ev[-1]["notes"]


async def test_invalid_structure_retried_once_then_succeeds():
    loop, llm, conv, _, _ = build([final("not json"), final()])
    assert (await run(loop, conv))[-1]["type"] == "done"
    assert len(llm.calls) == 2


async def test_invalid_structure_twice_fails_safely():
    loop, _, conv, _, _ = build([final("nope"), final("still nope")])
    assert (await run(loop, conv))[-1]["type"] == "failed"


async def test_llm_down_returns_degraded_with_raw_envelopes():
    loop, _, conv, _, _ = build([call("get_problems"), LLMError("down")])
    ev = await run(loop, conv)
    assert ev[-1]["type"] == "degraded" and ev[-1]["envelopes"][0].tool is ToolName.GET_PROBLEMS


async def test_llm_down_immediately_is_degraded_with_no_data():
    loop, _, conv, _, _ = build([LLMError("down")])
    assert (await run(loop, conv))[-1] == {"type": "degraded", "envelopes": [], "usage": Usage()}


async def test_denied_patient_stops_before_the_model_or_fhir_is_called():
    loop, llm, conv, _, calls = build([final()], allow=False)
    ev = await run(loop, conv)
    assert ev == [{"type": "denied"}] and llm.calls == [] and calls == []


async def test_tool_failure_is_passed_to_model_as_unavailable_source():
    loop, llm, conv, _, _ = build([call("get_problems"), final()], fhir_status=500)
    ev = await run(loop, conv)
    tool_msg = [m for m in llm.calls[1] if m["role"] == "tool"][0]["content"]
    assert "unavailable" in tool_msg and ev[-1]["type"] == "done"


async def test_snapshot_reuse_avoids_refetch_but_still_reauthorizes():
    loop, _, conv, authz, calls = build([call("get_problems"), final(), call("get_problems", 2), final()])
    await run(loop, conv)
    fetched_once, auth_once = len(calls), authz.calls
    await run(loop, conv, "follow up")
    assert len(calls) == fetched_once
    assert authz.calls > auth_once + 1


async def test_cached_snapshot_is_not_served_after_authorization_is_revoked():
    loop, llm, conv, authz, calls = build([call("get_problems"), final(), final()])
    await run(loop, conv)
    authz.allow = False
    ev = await run(loop, conv, "follow up")
    assert ev == [{"type": "denied"}] and len(llm.calls) == 2


def test_snapshot_expires_after_ttl():
    now = [0.0]
    snap = Snapshot(ttl_s=300, clock=lambda: now[0])
    env = object()
    snap.put(ToolName.GET_LABS, {}, env)
    assert snap.get(ToolName.GET_LABS, {}) is env
    now[0] = 301
    assert snap.get(ToolName.GET_LABS, {}) is None and snap.fresh() == []


def test_conversation_is_bound_to_user_and_patient():
    store = ConversationStore()
    c = store.open(None, USER, PID)
    assert store.open(c.id, USER, PID) is c
    assert store.open(c.id, USER, "22222222-2222-3333-4444-555555555555") is not c
    assert store.open(c.id, "bbbbbbbb-1111-2222-3333-444444444444", PID) is not c


def test_parse_draft_accepts_fenced_json_and_rejects_extras():
    assert parse_draft("```json\n" + GOOD + "\n```") is not None
    assert parse_draft('{"claims": [], "extra": 1}') is None
    assert parse_draft('{"claims": [{"text": "", "kind": "fact"}]}') is None
    assert parse_draft('{"claims": [{"text": "x", "kind": "diagnosis"}]}') is None
    assert parse_draft(None) is None
