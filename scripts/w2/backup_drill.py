#!/usr/bin/env python3
"""Backup and restore drill for the Week 2 derived store (w2.db). ISOLATED TEMPORARY COPIES ONLY.

  agent/.venv/bin/python scripts/w2/backup_drill.py [--source agent/data/w2.db] [--docs 12] [--out report.json]

Safety, in code and not only in this paragraph:
  * The source is opened read-only (`file:...?mode=ro`) and copied once with SQLite's online backup API into a fresh
    temp directory. Every delete, corruption, restore and check happens on that copy.
  * Every path the drill writes or deletes goes through `inside()`, which resolves real paths and refuses anything not
    under the temp root this run created. The source and the configured DATA_DIR are refused as targets.
  * It never calls the running agent. The "agent starts from a restored copy" check launches a throwaway process on
    another port with its own DATA_DIR inside the temp root.
  * The temp root is removed on exit. Timings and counts are kept in the report.
OpenEMR's database and documents volume are operator-owned and are NOT touched or drilled here.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
AGENT = ROOT / "agent"
FIXTURES = AGENT / "tests" / "fixtures" / "w2"
PATIENT = "11111111-2222-3333-4444-555555555555"
TABLES = ("w2_documents", "w2_facts", "w2_citations")
THROWAWAY_PORT = 8497


class UnsafePath(SystemExit):
    """A drill step tried to touch something outside its own temp directory."""


def inside(root: Path, path: Path, forbidden: tuple[Path, ...] = ()) -> Path:
    """The resolved path if it is strictly under `root` and is none of `forbidden` (or under one of them)."""
    real, base = path.resolve(), root.resolve()
    if real == base or base not in real.parents:
        raise UnsafePath(f"refusing {real}: not under the drill's temp directory {base}")
    for f in forbidden:
        if real == f.resolve() or f.resolve() in real.parents:
            raise UnsafePath(f"refusing {real}: it is the source or the configured data directory")
    return real


def sha256_file(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def snapshot(src: Path, dst: Path) -> None:
    """SQLite online backup from `src` (opened read-only) to the new file `dst`."""
    s = sqlite3.connect(f"file:{src}?mode=ro", uri=True)
    d = sqlite3.connect(dst)
    try:
        s.backup(d)
    finally:
        d.close()
        s.close()


def counts(db: Path) -> dict[str, int]:
    c = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        return {t: c.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in TABLES}
    finally:
        c.close()


def integrity(db: Path) -> str:
    """'ok', or the failure class. Never the message, which can echo content."""
    try:
        c = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        try:
            return c.execute("PRAGMA integrity_check").fetchone()[0]
        finally:
            c.close()
    except sqlite3.DatabaseError as e:
        return f"failed:{type(e).__name__}"


def content_digest(db: Path) -> str:
    """Order-independent digest of all rows: a restore is proven equal in content, not just in counts."""
    c = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    h = hashlib.sha256()
    try:
        for t in TABLES:
            for row in sorted(repr(r) for r in c.execute(f"SELECT * FROM {t}")):
                h.update(row.encode())
    finally:
        c.close()
    return h.hexdigest()


def timed(fn):
    t0 = time.perf_counter()
    out = fn()
    return out, round((time.perf_counter() - t0) * 1000, 1)


def populate(db: Path, docs: int) -> float:
    """Fill a store from committed synthetic fixtures with the canned VLM (no network, no cost). This is also the
    rebuild path: it is what re-extraction does, minus model latency. Returns ms per document."""
    sys.path[:0] = [str(AGENT)]
    from app.w2.ingest import Ingestor
    from app.w2.store import W2Store
    from app.w2.vlm import StubVlm
    from contracts.w2 import DocType
    vlm = StubVlm({DocType.LAB_PDF: (FIXTURES / "vlm_lab_good.json").read_text(),
                   DocType.INTAKE_FORM: (FIXTURES / "vlm_intake_good.json").read_text()})
    ing = Ingestor(W2Store(str(db)), vlm, 8 * 1024 * 1024, 5)
    start = int(time.time() * 1000) % 10_000_000  # ids that cannot collide with rows already in a copied source

    async def run() -> None:
        for i in range(docs):
            name, kind = (("lab_report.pdf", DocType.LAB_PDF) if i % 2 == 0
                          else ("intake_form.pdf", DocType.INTAKE_FORM))
            raw = (FIXTURES / name).read_bytes() + f"\n%% drill {start + i}\n".encode()
            await ing.ingest(PATIENT, start + i, kind, raw)

    t0 = time.perf_counter()
    asyncio.run(run())
    return round((time.perf_counter() - t0) * 1000 / max(docs, 1), 1)


def throwaway_agent(data_dir: Path, root: Path) -> dict:
    """Start a separate agent process on another port over `data_dir` and read its /ready. Never the live agent.
    cwd is the temp root so agent/.env (real secrets) is never loaded."""
    inside(root, data_dir)
    env = {**os.environ, "PYTHONPATH": str(AGENT), "DATA_DIR": str(data_dir), "W2_ENABLED": "true",
           "W2_USE_STUB_VLM": "true", "LLM_PROVIDER": "stub", "MODULE_SIGNING_SECRET": "drill-only",
           "OPENEMR_BASE_URL": "http://127.0.0.1:9", "TOOL_TIMEOUT_S": "1"}
    py = AGENT / ".venv" / "bin" / "python"
    proc = subprocess.Popen([str(py if py.exists() else sys.executable), "-m", "uvicorn", "app.main:app", "--host",
                             "127.0.0.1", "--port", str(THROWAWAY_PORT), "--no-access-log"], cwd=root, env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    t0, body = time.perf_counter(), None
    try:
        for _ in range(80):
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{THROWAWAY_PORT}/ready", timeout=3) as r:
                    body = json.loads(r.read())
            except urllib.error.HTTPError as e:  # a 503 still carries the checks
                body = json.loads(e.read())
            except (OSError, ValueError):
                time.sleep(0.25)
                continue
            break
    finally:
        proc.terminate()
        proc.wait(timeout=10)
    checks = (body or {}).get("w2_checks", {})
    return {"started": body is not None, "document_store_ok": checks.get("document_store", {}).get("ok"),
            "ready_ms": round((time.perf_counter() - t0) * 1000, 1)}


def golden_rebuild(root: Path) -> dict:
    """Copy only what git tracks or would track (no .env, no data, no venv) into the temp root and run the eval gate
    there: proof the golden set and gate rebuild from the repository alone."""
    dest = root / "clean-checkout"
    files = subprocess.run(["git", "ls-files", "-co", "--exclude-standard"], cwd=ROOT, capture_output=True,
                           text=True, check=True).stdout.splitlines()
    copied = 0
    for f in files:
        s = ROOT / f
        if s.is_file() and not f.startswith(("agent/data/", "agent/.venv/", "node_modules/", "vendor/")) and \
                (f.startswith(("agent/", "evals/", "contracts/", "docs/week2/", "scripts/w2/")) or "/" not in f):
            t = inside(root, dest / f)
            t.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(s, t)
            copied += 1
    py = AGENT / ".venv" / "bin" / "python"
    t0 = time.perf_counter()
    r = subprocess.run([str(py if py.exists() else sys.executable), "-m", "evals.gate"], cwd=dest,
                       capture_output=True, text=True)
    tail = [ln for ln in r.stdout.splitlines() if ln.strip()][-3:]
    return {"files_copied": copied, "exit_code": r.returncode, "seconds": round(time.perf_counter() - t0, 1),
            "tail": tail}


def drill(source: Path, docs: int, data_dir: Path, run_agent: bool = True, run_gate: bool = True) -> dict:
    """Run the whole drill. `source` is only ever read; `data_dir` is the live data directory the drill must never
    write to. Returns the report."""
    forbidden = (source, data_dir)
    live_hash = sha256_file(source) if source.exists() else None
    root = Path(tempfile.mkdtemp(prefix="w2-drill-"))
    rep: dict = {"steps_ms": {}}
    try:
        work = inside(root, root / "work" / "w2.db", forbidden)
        snap = inside(root, root / "backup" / "w2.db", forbidden)
        work.parent.mkdir(parents=True)
        snap.parent.mkdir(parents=True)
        if source.exists():
            _, rep["steps_ms"]["copy_source_readonly"] = timed(lambda: snapshot(source, work))
            rep["source_rows"] = counts(work)
        if not work.exists() or not counts(work)["w2_documents"]:
            rep["seeded_in_copy"] = docs  # the live store is empty or absent: the COPY is filled from fixtures
            rep["populate_ms_per_doc_stub_vlm"] = populate(work, docs)
        before, digest = counts(work), content_digest(work)
        _, rep["steps_ms"]["snapshot"] = timed(lambda: snapshot(work, snap))
        rep["snapshot_bytes"], rep["counts_at_snapshot"] = snap.stat().st_size, before
        extra = 3  # written after the snapshot: what the RPO window loses
        populate(work, extra)
        rep["documents_written_after_snapshot"] = extra
        rep["after_disaster"], rep["restore_verified"] = {}, {}
        for label in ("deleted", "corrupted"):  # both acted on the temp copy only
            inside(root, work, forbidden)
            if label == "deleted":
                work.unlink()
            else:
                work.write_bytes(os.urandom(4096))
            rep["after_disaster"][label] = integrity(work) if work.exists() else "missing"
            _, ms = timed(lambda: shutil.copy2(inside(root, snap, forbidden), inside(root, work, forbidden)))
            rep["steps_ms"][f"restore_after_{label}"] = ms
            rep["restore_verified"][label] = (integrity(work) == "ok" and counts(work) == before
                                              and content_digest(work) == digest)
            if label == "deleted":
                populate(work, extra)  # so the second disaster also hits a store ahead of its snapshot
        if run_agent:
            agent_dir = root / "agent_data"
            agent_dir.mkdir()
            shutil.copy2(work, agent_dir / "w2.db")
            rep["agent_from_restored_copy"] = throwaway_agent(agent_dir, root)
        fresh = inside(root, root / "rebuild" / "w2.db", forbidden)  # no backup at all: re-extract from fixtures
        fresh.parent.mkdir()
        per_doc = populate(fresh, before["w2_documents"])
        rep["rebuild"] = {"documents": before["w2_documents"], "ms_per_doc_stub_vlm": per_doc,
                          "documents_match_snapshot": counts(fresh)["w2_documents"] == before["w2_documents"]}
        if run_gate:
            rep["golden_set_rebuild"] = golden_rebuild(root)
    finally:
        shutil.rmtree(root, ignore_errors=True)
    rep["temp_dir_removed"] = not root.exists()
    rep["live_source_unchanged"] = (sha256_file(source) == live_hash) if live_hash else True
    return rep


def main(argv: list[str]) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--source", default=str(AGENT / "data" / "w2.db"))
    p.add_argument("--docs", type=int, default=12, help="documents seeded into the COPY when the source is empty")
    p.add_argument("--data-dir", default=str(AGENT / "data"), help="the live data directory; never written")
    p.add_argument("--skip-agent", action="store_true")
    p.add_argument("--skip-gate", action="store_true")
    p.add_argument("--out", default="")
    a = p.parse_args(argv)
    rep = drill(Path(a.source), a.docs, Path(a.data_dir), not a.skip_agent, not a.skip_gate)
    text = json.dumps(rep, indent=2)
    print(text)
    if a.out:
        Path(a.out).write_text(text + "\n")
    ok = (rep["live_source_unchanged"] and rep["temp_dir_removed"] and all(rep["restore_verified"].values()))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
