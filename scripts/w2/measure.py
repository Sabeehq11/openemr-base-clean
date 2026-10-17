#!/usr/bin/env python3
"""Week 2 measurement harness: ingestion, retrieval and chat latency, tokens and cost.

Tiers (a paid run can never start by accident):

  --tier stub   (default, free) In-process app, canned VLM, offline embedder and reranker, stub chat model, mocked
                FHIR. No network, no OpenEMR, no provider key. Numbers are labelled `stub`: they show plumbing
                overhead and prove the harness, never model latency.
  --tier real   (paid) Signed HTTP calls to a SEPARATE agent started with the real VLM and model. Refuses to start
                unless BOTH are present:
                  --approved-budget uploads=N,chats=M,usd=X     (N, M at most 10; X at most 5.00, a ceiling, not
                                                                 the plan: the proposed budget is 1.50)
                  W2_MEASURE_APPROVED=approve-<N>u-<M>c-<X>usd  (e.g. approve-10u-10c-1.50usd)
                The token is bound to the exact numbers, so approving one budget does not approve another. It is
                supplied by whoever owns the budget, in a separate step. The planned call counts, the per-kind shares of
                the budget and the reserve per call are printed before anything is sent. The usd figure is an internal
                estimated budget, reserved before each call. It is not a billing ceiling: actual provider charges may
                exceed it, because reserves are estimates, a failed or timed-out call can still be billed, and embedding
                and rerank spend is never written to traces.db. No provider-side spending limit is assumed. The run
                stops at the first provider error, at the call caps, when a kind's share cannot fund its next reserve,
                and when a call's cost is unknown. Nothing is retried here.
  --compare-shared  Week 1 /v1/chat with W2_ENABLED off and on, same mocks (free, in-process).

Examples:
  agent/.venv/bin/python scripts/w2/measure.py --uploads 30 --chats 30 --out /tmp/stub.json
  agent/.venv/bin/python scripts/w2/measure.py --compare-shared --requests 40
  W2_MEASURE_APPROVED=... agent/.venv/bin/python scripts/w2/measure.py --tier real \
      --approved-budget uploads=10,chats=10,usd=1.50 --base http://127.0.0.1:8401 --traces-db <load agent traces.db>
"""
from __future__ import annotations

import argparse
import base64
import json
import logging
import math
import os
import sqlite3
import statistics
import sys
import tempfile
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[2]
AGENT = ROOT / "agent"
FIXTURES = AGENT / "tests" / "fixtures" / "w2"

HARD_CAP_UPLOADS = 10
HARD_CAP_CHATS = 10
HARD_CAP_USD = 5.00
# Per-call reserves. An internal estimate of one call's worst case, used to decide whether the next call fits the
# approved budget. Not a price and not a billing ceiling: the provider may charge more than the reserve.
EST_UPLOAD_USD = 0.15
EST_CHAT_USD = 0.10
RESERVE = {"upload": EST_UPLOAD_USD, "chat": EST_CHAT_USD}
# How long to wait for the agent's trace row after a call returns. The chat trace is saved after the stream ends.
TRACE_WAIT_S = 2.0
TRACE_POLL_S = 0.2
SPEND_NOTICE = ("Only provider_verified_usd is a charge. The others are estimates. The approved budget is an internal "
                "estimate enforced by this script, not a billing ceiling: actual provider charges may exceed it. "
                "No provider-side spending limit is assumed. Embedding and rerank spend is not in traces.db.")
LAB_FILES = ["lab_report.pdf"]
INTAKE_FILES = ["intake_form.pdf"]
CHAT_QUESTIONS = [
    "What changed since the last visit, and what do the guidelines say about A1c frequency?",
    "Any abnormal labs from the uploaded report, and is blood pressure at goal per guidelines?",
]


class ApprovalError(SystemExit):
    """Raised before any call is made when the real tier is not fully approved."""


