"""Ops commands that read the stored traces and audit chain.

  python -m app.cli alerts [--json]     exit 0 = all clear, 2 = at least one alert firing (cron/CI friendly)
  python -m app.cli verify-audit        exit 0 = chain intact, 3 = broken
  python -m app.cli export-audit PATH   write a verified, hash-chained copy (JSONL) for storage the app cannot rewrite
"""
from __future__ import annotations

import json
import sys

from .audit import ChainedAuditSink
from .config import get_settings
from .metrics import evaluate_alerts
from .tracing import TraceStore


def main(argv: list[str]) -> int:
    cfg = get_settings()
    cmd = argv[0] if argv else ""
    if cmd == "alerts":
        alerts = evaluate_alerts(TraceStore(f"{cfg.data_dir}/traces.db"))
        if "--json" in argv:
            print(json.dumps([a.__dict__ for a in alerts], indent=2))
        else:
            for a in alerts:
                v = "n/a" if a.value is None else f"{a.value:.3g}"
                print(f"{'FIRING' if a.firing else 'ok    '} {a.name} value={v} threshold={a.threshold} "
                      f"samples={a.samples} -> {a.runbook}")
        return 2 if any(a.firing for a in alerts) else 0
    key = cfg.audit_hmac_key.get_secret_value()
    if cmd in ("verify-audit", "export-audit"):
        if not key:
            print("AUDIT_HMAC_KEY is not set", file=sys.stderr)
            return 3
        sink = ChainedAuditSink(f"{cfg.data_dir}/audit.db", key)
        res = sink.verify()
        if not res.ok:
            print(f"BROKEN at row {res.first_bad_seq} after {res.rows} good rows", file=sys.stderr)
            return 3
        if cmd == "verify-audit":
            print(f"intact: {res.rows} rows")
            return 0
        if len(argv) < 2:
            print("usage: export-audit PATH", file=sys.stderr)
            return 1
        with open(argv[1], "x") as f:  # "x": never overwrite an earlier export
            for seq, ts, event, body, prev, h in sink._db.execute(
                    "SELECT seq, ts, event, body, prev, hash FROM audit ORDER BY seq"):
                f.write(json.dumps({"seq": seq, "ts": ts, "event": event, "body": body, "prev": prev, "hash": h}) + "\n")
        print(f"exported {res.rows} rows to {argv[1]}")
        return 0
    print(__doc__)
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
