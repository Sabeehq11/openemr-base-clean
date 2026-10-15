"""Supervisor: routing table, caps, worker failure paths, and one logged event per handoff."""
import asyncio
import json
import logging

from app.logging_setup import JsonFormatter, correlation_id_var
from app.w2.supervisor import Supervisor, next_node
from contracts.w2 import GraphState, HandoffReason, Node, WorkerResult, WorkerStatus


def ok(node, refs=(), code=None, status=WorkerStatus.OK):
    async def fn():
        return WorkerResult(worker=node, status=status, refs=list(refs), error_code=code)
    return fn


def all_ok():
    return {n: ok(n) for n in (Node.INTAKE_EXTRACTOR, Node.CHART_FACTS, Node.EVIDENCE_RETRIEVER)}


def test_routing_table():  # guards: a wrong or reordered route
    s = GraphState()
    assert next_node(s).node is Node.INTAKE_EXTRACTOR
    s = s.model_copy(update={"docs_done": True})
    assert next_node(s).node is Node.CHART_FACTS
    s = s.model_copy(update={"chart_done": True, "wants_evidence": True})
    assert next_node(s).node is Node.EVIDENCE_RETRIEVER
    s = s.model_copy(update={"evidence_done": True})
    d = next_node(s)
    assert (d.node, d.reason) == (Node.SYNTHESIS, HandoffReason.READY)


def test_no_evidence_wanted_goes_straight_to_ready():  # guards: needless retrieval
    s = GraphState(docs_done=True, chart_done=True, wants_evidence=False)
    assert next_node(s).node is Node.SYNTHESIS


async def test_full_run_visits_each_node_once_in_order():  # guards: loops and repeated work
    out = await Supervisor(all_ok(), lambda s: True).run()
    assert out.end == "ready"
    visited = [h.to_node for h in out.handoffs if h.from_node is Node.SUPERVISOR]
    assert visited == [Node.INTAKE_EXTRACTOR, Node.CHART_FACTS, Node.EVIDENCE_RETRIEVER, Node.SYNTHESIS]


async def test_failed_worker_is_recorded_not_retried():  # guards: retry loops on a failed worker
    calls = {"n": 0}

    async def failing():
        calls["n"] += 1
        return WorkerResult(worker=Node.EVIDENCE_RETRIEVER, status=WorkerStatus.FAILED, error_code="boom")

    nodes = {**all_ok(), Node.EVIDENCE_RETRIEVER: failing}
    out = await Supervisor(nodes, lambda s: True).run()
    assert calls["n"] == 1 and out.end == "ready" and out.missing == [Node.EVIDENCE_RETRIEVER]


async def test_raising_worker_becomes_failed_result():  # guards: one worker exception taking the request down
    async def boom():
        raise RuntimeError("secret detail")

    out = await Supervisor({**all_ok(), Node.INTAKE_EXTRACTOR: boom}, lambda s: False).run()
    r = out.state.result_for(Node.INTAKE_EXTRACTOR)
    assert r.status is WorkerStatus.FAILED and r.error_code == "worker_error"
    assert out.end == "ready"


async def test_slow_worker_times_out():  # guards: a hang inside a worker
    async def slow():
        await asyncio.sleep(5)

    out = await Supervisor({**all_ok(), Node.INTAKE_EXTRACTOR: slow}, lambda s: False, worker_timeout_s=0.05).run()
    assert out.state.result_for(Node.INTAKE_EXTRACTOR).status is WorkerStatus.TIMEOUT
    assert Node.INTAKE_EXTRACTOR in out.missing


async def test_step_cap_ends_with_cap_handoff():  # guards: unbounded step count
    out = await Supervisor(all_ok(), lambda s: True, max_steps=2).run()
    assert out.end == "step_cap"
    assert out.handoffs[-1].reason is HandoffReason.STEP_CAP


async def test_deadline_ends_with_deadline_handoff():  # guards: unbounded total time
    t = [0.0]

    def clock():
        t[0] += 30.0
        return t[0]

    out = await Supervisor(all_ok(), lambda s: True, clock=clock, deadline_s=40.0).run()
    assert out.end == "deadline" and out.handoffs[-1].reason is HandoffReason.DEADLINE


async def test_denied_result_halts_the_run():  # guards: retrieval or synthesis after a denial
    nodes = {**all_ok(), Node.CHART_FACTS: ok(Node.CHART_FACTS, code="denied", status=WorkerStatus.FAILED)}
    out = await Supervisor(nodes, lambda s: True).run()
    assert out.end == "halted"
    assert out.state.result_for(Node.EVIDENCE_RETRIEVER) is None


async def test_one_log_event_per_handoff_with_correlation_id_and_no_values(caplog):
    # guards: untraceable routes, and values or text leaking into handoff events
    token = correlation_id_var.set("corr-12345678")
    try:
        with caplog.at_level(logging.INFO, logger="agent.w2.supervisor"):
            out = await Supervisor(all_ok(), lambda s: True).run()
        # The formatter reads the correlation id when it formats, so format while it is still set.
        events = [json.loads(JsonFormatter().format(r)) for r in caplog.records if r.getMessage() == "w2_handoff"]
    finally:
        correlation_id_var.reset(token)
    assert len(events) == len(out.handoffs)
    assert all(e["correlation_id"] == "corr-12345678" for e in events)
    assert all(set(e) <= {"ts", "level", "logger", "correlation_id", "msg", "from_node", "to_node", "reason", "step"}
               for e in events)
    assert [e["step"] for e in events] == sorted(e["step"] for e in events)