@dataclass(frozen=True)
class Budget:
    uploads: int
    chats: int
    usd: float

    @property
    def token(self) -> str:
        return f"approve-{self.uploads}u-{self.chats}c-{self.usd:.2f}usd"

    @property
    def worst_case_usd(self) -> float:
        return round(self.uploads * EST_UPLOAD_USD + self.chats * EST_CHAT_USD, 2)

    @property
    def upload_cap(self) -> float:
        """This kind's share of the approved budget, proportional to its worst-case need. Unused share does not roll
        over, so uploads can never spend the chat share."""
        if self.worst_case_usd == 0:
            return 0.0
        return round(self.usd * (self.uploads * EST_UPLOAD_USD) / self.worst_case_usd, 6)

    @property
    def chat_cap(self) -> float:
        return round(self.usd - self.upload_cap, 6)

    def cap_for(self, kind: str) -> float:
        return self.upload_cap if kind == "upload" else self.chat_cap


def parse_budget(raw: str | None) -> Budget:
    """`uploads=10,chats=10,usd=1.50`. Anything missing, malformed or over a hard cap is refused."""
    if not raw:
        raise ApprovalError("--tier real needs --approved-budget uploads=N,chats=M,usd=X")
    try:
        kv = dict(part.split("=", 1) for part in raw.split(","))
        b = Budget(int(kv["uploads"]), int(kv["chats"]), float(kv["usd"]))
    except (ValueError, KeyError):
        raise ApprovalError("--approved-budget must look like uploads=10,chats=10,usd=1.50") from None
    if not (0 <= b.uploads <= HARD_CAP_UPLOADS and 0 <= b.chats <= HARD_CAP_CHATS and 0 < b.usd <= HARD_CAP_USD):
        raise ApprovalError(f"over a hard cap: uploads<={HARD_CAP_UPLOADS}, chats<={HARD_CAP_CHATS}, usd<={HARD_CAP_USD}")
    if b.uploads + b.chats == 0:
        raise ApprovalError("budget allows no calls")
    for kind, n in (("upload", b.uploads), ("chat", b.chats)):
        if n and b.cap_for(kind) < RESERVE[kind]:
            raise ApprovalError(f"the {kind} share of this budget cannot fund one call "
                                f"(share ${b.cap_for(kind):.2f}, reserve ${RESERVE[kind]:.2f})")
    return b


def require_approval(budget: Budget, env: dict[str, str]) -> None:
    got = env.get("W2_MEASURE_APPROVED", "")
    if got != budget.token:
        raise ApprovalError("real tier refused: W2_MEASURE_APPROVED is missing or does not match this exact budget "
                            "(format approve-<N>u-<M>c-<X>usd). No call was made.")


class SpendGuard:
    """Counts calls and spend; says stop before the next call would break a cap. One guard per run.

    `usd` is an internal estimate used to gate the next call, not a record of what the provider charged. A call is
    allowed only when its reserve fits both its own kind's share and the approved total, so one kind cannot spend the
    other's share. An unknown cost is charged at the reserve and stops the run.
    """

    def __init__(self, budget: Budget, strict_cost: bool = False) -> None:
        self.b, self.strict = budget, strict_cost
        self.uploads, self.chats, self.usd, self.stopped = 0, 0, 0.0, ""
        self.by_kind = {"upload": 0.0, "chat": 0.0}

    def may(self, kind: str) -> bool:
        if self.stopped:
            return False
        reserve = RESERVE[kind]
        if self.by_kind[kind] + reserve > self.b.cap_for(kind) + 1e-9:
            # This kind is done. The other kind keeps its own share, so this is not a run-wide stop unless nothing
            # else remains to try (run_plan clears it when moving on to chats).
            self.stopped = f"{kind} share exhausted"
        elif self.usd + reserve > self.b.usd + 1e-9:
            self.stopped = "spend cap would be exceeded"
        elif kind == "upload" and self.uploads >= self.b.uploads:
            self.stopped = "upload cap reached"
        elif kind == "chat" and self.chats >= self.b.chats:
            self.stopped = "chat cap reached"
        return not self.stopped

    def record(self, kind: str, cost_usd: float | None, ok: bool, note: str = "") -> None:
        if kind == "upload":
            self.uploads += 1
        else:
            self.chats += 1
        unknown = cost_usd is None or (self.strict and ok and cost_usd <= 0.0)
        charged = RESERVE[kind] if unknown else max(cost_usd or 0.0, 0.0)
        self.by_kind[kind] = round(self.by_kind[kind] + charged, 6)
        self.usd = round(self.usd + charged, 6)
        if self.stopped:
            return
        if unknown:
            self.stopped = "cost unknown"
        elif not self.strict and charged > RESERVE[kind]:
            self.stopped = f"{kind} cost ${charged:.4f} exceeded its ${RESERVE[kind]:.2f} reserve"
        elif not ok:
            self.stopped = note or f"{kind} failed; stopping at the first provider error"


