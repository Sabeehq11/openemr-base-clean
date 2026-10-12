import json

import httpx
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr

from app import signing
from app.authz import AuthzDecision, Reason
from app.chat import build_chat_router, fail_closed_finalizer
from app.config import Settings
from app.conversation import ConversationStore
from app.fhir_client import FhirClient
from app.gateway import Gateway
from app.llm import DemoStubLLM, LLMResponse, StubLLM, UnavailableLLM
from app.tools import ToolRunner
from contracts import VerifiedAnswer, VerifiedClaim, ClaimKind

SECRET = "s"
USER = "aaaaaaaa-1111-2222-3333-444444444444"
PID = "11111111-2222-3333-4444-555555555555"
NOW = 1_800_000_000
_n = [0]


class Authz:
    def __init__(self, allow=True):
        self.allow = allow

    async def authorize(self, token, patient_uuid, claimed_user_uuid=None):
        if self.allow:
            return AuthzDecision(True, Reason.ALLOWED, USER, patient_uuid)
        return AuthzDecision(False, Reason.NOT_ON_CARE_TEAM, USER)


class Audit:
    def __init__(self):
        self.events = []

    def record(self, event, **f):
        self.events.append((event, f))


def passthrough(draft, envs):
    return VerifiedAnswer(claims=[VerifiedClaim(text=c.text, source_ids=c.source_ids, kind=c.kind) for c in draft.claims])


def build(llm=None, allow=True, finalizer=passthrough, empty=False):
    def h(req):
        res = req.url.path.rsplit("/", 1)[-1]
        entries = [] if empty else [{"resource": {"resourceType": res, "id": "r1", "code": {"text": f"{res} item"}}}]
        return httpx.Response(200, json={"resourceType": "Bundle", "entry": entries})

    s = Settings(module_signing_secret=SecretStr(SECRET), openemr_base_url="http://oe")
    audit = Audit()
    runner = ToolRunner(Authz(allow), FhirClient(s, transport=httpx.MockTransport(h)))
    app = FastAPI()
    app.include_router(build_chat_router(Gateway(s, audit, clock=lambda: NOW), runner,
                                         lambda: llm or DemoStubLLM(), finalizer, ConversationStore(), audit))
    return TestClient(app), audit


def chat(client, question="brief", patient=PID, conv=None):
    _n[0] += 1
    body = json.dumps({"patient_uuid": patient, "question": question, "conversation_id": conv}).encode()
    nonce = f"chat-nonce-{_n[0]:08d}"
    sig = signing.sign(SECRET, "POST", "/v1/chat", str(NOW), nonce, body, USER, "tok")
    return client.post("/v1/chat", content=body, headers={
        "x-copilot-timestamp": str(NOW), "x-copilot-nonce": nonce, "x-copilot-signature": sig,
        "x-copilot-user": USER, "x-copilot-token": "tok", "content-type": "application/json"})


def events(r):
    out = []
    for block in r.text.strip().split("\n\n"):
        name, data = block.split("\n")
        out.append((name.removeprefix("event: "), json.loads(data.removeprefix("data: "))))
    return out


def test_streams_conversation_steps_answer_then_end():
    c, audit = build()
    r = chat(c)
    assert r.headers["content-type"].startswith("text/event-stream")
    names = [n for n, _ in events(r)]
    assert names == ["conversation", "step", "step", "step", "step", "answer", "end"]
    answer = dict(events(r))["answer"]
    assert {"Condition/r1", "MedicationRequest/r1", "AllergyIntolerance/r1", "Observation/r1"} <= {
        s for cl in answer["claims"] for s in cl["source_ids"]}
    assert audit.events[-1][1]["outcome"] == "answer"


def test_empty_chart_is_stated_as_absence_with_no_sources():
    c, _ = build(empty=True)
    answer = dict(events(chat(c)))["answer"]
    assert all(cl["kind"] == "absence" and "documented" in cl["text"] for cl in answer["claims"])


def test_unverified_draft_is_never_released_without_a_verifier():
    c, _ = build(finalizer=fail_closed_finalizer)
    answer = dict(events(chat(c)))["answer"]
    assert answer["claims"] == [] and answer["dropped_count"] > 0


def test_llm_outage_returns_degraded_raw_records_not_prose():
    c, audit = build(llm=UnavailableLLM())
    ev = events(chat(c))
    assert [n for n, _ in ev] == ["conversation", "degraded", "end"]
    assert audit.events[-1][1]["outcome"] == "degraded"


