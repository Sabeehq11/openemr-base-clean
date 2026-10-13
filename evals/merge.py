"""Merge mode for the live report: add this run's cases to live-latest.json without touching earlier entries.

Earlier case entries are kept exactly as they are (result, error, ms). Which run a case came from is recorded in the
separate `runs` list, so nothing from an old run is relabelled as new, and each run keeps its own spend.
A case id already in the file is refused unless `rerun` is set; then only that entry is replaced and it moves to the
new run.
"""
from __future__ import annotations

import json
from pathlib import Path


def merge(existing: dict | None, new_cases: list[dict], run: dict, rerun: bool = False) -> dict:
    """Pure function: returns the new report document. `run` needs run, generated, model, spend_usd, spend_cap_usd."""
    cases: list[dict] = list(existing["cases"]) if existing else []
    runs: list[dict] = [dict(r) for r in existing.get("runs", [])] if existing else []
    if existing and not runs:  # a file written before runs existed is one run: the one that produced it
        runs = [{"run": existing["generated"][:10], "generated": existing["generated"],
                 "model": existing.get("model"), "spend_usd": existing.get("spend_usd", 0.0),
                 "spend_cap_usd": existing.get("spend_cap_usd"), "case_ids": [c["id"] for c in cases]}]
    have = {c["id"] for c in cases}
    clash = have & {c["id"] for c in new_cases}
    if clash and not rerun:
        raise ValueError(f"already in live-latest.json, pass --rerun to replace: {sorted(clash)}")
    for c in new_cases:
        if c["id"] in have:
            cases = [c if x["id"] == c["id"] else x for x in cases]
            for r in runs:
                r["case_ids"] = [i for i in r["case_ids"] if i != c["id"]]
        else:
            cases.append(c)
    label, n = run["run"], 1
    while any(r["run"] == label for r in runs):  # two runs on one day stay distinguishable
        n += 1
        label = f"{run['run']}#{n}"
    runs.append({**run, "run": label, "case_ids": [c["id"] for c in new_cases]})
    return {"tier": "live", "generated": run["generated"], "passed": sum(c["passed"] for c in cases),
            "total": len(cases), "model": run.get("model"), "spend_usd": round(sum(r["spend_usd"] for r in runs), 4),
            "spend_cap_usd": run.get("spend_cap_usd"), "runs": runs, "cases": cases}


def render_md(doc: dict, categories: tuple[str, ...]) -> str:
    runs, cases = doc["runs"], doc["cases"]
    run_of = {i: r["run"] for r in runs for i in r["case_ids"]}
    lines = ["# Eval results: live tier", "",
             f"Last updated {doc['generated']}. **{doc['passed']}/{doc['total']} passed.** "
             f"Cumulative model spend **${doc['spend_usd']:.4f}** over {len(runs)} runs.", "",
             "| Run | Generated | Cases | Model spend | Cap |", "|---|---|---|---|---|"]
    for r in runs:
        lines.append(f"| {r['run']} | {r['generated']} | {len(r['case_ids'])} | ${r['spend_usd']:.4f} | "
                     f"${r['spend_cap_usd']:.2f} |")
    for r in runs:
        if r.get("note"):
            lines += ["", f"Run {r['run']}: {r['note']}"]
    lines += ["", "| Category | Passed | Cases |", "|---|---|---|"]
    for cat in categories:
        rs = [c for c in cases if c["category"] == cat]
        if rs:
            lines.append(f"| {cat} | {sum(c['passed'] for c in rs)}/{len(rs)} | {len(rs)} |")
    lines += ["", "No model call = the case asserts real service behaviour but never reaches the model, so it is not "
              "evidence about the model.", "",
              "| Case | Run | Category | Guards against | Model call | Result |", "|---|---|---|---|---|---|"]
    for c in cases:
        model = "no model call" if c.get("no_model_call") else "yes"
        res = "pass" if c["passed"] else f"FAIL: {c['error']}"
        lines.append(f"| {c['id']} | {run_of.get(c['id'], '?')} | {c['category']} | {c['guards']} | {model} | {res} |")
    return "\n".join(lines) + "\n"


def write(results_dir: Path, new_cases: list[dict], run: dict, categories: tuple[str, ...],
          rerun: bool = False) -> Path:
    path = results_dir / "live-latest.json"
    existing = json.loads(path.read_text()) if path.exists() else None
    doc = merge(existing, new_cases, run, rerun)
    results_dir.mkdir(exist_ok=True)
    path.write_text(json.dumps(doc, indent=2))
    md = results_dir / "live-latest.md"
    md.write_text(render_md(doc, categories))
    return md
