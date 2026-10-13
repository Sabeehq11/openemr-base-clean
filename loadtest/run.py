"""Load test runner.  PYTHONPATH not needed:  agent/.venv/bin/python -m loadtest.run --help

Targets a SEPARATE agent instance (own DATA_DIR, rate limit off) so demo traces and the audit chain stay clean.
Every report is labelled with --source (stub | mock | real); the label must match how that agent was started.
Real runs need LOADTEST_MAX_USD (at most 2.00), a fixed --requests count, and are aborted before the next request
once spend (read from the target's own dashboard) reaches the cap.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import random
import sys
import time

import httpx

from . import client, report, scenarios

HARD_CAP_USD = 2.00
DEFAULT_CONTAINERS = ["agent-load", "development-easy-light-openemr-1", "development-easy-light-mysql-1"]


def parse(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--source", choices=["stub", "mock", "real"], required=True)
    p.add_argument("--users", type=int, required=True)
    p.add_argument("--duration", type=int, default=0, help="seconds (stub and mock only)")
    p.add_argument("--requests", type=int, default=0, help="total scenario starts; required for --source real")
    p.add_argument("--base", default="http://127.0.0.1:8401")
    p.add_argument("--think", default="1,3", help="min,max think time seconds")
    p.add_argument("--name", default="")
    p.add_argument("--containers", default=",".join(DEFAULT_CONTAINERS))
    p.add_argument("--seed", type=int, default=7)
    p.add_argument("--calibrate", action="store_true", help="fixed use-case rotation; needs --requests")
    a = p.parse_args(argv)
    if a.source == "real":
        if not a.requests:
            p.error("--source real needs a fixed --requests count")
        if a.duration:
            p.error("--source real cannot be time-based")
    if a.calibrate and not a.requests:
        p.error("--calibrate needs --requests")
    if a.source != "real" and not (a.duration or a.requests):
        p.error("give --duration or --requests")
    return a


def spend_cap(source: str) -> float:
    if source != "real":
        return 0.0
    raw = os.environ.get("LOADTEST_MAX_USD")
    if not raw:
        sys.exit("--source real refuses to start without LOADTEST_MAX_USD (max 2.00).")
    cap = float(raw)
    if not 0 < cap <= HARD_CAP_USD:
        sys.exit(f"LOADTEST_MAX_USD must be in (0, {HARD_CAP_USD}].")
    return cap


async def target_spend(c: httpx.AsyncClient, base: str, dash_token: str) -> float:
    r = await c.get(f"{base}/dashboard.json", params={"window": 86400}, auth=("x", dash_token))
    return float(r.json()["summary"]["cost_usd"]) if r.status_code == 200 else float("inf")  # unknowable = stop


def load_env() -> dict[str, str]:
    from tests.oauth_helper import load_env as le
    return le()


async def vuser(i: int, a: argparse.Namespace, ctx: dict, stop: asyncio.Event, results: list, budget: list) -> None:
    rng = random.Random(a.seed + i)
    lo, hi = (float(x) for x in a.think.split(","))
    doc = ctx["docs"][i % len(ctx["docs"])]
    other = ctx["docs"][(i + 1) % len(ctx["docs"])]
    mine, theirs = ctx["patients"][doc], ctx["patients"][other]
    while not stop.is_set():
        if a.requests:
            if budget[0] <= 0:
                return
            budget[0] -= 1
        if a.source == "real" and await target_spend(ctx["http"], a.base, ctx["dash"]) >= ctx["cap"]:
            print("spend cap reached; stopping before the next request", file=sys.stderr)
            stop.set()
            return
        conv = None
        if a.calibrate:
            steps = scenarios.CALIBRATION[(a.requests - budget[0] - 1) % len(scenarios.CALIBRATION)]
        else:
            steps = scenarios.pick(rng)
        for uc, question, new in steps:
            patient = rng.choice(theirs if uc == "denied" else mine)
            res = await client.chat(ctx["http"], a.base, ctx["secret"], ctx["uuids"][doc], ctx["tokens"][doc],
                                    patient, question, None if new else conv)
            conv = res.conversation_id
            results.append((uc, res))
            if res.outcome in ("http_429", "http_503"):
                break
        await asyncio.sleep(rng.uniform(lo, hi))


async def main(argv: list[str]) -> int:
    a = parse(argv)
    cap = spend_cap(a.source)
    env, fx = load_env(), __import__("tests.oauth_helper", fromlist=["x"]).load_fixture()
    docs = ["copilot_doc_a", "copilot_doc_b"]
    owners = {u: [p for p, o in fx["patient_owner"].items() if o == fx["user_ids"][u]] for u in docs}
    ctx = {"docs": docs, "uuids": fx["user_uuids"], "tokens": client.fetch_tokens(docs), "cap": cap,
           "patients": {u: [fx["patient_uuids"][p] for p in owners[u]] for u in docs},
           "secret": env["MODULE_SIGNING_SECRET"], "dash": env["DASHBOARD_TOKEN"]}
    names = a.containers.split(",")
    wall_start = time.time()
    wall_start = time.time()
    results: list = []
    samples: list = []
    stop, stats_stop = asyncio.Event(), asyncio.Event()
    budget = [a.requests]
    async with httpx.AsyncClient(timeout=60.0, limits=httpx.Limits(max_connections=a.users * 2 + 4)) as http:
        ctx["http"] = http
        t_stats = asyncio.create_task(report.sample_loop(names, samples, stats_stop))
        t0 = time.perf_counter()
        users = [asyncio.create_task(vuser(i, a, ctx, stop, results, budget)) for i in range(a.users)]
        if a.duration:
            await asyncio.sleep(a.duration)
            stop.set()
        await asyncio.gather(*users)
        elapsed = time.perf_counter() - t0
        stats_stop.set()
        await t_stats
        dash = (await http.get(f"{a.base}/dashboard.json", params={"window": 86400}, auth=("x", ctx["dash"]))).json()
    lat = [r.total_ms for _, r in results]
    first = [r.first_event_ms for _, r in results if r.first_event_ms is not None]
    outcomes: dict[str, int] = {}
    for _, r in results:
        outcomes[r.outcome] = outcomes.get(r.outcome, 0) + 1
    # A denial is only expected for the deliberate other-physician case; a denial for the caller's own patient is a
    # failure (authorization fails closed when OpenEMR is too slow), and so is anything not in the expected set.
    want = {"denied": "denied", "declined": "declined"}
    unexpected: dict[str, int] = {}
    for uc, r in results:
        if r.outcome != want.get(uc, "answer"):
            unexpected[f"{uc}->{r.outcome}"] = unexpected.get(f"{uc}->{r.outcome}", 0) + 1
    bad = sum(unexpected.values())
    summ = dash["summary"]
    rep = {"data_source": a.source, "model": env.get("OPENROUTER_MODEL") if a.source == "real" else a.source,
           "users": a.users, "duration_s": round(elapsed, 1), "requests": len(results),
           "rps": round(len(results) / elapsed, 2), "error_rate": round(bad / max(1, len(results)), 4),
           "outcomes": outcomes,
           "unexpected": unexpected,
           "latency_ms": {f"p{p}": report.pct(lat, p) for p in (50, 95, 99)},
           "first_event_ms": {f"p{p}": report.pct(first, p) for p in (50, 95)},
           "by_use_case": {uc: {"n": sum(1 for u, _ in results if u == uc),
                                "p50": report.pct([r.total_ms for u, r in results if u == uc], 50),
                                "p95": report.pct([r.total_ms for u, r in results if u == uc], 95)}
                           for uc in sorted({u for u, _ in results})},
           "docker": report.summarize_stats(samples), "spans": report.span_stats(names[0], wall_start), "spans": report.span_stats(names[0], wall_start), "spend_usd": summ["cost_usd"] if a.source == "real" else None,
           "target_summary": summ, "target_alerts": dash["alerts"]}
    print(report.write(rep, a.name or f"{a.source}-{a.users}u"))
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main(sys.argv[1:])))
