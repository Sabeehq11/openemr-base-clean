"""POST /v1/w2/chat end to end with stubs: groups, chart-claim preservation, degraded paths, denial, trace."""
import json
import logging

from app.chat import fail_closed_finalizer
from app.llm import LLMError, UnavailableLLM
from app.logging_setup import JsonFormatter, correlation_id_var
from tests.w2_chat_helpers import OTHER_PATIENT, Q, TwoStageLLM, ask, build, events, stage_b


def answer_of(r):
    return dict(events(r))["answer"]


def test_answer_has_three_separate_groups(tmp_path):  # guards: chart, record and guideline claims blended
    probe, _ = build(tmp_path / "p", TwoStageLLM(LLMError("down")))
    chunks = answer_of(ask(probe, Q))["guideline_claims"]  # the unsynthesised fallback lists the retrieved snippets
    chunk_id = chunks[0]["citations"][0]["field_or_chunk_id"]
    assert chunk_id
    llm = TwoStageLLM(stage_b(("Hemoglobin A1c is 7.9 %.", ["doc:7#0"]),
                              ("Published guidance on A1c testing frequency applies.", [chunk_id])))
    c, audit = build(tmp_path / "q", llm)
    ev = events(ask(c, Q))
    assert [n for n, _ in ev][0] == "conversation" and [n for n, _ in ev][-2:] == ["answer", "end"]
    a = dict(ev)["answer"]
    assert a["chart"]["claims"] and a["document_claims"] and a["guideline_claims"]
    assert a["guideline_claims"][0]["citations"][0]["source_type"] == "guideline"
    assert a["document_claims"][0]["citations"][0]["source_type"] == "extracted"
    assert a["document_claims"][0]["citations"][0]["bbox"]
    assert audit.events[-1][0] == "w2_chat_finished" and audit.events[-1][1]["outcome"] == "answer"


def test_chart_group_equals_stage_a_and_ignores_stage_b(tmp_path):
    # guards: Stage B restating, contradicting or citing chart claims changing the chart group
    plain, _ = build(tmp_path / "a", TwoStageLLM(stage_b(("Hemoglobin A1c is 7.9 %.", ["doc:7#0"]))))
    base = answer_of(ask(plain, Q))["chart"]
    hostile = TwoStageLLM(stage_b(("Observation item is normal.", ["Observation/r1"]),
                                  ("Condition item resolved.", ["Condition/r1"]),
                                  ("Hemoglobin A1c is 7.9 %.", ["doc:7#0"])))
    c, _ = build(tmp_path / "b", hostile)
    a = answer_of(ask(c, Q))
    assert a["chart"] == base
    assert a["rejections"].get("unknown_source") == 2
    assert all("Observation/r1" not in m["content"] for call in hostile.b_calls for m in call)  # Stage B never saw it


def test_stage_b_outage_keeps_chart_and_shows_recorded_facts(tmp_path):
    # guards: a Stage B outage removing or altering the chart group
    plain, _ = build(tmp_path / "a", TwoStageLLM(stage_b(("Hemoglobin A1c is 7.9 %.", ["doc:7#0"]))))
    base = answer_of(ask(plain, Q))["chart"]
    c, _ = build(tmp_path / "b", TwoStageLLM(LLMError("down")))
    a = answer_of(ask(c, Q))
    assert a["chart"] == base and a["unsynthesised"] and a["document_claims"]
    assert any("language model was unavailable" in n for n in a["notes"])


def test_no_documents_is_stated_not_invented(tmp_path):  # guards: an empty document set shown as silence
    c, _ = build(tmp_path, TwoStageLLM(stage_b(("x", []))), upload=False)
    a = answer_of(ask(c, "list the allergies"))
    assert not a["document_claims"] and any("No uploaded documents" in n for n in a["notes"])


def test_no_evidence_names_the_missing_source(tmp_path):  # guards: empty retrieval looking like a complete answer
    c, _ = build(tmp_path, TwoStageLLM(stage_b(("x", []))), upload=False)
    a = answer_of(ask(c, "What is the best pizza topping guideline in chicago"))
    assert not a["guideline_claims"] and any("No guideline evidence" in n for n in a["notes"])


def test_denied_caller_gets_denial_and_no_stored_read(tmp_path):  # guards: stored facts read before authorization
    c, audit = build(tmp_path, TwoStageLLM(stage_b(("x", []))))
    ev = events(ask(c, Q, patient=OTHER_PATIENT))
    assert [n for n, _ in ev] == ["conversation", "denied", "end"]
    assert audit.events[-1][1]["outcome"] == "denied"


