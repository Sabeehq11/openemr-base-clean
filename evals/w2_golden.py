"""W2 golden set runner. Cases are JSONL under evals/w2/golden/. Each one runs the real ingest, supervisor, workers,
verifier and /v1/w2/chat router with a stub vision model and a scripted second model call, then is scored by the five
boolean rubrics in w2_rubrics.py.

  python -m evals.w2_golden      run all cases; write evals/results/w2-golden-latest.json and .md

Case fields: id, category, guards, critical, rubrics (beyond the two that always apply), uploads, question,
patient ("other" = a caller with no access), stage_b, expected, sentinels, fhir_down.
  uploads: [{"doc_type", "vlm" (fixture json or null), "file" (pdf or png), "vlm_error" (simulate an outage)}]
  stage_b: null (model down) | "garbage" | [{"text", "refs"}]. A ref is "fact:<text part>" (a document fact offered
           to the model), "chunk:<n or id part>" (a guideline chunk offered) or "raw:<id>" (used as written).
"""
from __future__ import annotations

import json
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "agent"))

from .w2_rubrics import RUBRICS, score  # noqa: E402

GOLDEN = HERE / "w2" / "golden"
RESULTS = HERE / "results"
EXPECTED_COUNTS = {"extraction": 12, "retrieval": 10, "citations": 8, "refusals": 8, "missing_data": 6,
                   "phi_logging": 6}
MIN_PER_RUBRIC = 5


def load_cases() -> list[dict]:
    cases: list[dict] = []
    for f in sorted(GOLDEN.glob("*.jsonl")):
        for line in f.read_text().splitlines():
            if line.strip():
                cases.append(json.loads(line))
    return cases


def check_integrity(cases: list[dict], full: bool = True) -> list[str]:
    """Problems with the set itself. A set that is the wrong shape must not be scored as if it were the real one."""
    bad: list[str] = []
    ids = [c["id"] for c in cases]
    bad += [f"duplicate id {i}" for i, n in Counter(ids).items() if n > 1]
    counts = Counter(c["category"] for c in cases)
    bad += [f"unknown category {k}" for k in counts if k not in EXPECTED_COUNTS]
    if full:
        bad += [f"category {k}: {counts.get(k, 0)} cases, expected {n}" for k, n in EXPECTED_COUNTS.items()
                if counts.get(k, 0) != n]
        applied = Counter(r for c in cases for r in dict.fromkeys(["schema_valid", "no_phi_in_logs",
                                                                  *c.get("rubrics", [])]))
        bad += [f"rubric {r} applies to {applied.get(r, 0)} cases, need {MIN_PER_RUBRIC}" for r in RUBRICS
                if applied.get(r, 0) < MIN_PER_RUBRIC]
    for c in cases:
        if not c.get("guards") or "expected" not in c or not isinstance(c.get("critical"), bool):
            bad.append(f"{c['id']}: needs guards, expected and a boolean critical flag")
        bad += [f"{c['id']}: unknown rubric {r}" for r in c.get("rubrics", []) if r not in RUBRICS]
        if c.get("critical") and c["category"] == "refusals" and "safe_refusal" not in c.get("rubrics", []):
            bad.append(f"{c['id']}: a critical refusal case must apply safe_refusal")
        if c.get("critical") and c["category"] == "phi_logging" and "no_phi_in_logs" not in c.get("rubrics", []) \
                and not c.get("sentinels"):
            bad.append(f"{c['id']}: a critical phi_logging case must seed sentinels")
    return bad


# ---- one case ---------------------------------------------------------------------------------------------------

FIX = HERE.parent / "agent" / "tests" / "fixtures" / "w2"
DEFAULT_SENSITIVE = ["11111111-2222-3333-4444-555555555555", "Syntheticson", "555-010-0199", "1970-03-14",
                     "lab_report", "intake_form.pdf", "Hemoglobin A1c   7.9", "Metformin", "Penicillin"]


