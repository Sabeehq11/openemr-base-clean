"""The measurement harness's spending guard. A paid run must be impossible without explicit, matching approval."""
import importlib.util
import sqlite3
import sys
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location("w2_measure", Path(__file__).resolve().parents[2] / "scripts" / "w2" / "measure.py")
m = importlib.util.module_from_spec(SPEC)
sys.modules["w2_measure"] = m
SPEC.loader.exec_module(m)


class Fake(m.Target):
    """A provider stand-in: every call costs `cost`; `fail_at` makes that call (1-based) fail."""

    def __init__(self, cost=0.1, fail_at=0):
        self.cost, self.fail_at, self.n = cost, fail_at, 0

    def _call(self, kind):
        self.n += 1
        cost = self.cost[kind] if isinstance(self.cost, dict) else self.cost
        return m.Call(kind, kind, 5.0, self.n != self.fail_at, cost)

    def upload(self, name, doc_type, doc_id, salt):
        return self._call("upload")

    def chat(self, question):
        return self._call("chat")

    def span_rows(self):
        return []


def test_real_tier_refuses_without_a_budget():
    with pytest.raises(m.ApprovalError):
        m.main(["--tier", "real"], env={})


def test_real_tier_refuses_without_the_matching_token_before_building_a_target(monkeypatch):
    built = []
    monkeypatch.setattr(m, "RealTarget", lambda *a, **k: built.append(1))
    monkeypatch.setattr(m, "real_credentials", lambda: built.append("creds"))
    b = "uploads=10,chats=10,usd=1.50"
    for env in ({}, {"W2_MEASURE_APPROVED": "yes"}, {"W2_MEASURE_APPROVED": "approve-10u-10c-2.00usd"},
                {"W2_MEASURE_APPROVED": "approve-5u-10c-1.50usd"}):
        with pytest.raises(m.ApprovalError):
            m.main(["--tier", "real", "--approved-budget", b, "--traces-db", "x"], env=env)
    assert built == []  # nothing was constructed, so nothing could be sent


@pytest.mark.parametrize("raw", ["uploads=11,chats=1,usd=1", "uploads=1,chats=11,usd=1", "uploads=1,chats=1,usd=5.01",
                                 "uploads=1,chats=1,usd=0", "uploads=0,chats=0,usd=1", "garbage", "uploads=1"])
def test_budgets_over_the_hard_caps_or_malformed_are_refused(raw):
    with pytest.raises(m.ApprovalError):
        m.parse_budget(raw)


def test_token_is_bound_to_the_exact_budget():
    b = m.parse_budget("uploads=10,chats=10,usd=1.50")
    assert b.token == "approve-10u-10c-1.50usd"
    m.require_approval(b, {"W2_MEASURE_APPROVED": b.token})


def test_call_caps_are_enforced():
    g = m.SpendGuard(m.Budget(uploads=3, chats=2, usd=5.0))
    calls = m.run_plan(Fake(cost=0.0), g, uploads=10, chats=10)
    assert [c.kind for c in calls].count("upload") == 3 and [c.kind for c in calls].count("chat") == 0
    # the upload cap stops the whole plan: the guard stays stopped, so no chat is sent afterwards
    assert g.stopped == "upload cap reached"


def test_chats_run_when_uploads_fit_and_stop_at_their_own_cap():
    g = m.SpendGuard(m.Budget(uploads=2, chats=2, usd=5.0))
    calls = m.run_plan(Fake(cost=0.0), g, uploads=2, chats=10)
    assert len(calls) == 4 and g.stopped == "chat cap reached"


def test_spend_cap_stops_before_the_next_call():
    # one upload's reserve is 0.15, so the second does not fit a 0.20 share
    g = m.SpendGuard(m.Budget(uploads=10, chats=0, usd=0.20))
    calls = m.run_plan(Fake(cost=0.15), g, uploads=10, chats=0)
    assert len(calls) == 1 and g.stopped == "upload share exhausted"


def test_first_provider_error_stops_the_run_with_no_retry():
    f = Fake(fail_at=2)
    g = m.SpendGuard(m.Budget(uploads=10, chats=10, usd=5.0))
    calls = m.run_plan(f, g, uploads=10, chats=10)
    assert f.n == 2 and len(calls) == 2 and "stopping at the first provider error" in g.stopped


def test_report_flags_small_samples_and_slo():
    g = m.SpendGuard(m.Budget(10, 10, 5.0))
    calls = m.run_plan(Fake(cost=0.01), g, uploads=4, chats=4)
    r = m.build_report("real", calls, [], g)
    assert r["small_sample_note"] and r["slo"]["ingest_met"] is True and r["per_document_usd"]["usd"] == 0.01


def test_stub_tier_runs_free_and_cost_is_zero(tmp_path):
    out = tmp_path / "o.json"
    assert m.main(["--uploads", "4", "--chats", "4", "--out", str(out)], env={}) == 0
    import json
    r = json.loads(out.read_text())["stub"]
    assert (r["tier"] == "stub" and r["failures"] == 0 and r["agent_recorded_usd"]["usd"] == 0
            and "spend_usd" not in r and r["upload_ms"]["n"] == 4)


def test_reserve_blocks_a_call_the_remaining_budget_cannot_fund():
    g = m.SpendGuard(m.Budget(uploads=10, chats=0, usd=0.20))
    assert g.may("upload")
    g.record("upload", 0.10, ok=True)
    assert not g.may("upload")  # 0.01 settled plus a second 0.15 reserve does not fit
    assert g.stopped == "upload share exhausted"


