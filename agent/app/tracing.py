"""Trace store: one trace per chat request, one span per step (authz, each tool, each LLM call, verifier).

SQLite, no extra containers. A trace records latency, status, retries, token counts and cost. No prompt or response
text and no clinical values are stored: spans hold only names, timings and counts.
Instrumentation is a no-op when no trace is active, so tools and the FHIR client can call it unconditionally.
"""
from __future__ import annotations

import contextvars
import sqlite3
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Iterator

_current: contextvars.ContextVar["Trace | None"] = contextvars.ContextVar("current_trace", default=None)


@dataclass
class Trace:
    correlation_id: str
    started: float = field(default_factory=time.time)
    spans: list[tuple[str, str, float, str]] = field(default_factory=list)  # (kind, name, ms, status)
    retries: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost_usd: float = 0.0
    claims_kept: int = 0
    claims_dropped: int = 0
    outcome: str = "error"
    model: str = ""


def current() -> Trace | None:
    return _current.get()


def start(correlation_id: str, model: str = "") -> tuple[Trace, contextvars.Token]:
    t = Trace(correlation_id=correlation_id, model=model)
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


@contextmanager
def span(kind: str, name: str) -> Iterator[None]:
    """Times a block. Status is `ok`, or `error` if the block raised (the exception still propagates)."""
    t0, status = time.perf_counter(), "ok"
    try:
        yield
    except BaseException:
        status = "error"
        raise
    finally:
        tr = current()
        if tr is not None:
            tr.spans.append((kind, name, round((time.perf_counter() - t0) * 1000, 2), status))


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
        self._db.commit()

    def save(self, t: Trace) -> None:
        now = time.time()
        with self._lock:
            cur = self._db.execute(
                "INSERT INTO traces (ts, correlation_id, outcome, latency_ms, retries, prompt_tokens, completion_tokens, "
                "cost_usd, claims_kept, claims_dropped, model) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (now, t.correlation_id, t.outcome, round((now - t.started) * 1000, 2), t.retries, t.prompt_tokens,
                 t.completion_tokens, t.cost_usd, t.claims_kept, t.claims_dropped, t.model))
            tid = cur.lastrowid
            self._db.executemany("INSERT INTO spans (trace_id, ts, kind, name, latency_ms, status) VALUES (?,?,?,?,?,?)",
                                 [(tid, now, k, n, ms, s) for k, n, ms, s in t.spans])
            self._db.commit()

    def query(self, sql: str, params: tuple = ()) -> list[tuple]:
        with self._lock:
            return self._db.execute(sql, params).fetchall()
