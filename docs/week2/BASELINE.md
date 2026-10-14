# Week 1 baseline (recorded at the start of Week 2)

Purpose: a fixed, measured reference so Week 2 can prove it did not break Week 1. Every number is labelled **measured** (run for this document) or **quoted** (copied from a Week 1 document, not re-run).

## Reference point

| Item | Value |
|---|---|
| Git tag | `w1-baseline` |
| Commit | `2e1ed62` (`docs: move week 1 docs to docs/week1 and group co-pilot scripts under scripts/copilot`) |
| Branch the baseline was taken from | `main`, in sync with `origin/main`, working tree clean |
| Date measured | 2026-10-14 (UTC) |
| Machine | Darwin arm64 (Apple laptop) |
| Python used for the runs | 3.10.13 (`agent/.venv`) |
| Python in the agent container image | 3.12 (`agent/Dockerfile`, `python:3.12-slim`) |

The local Python (3.10) differs from the container (3.12). Results below were measured on 3.10 only. A 3.12 run was not made.

Dependency versions installed in `agent/.venv` (**measured**; they match `agent/requirements.txt`):

| Package | Version |
|---|---|
| fastapi | 0.115.6 |
| uvicorn | 0.34.0 |
| httpx | 0.28.1 |
| pydantic | 2.10.4 (pydantic_core 2.27.2) |
| pydantic-settings | 2.7.0 |
| PyJWT | 2.10.1 |
| pytest | 8.3.4 |
| pytest-asyncio | 0.25.0 |
| starlette (transitive) | 0.41.3 |
| anyio (transitive) | 4.15.1 |

## Offline agent tests (measured)

Command, from the repo root:

```bash
cd agent && .venv/bin/python -m pytest -q -p no:cacheprovider
```

| Result | Count |
|---|---|
| Collected | 552 |
| Passed | 522 |
| Skipped | 30 |
| Failed | 0 |
| Warnings | 1 (a starlette/anyio deprecation notice, not from our code) |
| Duration | about 8.5 s |

All 30 skips have one reason: the test "needs the live OpenEMR stack; run with `OE_LIVE=1`". They are in `tests/test_authz_live.py`, `tests/test_gateway_live.py`, `tests/test_module_token_live.py` and `tests/test_tools_live.py`. No live-stack run was made for this baseline.

Counts differ from older docs because those quote a different run:

- `docs/week1/ARCHITECTURE.md` section 12 says "321 passed with the live stack". That is a different configuration (`OE_LIVE=1`) and an earlier commit, and it is **quoted**, not reproduced here.
- The offline figure to compare against in Week 2 is **522 passed, 30 skipped**.

## Stub-tier evals (measured)

Command, from the repo root:

```bash
PYTHONPATH=agent agent/.venv/bin/python -m evals.run
```

Result: **60/60 passed**, exit code 0.

| Category | Passed | Cases |
|---|---|---|
| invariant | 10/10 | 10 |
| boundary | 11/11 | 11 |
| conflict | 6/6 | 6 |
| authorization | 9/9 | 9 |
| adversarial | 16/16 | 16 |
| failure_injection | 8/8 | 8 |
| **Total** | **60/60** | **60** |

Notes:

- Older docs say 56 stub evals (for example `docs/week1/ARCHITECTURE.md` section 12 and its summary text). The measured and committed result is 60. The committed `evals/results/stub-latest.json` already said 60/60 before this run, so the 56 in those docs is stale, not a regression. Those documents were left as they are, because they are historical Checkpoint 2 records.
- The eval run rewrites the tracked `evals/results/stub-latest.json` and `.md`. The only differences were the "generated" timestamp and per-case millisecond timings; pass or fail outcomes were identical. Those files were restored with `git checkout`, so this baseline commit changes no result files.
- The live-model tier (`--tier live`) was not run. It costs money and is on demand. Its recorded results are in `evals/results/live-latest.*` and `live-baseline-2026-10-12.*` and are not part of the 60/60 figure.

## Performance and cost (quoted, not re-run)

Load tests were deliberately not repeated. Use these documents as the Week 1 reference and compare Week 2 shared paths against them (a Week 2 requirement):

- Latency, throughput, CPU and memory baselines: `docs/week1/PERFORMANCE.md`. Results are labelled `stub`, `mock` or `real` there. Examples quoted from it: stub at 10 users has p95 9,747 ms; stub at 50 users has a 95% denial rate because OpenEMR saturates; the real-model request has p50 about 14 s and p95 about 19 s at 10 users.
- Spend and per-request cost: `docs/week1/AI_COST_ANALYSIS.md`. Real spend during load testing was 0.85 USD over 33 requests, per the Checkpoint 3 section of `docs/week1/ARCHITECTURE.md`.
- Existing alert thresholds (`agent/app/metrics.py`): p95 over 25 s for 5 minutes, error rate over 5% for 10 minutes, tool failure rate over 10% for 10 minutes.

## Other facts recorded at the baseline (measured by inspection)

- No GitHub workflow in `.github/workflows` references `agent/` or `evals/`. The PRD's PR-blocking eval gate does not exist yet.
- No git hooks are installed (`.git/hooks` has only samples), and the `gh` CLI is not installed on this machine.
- `agent/.env` and `agent/data/` are git-ignored and untracked.
- The README's "Deployed app" line says TBD. No public deployment exists at the baseline.

## How to reproduce this baseline

```bash
git checkout w1-baseline
cd agent && .venv/bin/python -m pytest -q -p no:cacheprovider      # expect 522 passed, 30 skipped
cd .. && PYTHONPATH=agent agent/.venv/bin/python -m evals.run       # expect 60/60 passed
git checkout -- evals/results                                        # discard the rewritten result files
```

Any Week 2 change must keep both results the same with `W2_ENABLED` (planned) unset or 0. Week 2 may add tests and cases, so later totals will be higher, but these 522 offline tests and 60 stub cases must continue to pass.

## Corrections to older numbers

| Where | Older statement | Baseline finding | Action |
|---|---|---|---|
| `docs/week1/ARCHITECTURE.md` section 12 and other Week 1 docs | 56 stub evals | 60 | Not edited (historical record) |
| `agent/README.md` line 52 | "56 cases tagged by failure mode" | 60 | Not edited in C0, because C0 must leave everything under `agent/` identical to `w1-baseline`. Fix it in a later checkpoint that is allowed to touch `agent/` (C3 changes the eval docs) |
| `docs/week1/ARCHITECTURE.md` section 12 | 321 passed (live stack) | 522 passed and 30 skipped offline | Different configuration, not edited |
| `README.md` | No test or eval counts before C0 | New Week 1 versus Week 2 table quotes 522 and 60 | Added from this baseline |