class _ScriptedLLM:
    """Stage A is the Week 1 demo model. Stage B (no tools offered) returns the case's scripted draft, with refs
    resolved against the ids actually offered in that request, so a case never hard-codes a generated id."""

    def __init__(self, stage_b) -> None:
        self.stage_b, self.b_calls = stage_b, 0

    async def complete(self, messages, tools):
        from app.llm import DemoStubLLM, LLMError, LLMResponse, Usage
        if tools:
            return await DemoStubLLM().complete(messages, tools)
        self.b_calls += 1
        if self.stage_b is None:
            raise LLMError("down")
        if self.stage_b == "garbage":
            return LLMResponse(text="not json", usage=Usage(1, 1, 0.0))
        offered = messages[1]["content"]
        facts, chunks, block = [], [], None
        for line in offered.splitlines():
            if line.startswith("[PATIENT DOCUMENT FACTS]"):
                block = facts
            elif line.startswith("[GUIDELINE EVIDENCE]"):
                block = chunks
            elif line.startswith("- id=") and block is not None:
                block.append(line)
        claims = [{"text": c["text"], "source_ids": [_resolve(r, facts, chunks) for r in c["refs"]]}
                  for c in self.stage_b]
        return LLMResponse(text=json.dumps({"claims": claims}), usage=Usage(10, 5, 0.0))


def _resolve(ref: str, facts: list[str], chunks: list[str]) -> str:
    kind, _, arg = ref.partition(":")
    pick = lambda line: line.split(" | ")[0].removeprefix("- id=")  # noqa: E731
    if kind == "raw":
        return arg
    if kind == "chunk":
        return pick(chunks[int(arg)] if arg.isdigit() else next((ln for ln in chunks if arg in ln), "- id=unresolved | "))
    return pick(next((ln for ln in facts if arg in ln), "- id=unresolved | "))  # not offered: the verifier decides


class _RecordingRetriever:
    def __init__(self, inner) -> None:
        self._inner, self.result = inner, None

    def retrieve(self, query: str, top: int = 5):
        self.result = self._inner.retrieve(query, top)
        return self.result


class _LogCapture:
    """Raw log records, unredacted: the check must see what the code wrote, not what the formatter would hide."""

    def __init__(self) -> None:
        import logging
        self.lines: list[str] = []
        outer = self

        class H(logging.Handler):
            def emit(self, record):
                fields = getattr(record, "fields", None)
                outer.lines.append(record.getMessage() + " " + json.dumps(fields, default=str, sort_keys=True)
                                   + (" exc_info" if record.exc_info else ""))

        self._h, self._lg = H(level=logging.DEBUG), logging.getLogger("agent")

    def __enter__(self):
        import logging
        self._old = self._lg.level
        self._lg.setLevel(logging.DEBUG)
        self._lg.addHandler(self._h)
        return self

    def __exit__(self, *exc):
        self._lg.removeHandler(self._h)
        self._lg.setLevel(self._old)


def _app(tmp: str, llm, fhir_down: bool):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    import httpx
    from pydantic import SecretStr
    from app.config import Settings
    from app.conversation import ConversationStore
    from app.fhir_client import FhirClient
    from app.gateway import Gateway
    from app.tools import ToolRunner
    from app.w2.chat import build_w2_chat_router
    from app.w2.rag import build_retriever
    from app.w2.store import W2Store
    from tests.w2_chat_helpers import NOW, SECRET, Audit, Authz, passthrough

    def h(req):
        if fhir_down:
            return httpx.Response(503)
        res = req.url.path.rsplit("/", 1)[-1]
        return httpx.Response(200, json={"resourceType": "Bundle", "entry": [
            {"resource": {"resourceType": res, "id": "r1", "code": {"text": f"{res} item"}}}]})

    s = Settings(module_signing_secret=SecretStr(SECRET), openemr_base_url="http://oe", data_dir=tmp)
    audit = Audit()
    gw = Gateway(s, audit, clock=lambda: NOW)
    runner = ToolRunner(Authz(), FhirClient(s, transport=httpx.MockTransport(h)))
    store = W2Store(f"{tmp}/w2.db")
    retr = _RecordingRetriever(build_retriever(s, offline=True))
    app = FastAPI()
    app.include_router(build_w2_chat_router(gw, runner, lambda: llm, passthrough, ConversationStore(), store, retr,
                                            audit, None))
    return TestClient(app), store, retr