def test_advice_request_is_declined_before_any_second_model_call(tmp_path):  # guards: advice reaching Stage B
    llm = TwoStageLLM(stage_b(("x", [])))
    c, _ = build(tmp_path, llm)
    ev = events(ask(c, "Should I increase the metformin dose?"))
    assert "declined" in dict(ev) and "answer" not in dict(ev) and llm.b_calls == []


def test_model_outage_returns_degraded_chart_only(tmp_path):  # guards: prose from a down model, or a second model call
    c, audit = build(tmp_path, UnavailableLLM_())
    ev = events(ask(c, Q))
    assert [n for n, _ in ev] == ["conversation", "degraded", "end"]
    assert audit.events[-1][1]["outcome"] == "degraded"


def test_unverified_chart_draft_is_never_released(tmp_path):  # guards: the Week 1 verifier being bypassed
    c, _ = build(tmp_path, TwoStageLLM(stage_b(("x", []))), finalizer=fail_closed_finalizer)
    assert answer_of(ask(c, Q))["chart"]["claims"] == []


def test_chart_failure_still_answers_and_names_the_gap(tmp_path):  # guards: a hang or silent gap when the chart is down
    c, _ = build(tmp_path, TwoStageLLM(stage_b(("Hemoglobin A1c is 7.9 %.", ["doc:7#0"]))), fhir_down=True)
    ev = events(ask(c, Q))
    assert ev[-2][0] in ("answer", "degraded", "error") and ev[-1][0] == "end"


def test_correlation_id_rebuilds_the_route_from_logs(tmp_path, caplog):  # guards: untraceable handoffs
    c, _ = build(tmp_path, TwoStageLLM(stage_b(("Hemoglobin A1c is 7.9 %.", ["doc:7#0"]))))
    with caplog.at_level(logging.INFO, logger="agent.w2.supervisor"):
        r = c.post("/v1/w2/chat", content=b"")  # unsigned: rejected, adds no handoffs
        assert r.status_code == 401
        ask(c, Q)
    lines = [json.loads(JsonFormatter().format(rec)) for rec in caplog.records if rec.getMessage() == "w2_handoff"]
    route = [(e["from_node"], e["to_node"]) for e in lines]
    assert route[0] == ("supervisor", "intake_extractor") and route[-1] == ("supervisor", "synthesis")
    assert ("chart_facts", "supervisor") in route and ("evidence_retriever", "supervisor") in route


class UnavailableLLM_:
    async def complete(self, messages, tools):
        raise LLMError("language model is unavailable")


def test_chat_route_is_mounted_only_when_w2_is_enabled_and_needs_a_signature(tmp_path):
    # guards: a doubled prefix or an unsigned route at the real app seam
    from fastapi.testclient import TestClient
    from pydantic import SecretStr
    from app.config import Settings
    from app.main import create_app
    off = TestClient(create_app(Settings(data_dir=str(tmp_path / "off"), w2_enabled=False)))
    assert off.post("/v1/w2/chat", json={}).status_code == 404
    s = Settings(data_dir=str(tmp_path / "on"), w2_enabled=True, w2_use_stub_vlm=True,
                 module_signing_secret=SecretStr("x"))
    on = TestClient(create_app(s))
    assert on.post("/v1/w2/chat", json={}).status_code == 401
    assert on.post("/v1/w2/v1/w2/chat", json={}).status_code == 404


def test_each_request_leaves_a_trace_with_its_outcome(tmp_path):  # guards: W2 requests missing from traces
    from app.tracing import TraceStore
    ts = TraceStore(str(tmp_path / "traces.db"))
    c, _ = build(tmp_path, TwoStageLLM(stage_b(("Hemoglobin A1c is 7.9 %.", ["doc:7#0"]))), traces=ts)
    assert "answer" in dict(events(ask(c, Q)))
    rows = ts.query("select outcome from traces")
    assert rows == [("answer",)]


def test_genetic_report_question_says_none_on_file_despite_other_documents(tmp_path):  # guards: unrelated lab/intake facts answering for a missing report
    c, _ = build(tmp_path, TwoStageLLM(stage_b(("x", []))), upload=True)
    a = answer_of(ask(c, "What did the uploaded genetic testing report show?"))
    assert not a["document_claims"]
    assert any("No genetic report is on file" in n for n in a["notes"])