def pct(values: list[float], p: float) -> float | None:
    if not values:
        return None
    s = sorted(values)
    return round(s[min(len(s) - 1, max(0, math.ceil(p / 100 * len(s)) - 1))], 2)


def dist(values: list[float]) -> dict:
    return {"n": len(values), "p50": pct(values, 50), "p95": pct(values, 95),
            "max": round(max(values), 2) if values else None,
            "mean": round(statistics.fmean(values), 2) if values else None}


# --------------------------------------------------------------------------------------------- targets

@dataclass
class Call:
    kind: str          # upload | chat
    label: str         # lab_pdf | intake_form | chat
    ms: float
    ok: bool
    cost_usd: float | None = 0.0  # None: the cost is unknown, so the guard charges the reserve and stops
    correlation_id: str = ""
    note: str = ""


class Target:
    """Something the plan can call. The stub target is in-process; the real target is signed HTTP."""
    label = "stub"

    def upload(self, name: str, doc_type: str, doc_id: int, salt: int) -> Call:
        raise NotImplementedError

    def chat(self, question: str) -> Call:
        raise NotImplementedError

    def span_rows(self) -> list[tuple[str, str, float, str]]:
        raise NotImplementedError


def run_plan(target: Target, guard: SpendGuard, uploads: int, chats: int, run_id: str = "") -> list[Call]:
    """Alternating lab and intake uploads, then chats. Every call asks the guard first; nothing is retried.

    A raised call is kept as a failed call with an unknown cost, and the run stops. The calls collected so far are
    always returned, so a report can still be written.
    """
    calls: list[Call] = []
    salt = run_id or uuid.uuid4().hex  # unique per run, so a rerun is never a cached duplicate
    for i in range(uploads):
        if not guard.may("upload"):
            break
        name, kind = (LAB_FILES[0], "lab_pdf") if i % 2 == 0 else (INTAKE_FILES[0], "intake_form")
        try:
            c = target.upload(name, kind, 1000 + i, f"{salt}-{i}")
        except Exception as e:  # the type only: never a message, which can carry a URL or a body
            c = Call("upload", kind, 0.0, False, None, note=note_from(e))
        guard.record("upload", c.cost_usd, c.ok, c.note)
        calls.append(c)
        if guard.stopped:
            break
    if guard.stopped == "upload share exhausted" and chats:
        guard.stopped = ""  # uploads used their share; chats still have theirs, and it does not roll over
    for i in range(chats):
        if not guard.may("chat"):
            break
        try:
            c = target.chat(CHAT_QUESTIONS[i % len(CHAT_QUESTIONS)])
        except Exception as e:
            c = Call("chat", "chat", 0.0, False, None, note=note_from(e))
        guard.record("chat", c.cost_usd, c.ok, c.note)
        calls.append(c)
        if guard.stopped:
            break
    return calls


def note_from(e: BaseException) -> str:
    return f"{type(e).__name__}; stopping at the first provider error"


def _salted(raw: bytes, salt: str) -> bytes:
    return raw + f"\n%% measure {salt}\n".encode()  # trailing bytes: a new sha256, so each upload does the work


