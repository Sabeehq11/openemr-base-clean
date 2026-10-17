"""Trace store: one trace per chat request, one span per step (authz, each tool, each LLM call, verifier).

SQLite, no extra containers. A trace records latency, status, retries, token counts and cost. No prompt or response
text and no clinical values are stored: spans hold only names, timings and counts.
Instrumentation is a no-op when no trace is active, so tools and the FHIR client can call it unconditionally.
"""
from __future__ import annotations

import contextvars
import json
import logging
import secrets
import sqlite3
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Iterator

logger = logging.getLogger("agent.trace")

_current: contextvars.ContextVar["Trace | None"] = contextvars.ContextVar("current_trace", default=None)
# The id of the innermost open span. A span opened while another is open records it as its parent.
_current_span: contextvars.ContextVar[str | None] = contextvars.ContextVar("current_span", default=None)


@dataclass
class Trace:
    correlation_id: str
    started: float = field(default_factory=time.time)
    # (kind, name, ms, status, span_id, parent_span_id, attrs). Older callers may append the first four only.
    spans: list[tuple] = field(default_factory=list)
    retries: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost_usd: float = 0.0
    claims_kept: int = 0
    claims_dropped: int = 0
    outcome: str = "error"
    model: str = ""
    flow: str = ""  # chat | w2_chat | w2_ingest | w2_retrieve ("" for rows written before C4)


def current() -> Trace | None:
    return _current.get()


def start(correlation_id: str, model: str = "", flow: str = "") -> tuple[Trace, contextvars.Token]:
    t = Trace(correlation_id=correlation_id, model=model, flow=flow)
    return t, _current.set(t)


def end(token: contextvars.Token) -> None:
    _current.reset(token)


def count_retry() -> None:
    t = current()
    if t is not None:
        t.retries += 1


def add_usage(prompt: int, completion: int, cost: float) -> None:
    t = current()
    if t is not None:
        t.prompt_tokens += prompt
        t.completion_tokens += completion
        t.cost_usd += cost


def current_span_id() -> str | None:
    return _current_span.get()


@contextmanager
def span(kind: str, name: str) -> Iterator[dict[str, int | float | str]]:
    """Times a block. Status is `ok`, or `error` if the block raised (the exception still propagates).

    Yields a dict the caller may fill with counts (hits, facts, ...). Only numbers and short codes belong in it: it is
    stored and logged, so never text from a document, a prompt or a response.
    """
    t0, status = time.perf_counter(), "ok"
    span_id, parent = secrets.token_hex(8), _current_span.get()
    token = _current_span.set(span_id)
    attrs: dict[str, int | float | str] = {}
    try:
        yield attrs
    except BaseException:
        status = "error"
        raise
    finally:
        try:
            _current_span.reset(token)
        except ValueError:  # closed from another context (a generator torn down late); the id is simply dropped
            pass
        ms = round((time.perf_counter() - t0) * 1000, 2)
        tr = current()
        if tr is not None:
            tr.spans.append((kind, name, ms, status, span_id, parent, dict(attrs)))
        # One structured line per step. The correlation ID is added by the log formatter. Names, timings, status and
        # counts only: never prompts, responses or chart values.
        logger.info("span", extra={"fields": {"span_kind": kind, "span_name": name, "ms": ms, "status": status,
                                              "span_id": span_id, "parent_span_id": parent, **attrs}})


class TraceStore:
    def __init__(self, path: str) -> None:
        self._lock = threading.Lock()
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._db.executescript(
            "CREATE TABLE IF NOT EXISTS traces (id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL, "
            "correlation_id TEXT NOT NULL, outcome TEXT NOT NULL, latency_ms REAL NOT NULL, retries INTEGER NOT NULL, "
            "prompt_tokens INTEGER NOT NULL, completion_tokens INTEGER NOT NULL, cost_usd REAL NOT NULL, "
            "claims_kept INTEGER NOT NULL, claims_dropped INTEGER NOT NULL, model TEXT NOT NULL);"
            "CREATE TABLE IF NOT EXISTS spans (id INTEGER PRIMARY KEY AUTOINCREMENT, trace_id INTEGER NOT NULL, "
            "ts REAL NOT NULL, kind TEXT NOT NULL, name TEXT NOT NULL, latency_ms REAL NOT NULL, status TEXT NOT NULL);"
            "CREATE INDEX IF NOT EXISTS idx_traces_ts ON traces(ts); CREATE INDEX IF NOT EXISTS idx_spans_ts ON spans(ts);")
        self._migrate()
        self._db.commit()

    def _migrate(self) -> None:
        """C4 additive columns. Nullable, so old rows stay valid and Week 1 queries are unaffected. Safe to repeat."""
        for table, column, ddl in (("traces", "flow", "TEXT"), ("spans", "span_id", "TEXT"),
                                   ("spans", "parent_span_id", "TEXT"), ("spans", "attrs", "TEXT")):
            have = {r[1] for r in self._db.execute(f"PRAGMA table_info({table})")}
            if column not in have:
                self._db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")

    def save(self, t: Trace) -> None:
        now = time.time()
        with self._lock:
            cur = self._db.execute(
                "INSERT INTO traces (ts, correlation_id, outcome, latency_ms, retries, prompt_tokens, completion_tokens, "
                "cost_usd, claims_kept, claims_dropped, model, flow) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (now, t.correlation_id, t.outcome, round((now - t.started) * 1000, 2), t.retries, t.prompt_tokens,
                 t.completion_tokens, t.cost_usd, t.claims_kept, t.claims_dropped, t.model, t.flow or None))
            tid = cur.lastrowid
            rows = []
            for sp in t.spans:
                k, n, ms, st, sid, parent, attrs = (tuple(sp) + (None, None, None))[:7]
                rows.append((tid, now, k, n, ms, st, sid, parent, json.dumps(attrs) if attrs else None))
            self._db.executemany("INSERT INTO spans (trace_id, ts, kind, name, latency_ms, status, span_id, "
                                 "parent_span_id, attrs) VALUES (?,?,?,?,?,?,?,?,?)", rows)
            self._db.commit()

    def query(self, sql: str, params: tuple = ()) -> list[tuple]:
        with self._lock:
            return self._db.execute(sql, params).fetchall()
