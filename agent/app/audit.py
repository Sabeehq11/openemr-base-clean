"""Append-only, HMAC-chained audit log (HIPAA 164.312(b) style record that OpenEMR does not keep for reads).

Each row stores hash = HMAC(key, prev_hash || canonical_json(record)). Editing, deleting or reordering any row breaks
every hash after it, and `verify()` finds the first bad row. The key lives only in agent/.env.
Only identifiers are stored (user uuid, patient uuid, tool, outcome, counts), never clinical values or tokens.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import sqlite3
import threading
import time
from dataclasses import dataclass

from .logging_setup import correlation_id_var, redact

GENESIS = "0" * 64
# Fields that may be stored. Anything else is dropped so PHI or secrets cannot leak into the audit table by accident.
ALLOWED_FIELDS = {
    "tool", "patient_uuid", "user_uuid", "outcome", "records", "correlation_id", "why", "reason", "model",
    "fields_returned",
}


@dataclass(frozen=True)
class ChainResult:
    ok: bool
    rows: int
    first_bad_seq: int | None = None


def _canonical(obj: dict) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


class ChainedAuditSink:
    def __init__(self, path: str, key: str, clock=time.time) -> None:
        if not key:
            raise ValueError("audit key is required")
        self._key, self._clock = key.encode(), clock
        self._lock = threading.Lock()
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._db.execute("CREATE TABLE IF NOT EXISTS audit (seq INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL, "
                         "event TEXT NOT NULL, body TEXT NOT NULL, prev TEXT NOT NULL, hash TEXT NOT NULL)")
        self._db.commit()

    def _mac(self, prev: str, ts: float, event: str, body: str) -> str:
        msg = f"{prev}|{ts!r}|{event}|{body}".encode()
        return hmac.new(self._key, msg, hashlib.sha256).hexdigest()

    def record(self, event: str, **fields: object) -> None:
        clean = {k: v for k, v in fields.items() if k in ALLOWED_FIELDS and v is not None}
        clean.setdefault("correlation_id", correlation_id_var.get())
        body = _canonical(redact(clean))  # type: ignore[arg-type]
        ts = round(self._clock(), 3)
        with self._lock:
            row = self._db.execute("SELECT hash FROM audit ORDER BY seq DESC LIMIT 1").fetchone()
            prev = row[0] if row else GENESIS
            self._db.execute("INSERT INTO audit (ts, event, body, prev, hash) VALUES (?,?,?,?,?)",
                             (ts, event, body, prev, self._mac(prev, ts, event, body)))
            self._db.commit()

    def verify(self) -> ChainResult:
        prev, n = GENESIS, 0
        with self._lock:
            rows = self._db.execute("SELECT seq, ts, event, body, prev, hash FROM audit ORDER BY seq").fetchall()
        for seq, ts, event, body, p, h in rows:
            if p != prev or not hmac.compare_digest(h, self._mac(prev, ts, event, body)):
                return ChainResult(False, n, seq)
            prev, n = h, n + 1
        return ChainResult(True, n)

    def rows(self, limit: int = 100) -> list[dict]:
        with self._lock:
            cur = self._db.execute("SELECT seq, ts, event, body FROM audit ORDER BY seq DESC LIMIT ?", (limit,))
            return [{"seq": s, "ts": t, "event": e, **json.loads(b)} for s, t, e, b in cur.fetchall()]