def trace_span_rows(query) -> list[tuple[str, str, float, str]]:
    return [(f or "", n, ms, st) for f, n, ms, st in query(
        "SELECT t.flow, s.name, s.latency_ms, s.status FROM spans s JOIN traces t ON t.id = s.trace_id")]


class StubTarget(Target):
    """The real routers and supervisor, in process, with the canned VLM, offline retriever, stub chat model and mocked
    FHIR. Measures plumbing only."""
    label = "stub"

    def __init__(self, tmp: Path) -> None:
        sys.path[:0] = [str(AGENT)]
        from app.tracing import TraceStore
        from tests import w2_chat_helpers as h
        self.h = h
        self.traces = TraceStore(str(tmp / "traces.db"))
        self.client, _ = h.build(tmp / "app", h.TwoStageLLM(h.stage_b(("Hemoglobin A1c is 7.9 %.", ["doc:7#0"]))),
                                 upload=False, traces=self.traces)

    def upload(self, name: str, doc_type: str, doc_id: int, salt: int) -> Call:
        data = _salted((FIXTURES / name).read_bytes(), salt)
        t0 = time.perf_counter()
        r = self.h.post(self.client, "/v1/w2/documents", {
            "patient_uuid": self.h.PATIENT, "document_id": doc_id, "doc_type": doc_type,
            "content_b64": base64.b64encode(data).decode()})
        ms = (time.perf_counter() - t0) * 1000
        return Call("upload", doc_type, ms, r.status_code == 200 and r.json()["document"]["status"] == "extracted")

    def chat(self, question: str) -> Call:
        t0 = time.perf_counter()
        r = self.h.ask(self.client, question)
        return Call("chat", "chat", (time.perf_counter() - t0) * 1000, r.status_code == 200 and "event: answer" in r.text)

    def span_rows(self) -> list[tuple[str, str, float, str]]:
        return trace_span_rows(self.traces.query)


def trace_cost(db_path: str, since: float) -> tuple[float, int, int]:
    """Spend and tokens the target itself recorded since `since`. Read-only."""
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        row = con.execute("SELECT COALESCE(SUM(cost_usd),0), COALESCE(SUM(prompt_tokens),0), "
                          "COALESCE(SUM(completion_tokens),0) FROM traces WHERE ts >= ?", (since,)).fetchone()
    finally:
        con.close()
    return float(row[0]), int(row[1]), int(row[2])


def trace_row_count(db_path: str) -> int:
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        return int(con.execute("SELECT COUNT(*) FROM traces").fetchone()[0])
    finally:
        con.close()


def cost_for_correlation(db_path: str, correlation_id: str, wait_s: float = TRACE_WAIT_S) -> float | None:
    """The cost the agent recorded for this one request, or None if no row appeared in time.

    Polls, because the chat trace is written after the stream ends. Other traffic in the same database is ignored.
    """
    deadline = time.monotonic() + wait_s
    while True:
        con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        try:
            row = con.execute("SELECT cost_usd FROM traces WHERE correlation_id = ?", (correlation_id,)).fetchone()
        finally:
            con.close()
        if row is not None:
            return float(row[0])
        if time.monotonic() >= deadline:
            return None
        time.sleep(TRACE_POLL_S)