def test_uploads_cannot_spend_the_chat_share():
    b = m.parse_budget("uploads=10,chats=10,usd=1.50")
    assert b.upload_cap == pytest.approx(0.9) and b.chat_cap == pytest.approx(0.6)
    g = m.SpendGuard(b)
    calls = m.run_plan(Fake(cost={"upload": 0.15, "chat": 0.10}), g, uploads=10, chats=10)
    assert [c.kind for c in calls].count("upload") == 6  # 6 x 0.15 = the $0.90 share
    assert [c.kind for c in calls].count("chat") == 6    # chats still run inside their own $0.60
    assert g.stopped == "chat share exhausted"
    assert "fund 6 uploads and 6 chats" in m.plan_text(b)
    assert "not a billing ceiling" in m.plan_text(b)


def test_a_share_too_small_for_one_call_is_refused():
    with pytest.raises(m.ApprovalError):
        m.parse_budget("uploads=1,chats=1,usd=0.20")  # the chat share is $0.08, under the $0.10 reserve


def test_unknown_cost_charges_the_reserve_and_stops():
    g = m.SpendGuard(m.Budget(uploads=10, chats=0, usd=5.0), strict_cost=True)
    g.record("upload", None, ok=False)
    assert g.by_kind["upload"] == pytest.approx(m.EST_UPLOAD_USD) and g.stopped == "cost unknown"
    assert not g.may("upload")


def test_a_cost_over_its_reserve_stops_the_run():
    g = m.SpendGuard(m.Budget(uploads=10, chats=0, usd=5.0))
    g.record("upload", 0.20, ok=True)
    assert "exceeded its $0.15 reserve" in g.stopped


def test_a_raised_call_is_kept_and_stops_the_run():
    class Boom(m.Target):
        def __init__(self):
            self.n = 0

        def upload(self, name, doc_type, doc_id, salt):
            self.n += 1
            if self.n == 2:
                raise RuntimeError("provider said secret-token=abc")
            return m.Call("upload", doc_type, 1.0, True, 0.01)

        def chat(self, question):
            raise AssertionError("no chat should be sent")

        def span_rows(self):
            return []

    boom = Boom()
    g = m.SpendGuard(m.Budget(uploads=10, chats=10, usd=5.0))
    calls = m.run_plan(boom, g, uploads=10, chats=10)
    assert len(calls) == 2 and calls[1].ok is False and calls[1].cost_usd is None
    assert g.stopped == "cost unknown" and "secret-token" not in calls[1].note
    assert boom.n == 2


def test_report_separates_estimate_recorded_and_verified_spend():
    g = m.SpendGuard(m.Budget(10, 10, 5.0))
    calls = [m.Call("upload", "lab_pdf", 1.0, True, 0.02), m.Call("upload", "lab_pdf", 1.0, False, None)]
    r = m.build_report("real", calls, [], g)
    assert r["reserved_ceiling_usd"] == {"usd": 0.17, "basis": "estimate"}  # 0.02 known + 0.15 reserve
    assert r["agent_recorded_usd"]["basis"] == "agent_trace" and r["agent_recorded_usd"]["usd"] == 0.02
    assert r["provider_verified_usd"] == {"usd": None, "basis": "provider_statement"}
    assert r["unknown_cost_calls"] == 1 and "spend_usd" not in r and "Only provider_verified_usd" in r["spend_notice"]
    verified = m.build_report("real", calls, [], g, provider_verified_usd=0.19)
    assert verified["provider_verified_usd"]["usd"] == 0.19


def test_salt_differs_across_runs():
    seen = []

    class Remember(m.Target):
        def upload(self, name, doc_type, doc_id, salt):
            seen.append(salt)
            return m.Call("upload", doc_type, 1.0, True, 0.01)

        def chat(self, question):
            return m.Call("chat", "chat", 1.0, True, 0.01)

        def span_rows(self):
            return []

    for _ in range(2):
        m.run_plan(Remember(), m.SpendGuard(m.Budget(1, 0, 5.0)), uploads=1, chats=0)
    assert seen[0] != seen[1]


def test_cost_lookup_ignores_other_traffic(tmp_path):
    db = tmp_path / "traces.db"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE traces (correlation_id TEXT, cost_usd REAL, ts REAL, prompt_tokens INT, "
                "completion_tokens INT)")
    con.execute("INSERT INTO traces VALUES ('mine', 0.03, 1, 0, 0)")
    con.execute("INSERT INTO traces VALUES ('someone-else', 9.99, 1, 0, 0)")
    con.commit()
    con.close()
    assert m.cost_for_correlation(str(db), "mine", wait_s=0) == pytest.approx(0.03)
    assert m.cost_for_correlation(str(db), "missing", wait_s=0) is None


def test_a_duplicate_upload_is_rejected(tmp_path):
    db = tmp_path / "traces.db"
    sqlite3.connect(db).execute("CREATE TABLE traces (correlation_id TEXT, cost_usd REAL)").close()
    t = m.RealTarget.__new__(m.RealTarget)
    t.db, t.base, t.patient = str(db), "http://127.0.0.1:8401", "p"
    t._headers = lambda path, body, cid: {}

    class Resp:
        status_code = 200

        def json(self):
            return {"duplicate": True, "document": {"status": "extracted"}}

    class Http:
        def post(self, url, content, headers):
            return Resp()

    t.http = Http()
    c = t.upload("lab_report.pdf", "lab_pdf", 1, "salt")
    assert c.ok is False and c.cost_usd is None and "duplicate" in c.note
