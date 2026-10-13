"""Percentiles, docker stats sampling and report writing. Every report carries its data_source label."""
from __future__ import annotations

import asyncio
import json
import math
import re
import subprocess
import time
from pathlib import Path

OUT = Path(__file__).resolve().parent / "results"
_UNITS = {"B": 1, "KiB": 1024, "MiB": 1024**2, "GiB": 1024**3}


def pct(values: list[float], p: float) -> float | None:
    if not values:
        return None
    s = sorted(values)
    return s[min(len(s) - 1, max(0, math.ceil(p / 100 * len(s)) - 1))]


def _mem_mib(text: str) -> float:
    m = re.match(r"([\d.]+)\s*([KMG]?i?B)", text)
    return round(float(m.group(1)) * _UNITS.get(m.group(2), 1) / 1024**2, 1) if m else 0.0


def docker_sample(names: list[str]) -> dict[str, tuple[float, float]]:
    try:
        out = subprocess.check_output(["docker", "stats", "--no-stream", "--format",
                                       "{{.Name}}|{{.CPUPerc}}|{{.MemUsage}}", *names], text=True, timeout=20)
    except (subprocess.SubprocessError, OSError):
        return {}
    res = {}
    for line in out.strip().splitlines():
        name, cpu, mem = line.split("|")
        res[name] = (float(cpu.rstrip("%") or 0), _mem_mib(mem.split("/")[0].strip()))
    return res


async def sample_loop(names: list[str], samples: list[dict], stop: asyncio.Event, every_s: float = 2.0) -> None:
    while not stop.is_set():
        s = await asyncio.to_thread(docker_sample, names)
        if s:
            samples.append(s)
        try:
            await asyncio.wait_for(stop.wait(), timeout=every_s)
        except asyncio.TimeoutError:
            pass


def summarize_stats(samples: list[dict]) -> dict:
    per: dict[str, list[tuple[float, float]]] = {}
    for s in samples:
        for n, v in s.items():
            per.setdefault(n, []).append(v)
    return {n: {"cpu_avg_pct": round(sum(c for c, _ in v) / len(v), 1), "cpu_max_pct": max(c for c, _ in v),
                "mem_avg_mib": round(sum(m for _, m in v) / len(v), 1), "mem_max_mib": max(m for _, m in v),
                "samples": len(v)} for n, v in per.items()}


def write(report: dict, name: str) -> Path:
    OUT.mkdir(exist_ok=True)
    base = OUT / f"{name}-{time.strftime('%Y%m%d-%H%M%S')}"
    base.with_suffix(".json").write_text(json.dumps(report, indent=2))
    lat = report["latency_ms"]
    lines = [f"# {name}", "", f"**data_source: {report['data_source']}**  model: {report['model']}  "
             f"users: {report['users']}  duration: {report['duration_s']} s  requests: {report['requests']}", "",
             "| metric | value |", "|---|---|"]
    lines += [f"| {k} | {v} |" for k, v in {
        "p50 ms": lat["p50"], "p95 ms": lat["p95"], "p99 ms": lat["p99"],
        "time to first event p50/p95 ms": f"{report['first_event_ms']['p50']}/{report['first_event_ms']['p95']}",
        "requests/s": report["rps"], "error rate (unexpected outcomes)": report["error_rate"],
        "unexpected": report["unexpected"], "outcomes": report["outcomes"],
        "spend usd (real only)": report["spend_usd"]}.items()]
    lines += ["", "docker stats: " + json.dumps(report["docker"]), "", "| span | n | p50 ms | p95 ms | errors |", "|---|---|---|---|---|"]
    lines += [f"| {k} | {v['n']} | {v['p50']} | {v['p95']} | {v['errors']} |" for k, v in report.get("spans", {}).items()]
    base.with_suffix(".md").write_text("\n".join(lines) + "\n")
    return base.with_suffix(".md")


_QUERY = (
    "import sqlite3,json,sys;c=sqlite3.connect('file:/srv/agent/data/traces.db?mode=ro',uri=True);t=float(sys.argv[1]);"
    "print(json.dumps(c.execute('select kind,name,status,latency_ms from spans where ts>=?',(t,)).fetchall()))")


def span_stats(container: str, since_ts: float) -> dict:
    """Per-span latency from the target's own trace store (spans written since the run started)."""
    try:
        out = subprocess.check_output(["docker", "exec", container, "python", "-c", _QUERY, str(since_ts)],
                                      text=True, timeout=30)
        rows = json.loads(out)
    except (subprocess.SubprocessError, OSError, ValueError):
        return {}
    by: dict[str, list[float]] = {}
    fail: dict[str, int] = {}
    for kind, name, status, ms in rows:
        k = f"{kind}:{name}"
        by.setdefault(k, []).append(ms)
        fail[k] = fail.get(k, 0) + (status != "ok")
    return {k: {"n": len(v), "p50": pct(v, 50), "p95": pct(v, 95), "errors": fail[k]} for k, v in sorted(by.items())}


_QUERY = (
    "import sqlite3,json,sys;c=sqlite3.connect('file:/srv/agent/data/traces.db?mode=ro',uri=True);t=float(sys.argv[1]);"
    "print(json.dumps(c.execute('select kind,name,status,latency_ms from spans where ts>=?',(t,)).fetchall()))")


def span_stats(container: str, since_ts: float) -> dict:
    """Per-span latency from the target's own trace store (spans written since the run started)."""
    try:
        out = subprocess.check_output(["docker", "exec", container, "python", "-c", _QUERY, str(since_ts)],
                                      text=True, timeout=30)
        rows = json.loads(out)
    except (subprocess.SubprocessError, OSError, ValueError):
        return {}
    by: dict[str, list[float]] = {}
    fail: dict[str, int] = {}
    for kind, name, status, ms in rows:
        k = f"{kind}:{name}"
        by.setdefault(k, []).append(ms)
        fail[k] = fail.get(k, 0) + (status != "ok")
    return {k: {"n": len(v), "p50": pct(v, 50), "p95": pct(v, 95), "errors": fail[k]} for k, v in sorted(by.items())}