class RealTarget(Target):
    """Signed HTTP calls to a separate, real-model agent. Only constructed after approval has been checked."""
    label = "real"

    def __init__(self, base: str, traces_db: str, secret: str, user_uuid: str, token: str, patient: str) -> None:
        import httpx
        sys.path[:0] = [str(AGENT)]
        self.http = httpx.Client(timeout=90.0)
        self.base, self.db, self.secret = base, traces_db, secret
        self.user, self.token, self.patient = user_uuid, token, patient
        self.started = time.time()

    def _headers(self, path: str, body: bytes, cid: str) -> dict[str, str]:
        from app import signing
        ts, nonce = str(int(time.time())), str(uuid.uuid4())
        sig = signing.sign(self.secret, "POST", path, ts, nonce, body, self.user, self.token)
        return {"x-copilot-timestamp": ts, "x-copilot-nonce": nonce, "x-copilot-user": self.user,
                "x-copilot-token": self.token, "x-correlation-id": cid,
                "content-type": "application/json", "x-copilot-signature": sig}

    def _cost(self, cid: str, ok: bool) -> float | None:
        """This request's recorded cost. None when the row never appeared, or an ok call recorded nothing: a real
        model call that shows $0 was not recorded, and an unrecorded call must not count as free."""
        cost = cost_for_correlation(self.db, cid)
        if cost is None or (ok and cost <= 0.0):
            return None
        return cost

    def upload(self, name: str, doc_type: str, doc_id: int, salt: str) -> Call:
        path = "/v1/w2/documents"
        raw = _salted((FIXTURES / name).read_bytes(), salt)
        body = json.dumps({"patient_uuid": self.patient, "document_id": doc_id, "doc_type": doc_type,
                           "content_b64": base64.b64encode(raw).decode()}).encode()
        cid, t0 = str(uuid.uuid4()), time.perf_counter()
        r = self.http.post(f"{self.base}{path}", content=body, headers=self._headers(path, body, cid))
        ms = (time.perf_counter() - t0) * 1000
        body_json = r.json() if r.status_code == 200 else {}
        duplicate = bool(body_json.get("duplicate"))
        extracted = body_json.get("document", {}).get("status") == "extracted"
        ok = r.status_code == 200 and extracted and not duplicate
        note = "duplicate response; this run's upload was served from cache" if duplicate else ""
        return Call("upload", doc_type, ms, ok, self._cost(cid, ok), cid, note)

    def chat(self, question: str) -> Call:
        path = "/v1/w2/chat"
        body = json.dumps({"patient_uuid": self.patient, "question": question}).encode()
        cid, t0, terminal = str(uuid.uuid4()), time.perf_counter(), ""
        with self.http.stream("POST", f"{self.base}{path}", content=body, headers=self._headers(path, body, cid)) as r:
            if r.status_code == 200:
                for line in r.iter_lines():
                    if line.startswith("event: "):
                        terminal = line[7:]
            ok = r.status_code == 200 and terminal == "answer"
        ms = (time.perf_counter() - t0) * 1000
        return Call("chat", "chat", ms, ok, self._cost(cid, ok), cid)

    def span_rows(self) -> list[tuple[str, str, float, str]]:
        con = sqlite3.connect(f"file:{self.db}?mode=ro", uri=True)
        try:
            return trace_span_rows(lambda q: con.execute(q + " WHERE t.ts >= ?", (self.started,)).fetchall())
        finally:
            con.close()


# --------------------------------------------------------------------------------------------- report

SLO_INGEST_MS, SLO_RETRIEVE_MS = 30_000.0, 3_000.0
SMALL_SAMPLE = 20  # below this, p95 is the near-maximum and the report says so