def _upload(store, patient: str, n: int, up: dict) -> tuple[str, int]:
    """Returns (status, how many calls reached the vision model)."""
    import asyncio
    from app.w2.ingest import Ingestor, RejectedUpload
    from app.w2.vlm import StubVlm, VlmError
    from contracts.w2 import DocType
    dt = DocType(up["doc_type"])
    vlm = StubVlm({dt: (FIX / up["vlm"]).read_text()} if up.get("vlm") else {},
                  VlmError("vlm_down") if up.get("vlm_error") else None)
    ing = Ingestor(store, vlm, 1_000_000, 5)
    try:
        res = asyncio.run(ing.ingest(patient, n, dt, (FIX / up["file"]).read_bytes()))
    except RejectedUpload as e:
        return f"rejected:{e.code}", len(vlm.calls)
    return res.record.status.value, len(vlm.calls)


def run_case(case: dict) -> dict:
    """Run one case and return the outcome the rubrics read. Never raises for a bad case: a crash is a failed case."""
    import tempfile
    from app.w2.facts import facts_from_record
    from contracts.w2 import W2VerifiedAnswer
    from tests.w2_chat_helpers import OTHER_PATIENT, ask, events
    from tests.w2_helpers import PATIENT
    llm = _ScriptedLLM(case.get("stage_b"))
    tmp = tempfile.mkdtemp()
    client, store, retr = _app(tmp, llm, bool(case.get("fhir_down")))
    out: dict = {"extraction": [], "facts": [], "unsupported": 0, "docs": [], "guides": [], "rejections": {},
                 "notes": [], "snippets": [], "evidence_status": "none", "declined": False, "terminal": "none",
                 "answer_valid": True, "llm_calls": 0, "vlm_calls": []}
    with _LogCapture() as cap:
        for i, up in enumerate(case.get("uploads", []), start=7):
            owner = OTHER_PATIENT if case.get("upload_patient") == "other" else PATIENT
            status, calls = _upload(store, owner, i, up)
            out["extraction"].append(status)
            out["vlm_calls"].append(calls)
        for doc_id in store.list_document_ids(PATIENT, 5):
            rec = store.get(PATIENT, doc_id)
            if rec is not None:
                facts, unsupported = facts_from_record(rec)
                out["facts"] += [{"label": f.label, "text": f.text} for f in facts]
                out["unsupported"] += unsupported
        who = OTHER_PATIENT if case.get("patient") == "other" else PATIENT
        evs = events(ask(client, case["question"], who))
        names = [n for n, _ in evs]
        out["terminal"] = next((n for n in reversed(names) if n != "end"), "none")
        out["declined"] = "declined" in names
        data = dict(evs)
        if "answer" in data:
            try:
                ans = W2VerifiedAnswer.model_validate(data["answer"])
            except Exception:  # noqa: BLE001 - an answer that does not parse is the failure being measured
                out["answer_valid"] = False
            else:
                dump = json.loads(ans.model_dump_json())
                out["docs"], out["guides"] = dump["document_claims"], dump["guideline_claims"]
                out["rejections"], out["notes"] = dump["rejections"], dump["notes"]
        if retr.result is not None:
            out["evidence_status"] = retr.result.status.value
            out["snippets"] = [s.chunk_id for s in retr.result.snippets]
    out["llm_calls"] = llm.b_calls
    out["log_lines"] = cap.lines
    out["sensitive"] = [*DEFAULT_SENSITIVE, *case.get("sentinels", [])]
    return out


# ---- whole set --------------------------------------------------------------------------------------------------

