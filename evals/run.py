"""Eval runner. Writes evals/results/<tier>-latest.json and .md.

  python -m evals.run                run the deterministic stub tier (no network, no cost)
  python -m evals.run --tier live    run the live-model tier (see evals/live.py; needs OPENROUTER_API_KEY, costs money)
"""
from __future__ import annotations

import json
import sys
import time
import traceback
from collections import defaultdict
from pathlib import Path

from .registry import CATEGORIES, load_all

RESULTS = Path(__file__).resolve().parent / "results"


def run_stub() -> list[dict]:
    out = []
    for c in load_all():
        if c.tier != "stub":
            continue
        t0, err = time.perf_counter(), None
        try:
            c.fn()
        except BaseException as e:  # a case that crashes is a failed case, not a skipped one
            err = "".join(traceback.format_exception_only(type(e), e)).strip()[:400]
        out.append({"id": c.id, "category": c.category, "guards": c.guards, "passed": err is None, "error": err,
                    "ms": round((time.perf_counter() - t0) * 1000, 1)})
    return out


def write_report(results: list[dict], tier: str, extra: dict | None = None) -> Path:
    RESULTS.mkdir(exist_ok=True)
    by_cat: dict[str, list[dict]] = defaultdict(list)
    for r in results:
        by_cat[r["category"]].append(r)
    passed = sum(r["passed"] for r in results)
    stamp = time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime())
    (RESULTS / f"{tier}-latest.json").write_text(json.dumps(
        {"tier": tier, "generated": stamp, "passed": passed, "total": len(results), **(extra or {}),
         "cases": results}, indent=2))
    lines = [f"# Eval results: {tier} tier", "", f"Generated {stamp}. **{passed}/{len(results)} passed.**", ""]
    for k, v in (extra or {}).items():
        lines.append(f"- {k}: {v}")
    lines += ["", "| Category | Passed | Cases |", "|---|---|---|"]
    for cat in CATEGORIES:
        rs = by_cat.get(cat, [])
        if rs:
            lines.append(f"| {cat} | {sum(r['passed'] for r in rs)}/{len(rs)} | {len(rs)} |")
    lines += ["", "| Case | Category | Guards against | Result |", "|---|---|---|---|"]
    for r in results:
        lines.append(f"| {r['id']} | {r['category']} | {r['guards']} | {'pass' if r['passed'] else 'FAIL: ' + str(r['error'])} |")
    path = RESULTS / f"{tier}-latest.md"
    path.write_text("\n".join(lines) + "\n")
    return path


def main(argv: list[str]) -> int:
    tier = argv[argv.index("--tier") + 1] if "--tier" in argv else "stub"
    if tier == "live":
        from .live import run_live
        return run_live()
    results = run_stub()
    path = write_report(results, "stub")
    failed = [r for r in results if not r["passed"]]
    print(f"{len(results) - len(failed)}/{len(results)} passed -> {path}")
    for r in failed:
        print(f"FAIL {r['id']}: {r['error']}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