def build_report(tier: str, calls: list[Call], spans: list[tuple[str, str, float, str]], guard: SpendGuard,
                 tokens: tuple[int, int] = (0, 0), tokens_ok: bool = True,
                 agent_recorded_usd: float | None = None, foreign_spend_usd: float | None = None,
                 provider_verified_usd: float | None = None) -> dict:
    ups = [c.ms for c in calls if c.kind == "upload"]
    chats = [c.ms for c in calls if c.kind == "chat"]
    by_name: dict[str, list[float]] = {}
    for _flow, name, ms, _st in spans:
        by_name.setdefault(name, []).append(ms)
    retr = by_name.get("evidence_retriever", []) + [ms for f, n, ms, _ in spans if f == "w2_retrieve"]
    ing_p95, ret_p95 = pct(ups, 95), pct(retr, 95)
    known = [c for c in calls if c.cost_usd is not None]
    unknown = [c for c in calls if c.cost_usd is None]
    reserved = round(sum(c.cost_usd for c in known) + sum(RESERVE[c.kind] for c in unknown), 4)
    recorded = round(sum(c.cost_usd for c in known), 4) if agent_recorded_usd is None else round(agent_recorded_usd, 4)
    per = lambda kind: [c.cost_usd for c in known if c.kind == kind]
    avg = lambda xs: round(sum(xs) / len(xs), 4) if xs else None
    return {
        "tier": tier, "calls": len(calls), "failures": sum(1 for c in calls if not c.ok),
        "stopped": guard.stopped or None, "unknown_cost_calls": len(unknown),
        "reserved_ceiling_usd": {"usd": reserved, "basis": "estimate"},
        "agent_recorded_usd": {"usd": recorded, "basis": "agent_trace"},
        "provider_verified_usd": {"usd": provider_verified_usd, "basis": "provider_statement"},
        "spend_notice": SPEND_NOTICE, "cost_reconciled": foreign_spend_usd == 0.0,
        "foreign_spend_usd": foreign_spend_usd, "tokens_ok": tokens_ok,
        "tokens": {"prompt": tokens[0], "completion": tokens[1]},
        "upload_ms": dist(ups), "chat_ms": dist(chats), "retrieval_ms": dist(retr),
        "upload_by_type_ms": {t: dist([c.ms for c in calls if c.kind == "upload" and c.label == t])
                              for t in sorted({c.label for c in calls if c.kind == "upload"})},
        "steps_ms": {n: dist(v) for n, v in sorted(by_name.items())},
        "slo": {"ingest_p95_target_ms": SLO_INGEST_MS, "ingest_p95_ms": ing_p95,
                "ingest_met": None if ing_p95 is None else ing_p95 <= SLO_INGEST_MS,
                "retrieve_p95_target_ms": SLO_RETRIEVE_MS, "retrieve_p95_ms": ret_p95,
                "retrieve_met": None if ret_p95 is None else ret_p95 <= SLO_RETRIEVE_MS},
        "per_document_usd": {"usd": avg(per("upload")), "basis": "agent_trace"},
        "per_chat_usd": {"usd": avg(per("chat")), "basis": "agent_trace"},
        "small_sample_note": ("p95 is the near-maximum with fewer than %d samples" % SMALL_SAMPLE)
        if min(len(ups) or SMALL_SAMPLE, len(chats) or SMALL_SAMPLE) < SMALL_SAMPLE else None,
    }


# --------------------------------------------------------------------------------------------- shared path

def compare_shared(requests: int, tmp: Path) -> dict:
    """Week 1 /v1/chat through the real app with W2_ENABLED off and on. Same mocks, same order, interleaved so drift
    hits both sides equally."""
    sys.path[:0] = [str(AGENT)]
    from fastapi.testclient import TestClient
    from pydantic import SecretStr

    import httpx
    from app import signing
    from app.authz import AuthzDecision, Reason
    from app.config import Settings
    from app.main import create_app
    from tests.test_chat_observability_unit import PID, USER, fhir

    class Allow:
        async def authorize(self, token, patient_uuid, claimed_user_uuid=None):
            return AuthzDecision(True, Reason.ALLOWED, USER, patient_uuid)

    def make(flag: bool) -> TestClient:
        s = Settings(data_dir=str(tmp / f"shared-{int(flag)}"), audit_hmac_key=SecretStr("k" * 32),
                     module_signing_secret=SecretStr("s"), llm_provider="stub", dashboard_token=SecretStr("d"),
                     w2_enabled=flag, w2_use_stub_vlm=True, rate_limit_per_min=0)
        app = create_app(s)
        app.state.gateway._clock = lambda: 1_800_000_000
        app.state.runner._authz = Allow()
        app.state.runner._fhir._transport = httpx.MockTransport(fhir)
        return TestClient(app)

    clients, lat, n = {False: make(False), True: make(True)}, {False: [], True: []}, [0]

    def once(c: TestClient) -> tuple[float, bool]:
        n[0] += 1
        body = json.dumps({"patient_uuid": PID, "question": "brief", "conversation_id": None}).encode()
        nonce = f"measure-nonce-{n[0]:012d}"
        sig = signing.sign("s", "POST", "/v1/chat", "1800000000", nonce, body, USER, "tok")
        h = {"x-copilot-timestamp": "1800000000", "x-copilot-nonce": nonce, "x-copilot-signature": sig,
             "x-copilot-user": USER, "x-copilot-token": "tok", "content-type": "application/json"}
        t0 = time.perf_counter()
        r = c.post("/v1/chat", content=body, headers=h)
        return (time.perf_counter() - t0) * 1000, r.status_code == 200 and "event: answer" in r.text

    for flag in (False, True):  # warm-up, not counted
        once(clients[flag])
    bad = 0
    for _ in range(requests):
        for flag in (False, True):
            ms, ok = once(clients[flag])
            lat[flag].append(ms)
            bad += 0 if ok else 1
    off, on = dist(lat[False]), dist(lat[True])
    delta = None if not off["p50"] else round((on["p50"] - off["p50"]) / off["p50"] * 100, 1)
    return {"label": "stub", "requests_per_side": requests, "failures": bad, "w2_off_ms": off, "w2_on_ms": on,
            "p50_delta_pct": delta}