def run_all(cases: list[dict]) -> list[dict]:
    results = []
    for c in cases:
        t0, err, scores = time.perf_counter(), None, {}
        try:
            scores = score(c, run_case(c))
        except BaseException as e:  # noqa: BLE001 - a case that crashes is a failed case, never a skipped one
            err = f"{type(e).__name__}"
            scores = {r: False for r in dict.fromkeys(["schema_valid", "no_phi_in_logs", *c.get("rubrics", [])])}
        results.append({"id": c["id"], "category": c["category"], "critical": c["critical"], "guards": c["guards"],
                        "scores": scores, "passed": all(scores.values()), "error": err,
                        "ms": round((time.perf_counter() - t0) * 1000, 1)})
    return results


def summarise(results: list[dict]) -> dict:
    rubric: dict[str, list[bool]] = defaultdict(list)
    cat: dict[str, list[bool]] = defaultdict(list)
    for r in results:
        cat[r["category"]].append(r["passed"])
        for k, v in r["scores"].items():
            rubric[k].append(v)
    pct = lambda xs: round(100.0 * sum(xs) / len(xs), 1) if xs else 0.0  # noqa: E731
    return {"cases": len(results),
            "categories": {k: len(v) for k, v in sorted(cat.items())},
            "rubrics": {k: {"applied": len(rubric[k]), "pass_rate": pct(rubric[k])} for k in RUBRICS if k in rubric},
            "category_pass_rate": {k: pct(v) for k, v in sorted(cat.items())},
            "critical_ids": sorted(r["id"] for r in results if r["critical"]),
            "critical_failures": sorted(r["id"] for r in results if r["critical"] and not r["passed"]),
            "case_ids": sorted(r["id"] for r in results)}


def write_report(results: list[dict], summary: dict, problems: list[str]) -> Path:
    RESULTS.mkdir(exist_ok=True)
    stamp = time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime())
    passed = sum(r["passed"] for r in results)
    (RESULTS / "w2-golden-latest.json").write_text(json.dumps(
        {"generated": stamp, "passed": passed, "total": len(results), "integrity_problems": problems,
         "summary": summary, "cases": results}, indent=2) + "\n")
    lines = [f"# W2 golden set", "", f"Generated {stamp}. **{passed}/{len(results)} cases passed.**", ""]
    if problems:
        lines += ["Integrity problems:", *[f"- {p}" for p in problems], ""]
    lines += ["| Rubric | Applied | Pass rate |", "|---|---|---|"]
    lines += [f"| {k} | {v['applied']} | {v['pass_rate']} |" for k, v in summary["rubrics"].items()]
    lines += ["", "| Category | Cases | Pass rate |", "|---|---|---|"]
    lines += [f"| {k} | {summary['categories'][k]} | {v} |" for k, v in summary["category_pass_rate"].items()]
    lines += ["", "| Case | Category | Critical | Result |", "|---|---|---|---|"]
    for r in results:
        bad = [k for k, v in r["scores"].items() if not v]
        res = "pass" if r["passed"] else "FAIL: " + ", ".join(bad) + (f" ({r['error']})" if r["error"] else "")
        lines.append(f"| {r['id']} | {r['category']} | {'yes' if r['critical'] else ''} | {res} |")
    path = RESULTS / "w2-golden-latest.md"
    path.write_text("\n".join(lines) + "\n")
    return path


def main(argv: list[str]) -> int:
    cases = load_cases()
    problems = check_integrity(cases, full="--partial" not in argv)
    results = run_all(cases)
    summary = summarise(results)
    path = write_report(results, summary, problems)
    failed = [r for r in results if not r["passed"]]
    print(f"{len(results) - len(failed)}/{len(results)} passed -> {path}")
    for r in failed:
        print(f"FAIL {r['id']}: " + ", ".join(k for k, v in r["scores"].items() if not v) + (f" ({r['error']})" if r["error"] else ""))
    for p in problems:
        print(f"INTEGRITY {p}")
    return 1 if failed or problems else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
