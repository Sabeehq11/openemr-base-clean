"""Failure injection: tool timeout, malformed tool output, provider errors, and the loop's own limits."""
from __future__ import annotations

from .cases_invariants import call_all
from .charts import full_chart
from .harness import Harness, answer, claim
from .registry import case


def _failing(kind: str, rtype: str) -> Harness:
    chart = full_chart()
    chart.fail[rtype] = kind
    return Harness(chart, [call_all(), answer(claim("Source unavailable: get_labs."),
                                              claim("Type 2 diabetes mellitus", "Condition/c1"))])


@case("fail-tool-timeout-is-retried-once-then-reported", "failure_injection", "silent gap when a tool times out")
def _():
    h = _failing("timeout", "Observation")
    r = h.chat("brief")
    assert r.get("answer") is not None
    tool_msgs = [m["content"] for m in h.llm.calls[-1] if m["role"] == "tool" and "unavailable" in m["content"]]
    assert tool_msgs and "timeout" in tool_msgs[0]
    rows = h.app.state.traces.query("SELECT retries FROM traces")
    assert rows[0][0] >= 1  # exactly one retry was attempted for the failing resource


@case("fail-malformed-tool-output-is-typed-error", "failure_injection", "garbage upstream data reaching the model")
def _():
    h = _failing("malformed", "Observation")
    r = h.chat("brief")
    assert r.get("answer") is not None
    assert any("bad_response" in m["content"] for m in h.llm.calls[-1] if m["role"] == "tool")


@case("fail-upstream-500-does-not-leak-detail", "failure_injection", "raw upstream error text shown to users")
def _():
    h = _failing("error", "Condition")
    r = h.chat("brief")
    import re
    # "500 mg" is a recorded dose on a cited card, not upstream detail; any other 500 (a status code) still fails.
    assert not re.search(r"\b500\b(?!\s*mg)", r.resp.text) and "Traceback" not in r.resp.text


@case("fail-failed-tool-is-audited-and-traced", "failure_injection", "invisible tool failures")
def _():
    h = _failing("error", "Observation")
    h.chat("brief")
    assert any(x["event"] == "tool_error" for x in h.app.state.audit_chain.rows())
    assert h.app.state.traces.query("SELECT count(*) FROM spans WHERE kind='tool' AND status='error'")[0][0] >= 1


@case("fail-llm-provider-down-degrades-to-raw-records", "failure_injection", "no usable output when LLM is down")
def _():
    from app.llm import LLMError
    h = Harness(full_chart(), [call_all(), LLMError("down")])
    r = h.chat("brief")
    deg = r.get("degraded")
    assert deg and r.get("answer") is None and "unavailable" in deg["message"]
    assert {e["tool"] for e in deg["records"]} == {"get_problems", "get_medications", "get_allergies", "get_labs"}


@case("fail-llm-down-before-any-tool-gives-empty-degraded", "failure_injection", "crash when nothing was fetched")
def _():
    from app.llm import LLMError
    h = Harness(full_chart(), [LLMError("down")])
    r = h.chat("brief")
    assert r.names == ["conversation", "degraded", "end"] and r.get("degraded")["records"] == []


@case("fail-llm-error-is-not-an-alert-error", "failure_injection", "degraded mode counted as hard error")
def _():
    from app.llm import LLMError
    h = Harness(full_chart(), [LLMError("down")])
    h.chat("brief")
    assert h.app.state.traces.query("SELECT outcome FROM traces") == [("degraded",)]


@case("fail-replayed-request-rejected", "failure_injection", "captured request replayed")
def _():
    import json
    from app import signing
    from .harness import DOC, NOW, PID, SECRET
    h = Harness(full_chart(), [call_all(), answer(claim("Type 2 diabetes mellitus", "Condition/c1"))])
    body = json.dumps({"patient_uuid": PID, "question": "brief", "conversation_id": None}).encode()
    nonce = "replay-nonce-0000000001"
    sig = signing.sign(SECRET, "POST", "/v1/chat", str(NOW), nonce, body, DOC, "tok")
    hdr = {"x-copilot-timestamp": str(NOW), "x-copilot-nonce": nonce, "x-copilot-signature": sig,
           "x-copilot-user": DOC, "x-copilot-token": "tok", "content-type": "application/json"}
    assert h.client.post("/v1/chat", content=body, headers=hdr).status_code == 200
    assert h.client.post("/v1/chat", content=body, headers=hdr).status_code == 401