def test_denied_patient_gets_one_generic_event_and_no_data():
    c, audit = build(allow=False)
    ev = events(chat(c))
    assert [n for n, _ in ev] == ["conversation", "denied", "end"]
    assert ev[1][1] == {"message": "You do not have access to this patient."}
    assert audit.events[-1][1]["outcome"] == "denied"


def test_invalid_model_output_yields_single_error_event():
    bad = StubLLM([LLMResponse(text="nope"), LLMResponse(text="nope again")])
    c, audit = build(llm=bad)
    names = [n for n, _ in events(chat(c))]
    assert names.count("error") == 1 and names[-1] == "end" and "answer" not in names


def test_conversation_id_is_reused_for_same_patient_and_new_for_other():
    c, _ = build()
    first = dict(events(chat(c)))["conversation"]["id"]
    assert dict(events(chat(c, conv=first)))["conversation"]["id"] == first
    other = "99999999-2222-3333-4444-555555555555"
    assert dict(events(chat(c, patient=other, conv=first)))["conversation"]["id"] != first


def test_malformed_chat_requests_are_400_after_signature_check():
    c, _ = build()
    assert chat(c, question="").status_code == 400
    assert chat(c, question="x" * 1001).status_code == 400
    assert c.post("/v1/chat", json={"patient_uuid": PID, "question": "hi"}).status_code == 401


def test_real_verifier_drops_an_invented_value_end_to_end():
    from app.verifier import Verifier
    invented = json.dumps({"claims": [
        {"text": "Problem: Condition item", "kind": "fact", "source_ids": ["Condition/r1"]},
        {"text": "Potassium was 9.9", "kind": "fact", "source_ids": ["Observation/r1"]}]})
    from app.llm import ToolCall
    llm = StubLLM([LLMResponse(tool_calls=[ToolCall("1", "get_problems", {}), ToolCall("2", "get_labs", {})]),
                   LLMResponse(text=invented)])
    c, _ = build(llm=llm, finalizer=Verifier().verify)
    answer = dict(events(chat(c)))["answer"]
    shown = [cl["text"] for cl in answer["claims"]]
    assert "Problem: Condition item" in shown and "Potassium was 9.9" not in shown
    assert answer["dropped_count"] == 1


def test_answer_event_carries_a_card_for_every_cited_source_and_only_those():
    c, _ = build()
    answer = dict(events(chat(c)))["answer"]
    cited = {s for cl in answer["claims"] for s in cl["source_ids"]}
    assert cited and {s["source_id"] for s in answer["sources"]} == cited
    assert all(s["title"] and s["kind"] for s in answer["sources"])


def test_degraded_event_carries_cards_for_the_chart_data_it_shows():
    from app.llm import LLMError, LLMResponse, StubLLM, ToolCall

    # The model reads the chart, then goes down: the physician is shown the fetched records, each with a card.
    llm = StubLLM([LLMResponse(tool_calls=[ToolCall("t1", "get_medications", {}), ToolCall("t2", "get_problems", {})]),
                   LLMError("down")])
    c, _ = build(llm=llm)
    ev = dict(events(chat(c)))
    shown = {sid for env in ev["degraded"]["records"] for sid in env["source_ids"]}
    assert shown and {s["source_id"] for s in ev["degraded"]["sources"]} == shown


def test_answer_cards_never_leak_the_patient_identity():
    c, _ = build()
    raw = json.dumps(dict(events(chat(c)))["answer"]["sources"])
    assert "Real" not in raw and "1970-01-01" not in raw and PID not in raw


def test_advice_request_is_declined_without_calling_the_model_or_any_tool():
    from app.llm import StubLLM

    llm = StubLLM([])  # an empty script raises if the model is called at all
    c, audit = build(llm=llm)
    ev = events(chat(c, question="What dose should I raise metformin to?"))
    assert [n for n, _ in ev] == ["conversation", "declined", "end"]
    d = dict(ev)["declined"]
    assert d["kind"] == "treatment" and d["message"].startswith("I can't recommend")
    assert llm.calls == []
    assert audit.events[-1][1]["outcome"] == "declined"
    assert not any(e[0] == "tool_call" for e in audit.events)


def test_advice_request_from_an_unauthorized_caller_gets_the_generic_denial_not_the_decline():
    c, _ = build(allow=False)
    ev = dict(events(chat(c, question="What dose should I raise metformin to?")))
    assert "denied" in ev and "declined" not in ev


def test_a_dose_lookup_is_still_answered():
    c, _ = build()
    names = [n for n, _ in events(chat(c, question="What dose of metformin is she on?"))]
    assert "answer" in names and "declined" not in names