# --------------------------------------------------------------------------------------------- cli

def plan_text(b: Budget) -> str:
    def fits(n: int, cap: float, reserve: float) -> int:
        return min(n, int((cap + 1e-9) / reserve)) if reserve else 0
    return (f"PLANNED REAL RUN: up to {b.uploads} uploads + {b.chats} chats (call-count limits, not promises). "
            f"Internal estimated budget ${b.usd:.2f}, split ${b.upload_cap:.2f} uploads / ${b.chat_cap:.2f} chats; "
            f"reserve ${EST_UPLOAD_USD:.2f} per upload and ${EST_CHAT_USD:.2f} per chat, so the shares fund "
            f"{fits(b.uploads, b.upload_cap, EST_UPLOAD_USD)} uploads and {fits(b.chats, b.chat_cap, EST_CHAT_USD)} "
            f"chats. Worst-case estimate ${b.worst_case_usd:.2f}. This is an internal estimate enforced by this "
            f"script, not a billing ceiling: actual provider charges may exceed it. No provider-side spending limit "
            f"is assumed. First provider error, unknown cost or a duplicate upload stops the run. Nothing is retried.")


def parse(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--tier", choices=["stub", "real"], default="stub")
    p.add_argument("--uploads", type=int, default=30, help="stub tier only; the real tier uses --approved-budget")
    p.add_argument("--chats", type=int, default=30, help="stub tier only")
    p.add_argument("--approved-budget", default=None, help="real tier: uploads=N,chats=M,usd=X")
    p.add_argument("--base", default="http://127.0.0.1:8401", help="real tier: a separate agent, not the live one")
    p.add_argument("--traces-db", default="", help="real tier: that agent's traces.db (read-only)")
    p.add_argument("--provider-spend-usd", type=float, default=None,
                   help="real tier: spend the operator read from the provider after the run; never fetched here")
    p.add_argument("--compare-shared", action="store_true")
    p.add_argument("--requests", type=int, default=40, help="per side for --compare-shared")
    p.add_argument("--out", default="")
    return p.parse_args(argv)


def real_credentials() -> tuple[str, str, str, str]:
    """Signing secret from agent/.env, a real OAuth token for a synthetic user, and one of that user's patients."""
    sys.path[:0] = [str(AGENT), str(ROOT)]
    from tests.oauth_helper import get_token, load_env, load_fixture
    env, fx = load_env(), load_fixture()
    user = "copilot_doc_a"
    mine = [p for p, o in fx["patient_owner"].items() if o == fx["user_ids"][user]]
    scopes = ("openid fhirUser user/Patient.rs user/CareTeam.rs user/Condition.rs user/MedicationRequest.rs "
              "user/AllergyIntolerance.rs user/Observation.rs user/Encounter.rs")
    return (env["MODULE_SIGNING_SECRET"], fx["user_uuids"][user], get_token(user, scopes)["access_token"],
            fx["patient_uuids"][mine[0]])


def main(argv: list[str], env: dict[str, str] | None = None) -> int:
    logging.disable(logging.INFO)  # the app's per-step JSON lines would bury the report
    try:
        return _main(argv, env)
    finally:
        logging.disable(logging.NOTSET)  # never leak into a caller (tests run this in-process)


def require_isolated_target(base: str, traces_db: str) -> None:
    """A real run may only talk to a fresh agent on this machine, so its traces are its own."""
    if not traces_db:
        raise ApprovalError("--traces-db is required so spend is read from the target, not guessed")
    host = (urlparse(base).hostname or "").lower()
    if host not in ("127.0.0.1", "localhost", "::1"):
        raise ApprovalError(f"--base must be a loopback address, got {host or base!r}")
    try:
        rows = trace_row_count(traces_db)
    except sqlite3.Error as e:
        raise ApprovalError(f"--traces-db is not a readable trace database ({type(e).__name__})") from None
    if rows:
        raise ApprovalError(f"--traces-db already has {rows} rows; a real run needs a fresh dedicated agent")


def real_report(a: argparse.Namespace, target: Target, calls: list[Call], guard: SpendGuard) -> dict:
    db, since = a.traces_db, getattr(target, "started", 0.0)
    tokens, tokens_ok, recorded, foreign = (0, 0), False, None, None
    try:
        total, pt, ct = trace_cost(db, since)
        tokens, tokens_ok = (pt, ct), True
        known = round(sum(c.cost_usd for c in calls if c.cost_usd is not None), 6)
        recorded, foreign = known, round(total - known, 6)
    except sqlite3.Error:
        pass  # the report still goes out, flagged tokens_ok False
    spans: list[tuple[str, str, float, str]] = []
    try:
        spans = target.span_rows()
    except sqlite3.Error:
        pass
    return build_report("real", calls, spans, guard, tokens, tokens_ok, recorded, foreign, a.provider_spend_usd)


def write_out(path: str, out: dict) -> None:
    if path:
        Path(path).write_text(json.dumps(out, indent=2) + "\n")


def summary_text(r: dict) -> str:
    return (f"reserved_ceiling_usd (estimate): ${r['reserved_ceiling_usd']['usd']:.4f}; "
            f"agent_recorded_usd (agent_trace): ${r['agent_recorded_usd']['usd']:.4f}; "
            f"provider_verified_usd (provider_statement): {r['provider_verified_usd']['usd']}. "
            f"The approved budget is an internal estimate, not a billing ceiling and not proof of what was charged.")


def _main(argv: list[str], env: dict[str, str] | None) -> int:
    a = parse(argv)
    env = os.environ if env is None else env
    out: dict = {}
    with tempfile.TemporaryDirectory(prefix="w2-measure-") as td:
        tmp = Path(td)
        if a.compare_shared:
            out["shared_path"] = compare_shared(a.requests, tmp)
        if a.tier == "real":
            budget = parse_budget(a.approved_budget)
            print(plan_text(budget))
            require_approval(budget, dict(env))  # before any target exists, so nothing can be sent
            require_isolated_target(a.base, a.traces_db)
            secret, user, token, patient = real_credentials()
            target = RealTarget(a.base, a.traces_db, secret, user, token, patient)
            guard = SpendGuard(budget, strict_cost=True)
            calls: list[Call] = []
            try:
                calls = run_plan(target, guard, budget.uploads, budget.chats)
            finally:  # a crash mid-run must still leave the calls collected so far, on disk
                out["real"] = real_report(a, target, calls, guard)
                print(summary_text(out["real"]))
                write_out(a.out, out)
        elif not a.compare_shared:
            guard = SpendGuard(Budget(a.uploads, a.chats, HARD_CAP_USD))  # stub calls cost nothing; $0 is legitimate
            target = StubTarget(tmp)
            calls = run_plan(target, guard, a.uploads, a.chats)
            out["stub"] = build_report("stub", calls, target.span_rows(), guard)
    text = json.dumps(out, indent=2)
    print(text)
    if a.out:
        Path(a.out).write_text(text + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
