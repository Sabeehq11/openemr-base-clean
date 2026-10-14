# Performance baselines (Checkpoint 3)

Synthetic data only. One laptop, one run per cell. There are no repeats, so run-to-run noise is unknown; treat
differences under about 15% as noise.

## How to read this

Every number carries a **data source**. The three sources are never mixed in one table.

| source | what the model is | what it tells you | spend |
|---|---|---|---|
| `stub` | `DemoStubLLM`, answers instantly | agent, authorization, FHIR and OpenEMR overhead with no model in the loop | 0 |
| `mock` | local server replaying latency and token sizes seen in the real calibration | how model latency interacts with concurrency. **A simulation, not a provider measurement** | 0 |
| `real` | OpenRouter, `anthropic/claude-sonnet-4.5` | actual provider behaviour, **at 1 and 10 users only (33 requests)** | 0.85 USD |

There is no 50-user `real` run (spend ceiling). Every 50-user number below is `stub` or `mock`.

## Machine and setup

- Apple laptop, 10 cores, 16 GB RAM. Docker Desktop VM: 10 CPUs, 8.2 GB RAM, no per-container limits except where a
  failure run says so.
- OpenEMR (`openemr/openemr:flex`), MariaDB 11.8 and the load-test agent run on the same VM as the load generator, so
  the generator competes for CPU. That makes the 50-user numbers somewhat pessimistic.
- The load-test agent is a **separate container** (`agent-load`, port 8401) with its own data volume,
  `RATE_LIMIT_PER_MIN=0` and one uvicorn worker. Demo traces and the demo audit chain were not touched.
- Virtual users loop with 1-3 s think time over the two seeded physicians and their own patients. Mix: UC-1 brief 45%,
  UC-2 what changed 20%, UC-3 labs and medication flags 20%, UC-4 brief plus follow-up 10%, declined advice 3%,
  other-physician patient (must be denied) 2%.
- Requests are signed exactly like the OpenEMR module signs them, with real authorization-code OAuth tokens.
  Authorization (token introspection plus CareTeam read) runs on every request and every tool call, as in production.
- "Error rate" in the tables is **unexpected outcomes**: anything other than an answer, except the deliberate declines
  and denials. A denial for a physician's own patient counts as an error.

## Results (guard off, retry not yet in play unless stated)

Latency is end-to-end for one `/v1/chat` request, in ms. `first` is time to the first SSE event. CPU is the average
over the run, in percent of one core (so 800 means 8 cores busy). Raw reports are in `loadtest/results/`.

### `stub`: system overhead, no model

| users | requests | p50 | p95 | p99 | req/s | error rate | agent CPU / mem | OpenEMR CPU / mem | MariaDB CPU |
|---|---|---|---|---|---|---|---|---|---|
| 1 (smoke, 4 req) | 4 | 2191 | 2358 | 2358 | 0.4 | 0% | 2% / 47 MiB | 60% / 472 MiB | 9% |
| 10 | 322 | 8169 | 9747 | 9894 | 1.03 | 0% | 13% / 70 MiB | 443% / 758 MiB | 50% |
| 50 | 1466 | 6381 | 25824 | 40233 | 4.45 | **95%** | 28% / 69 MiB | 808% / 2283 MiB | 74% |

At 50 users 1417 of 1466 requests were **denied**, including physicians' own patients. The agent fails closed when it
cannot verify authorization (introspection 3 s timeout, CareTeam read), and OpenEMR was too saturated to answer in
time. Authorization span p50 at 50 users was 5.2-5.8 s against 1.1 s at 10 users.

Per-span p50 / p95 (ms), stub:

| span | 10 users | 50 users |
|---|---|---|
| authz:patient_access | 1091 / 1434 | 5754 / 6613 (959 failed) |
| each FHIR tool (4 per brief) | 558-662 / 778-970 | 2127-3422 / 3621-4192 |
| llm:complete | 0.06 / 0.18 | 0.03 / 0.13 |
| verifier | 0.5 / 1.0 | 0.5 / 2.0 |

### `mock`: simulated model latency (not a provider measurement)

Mock profile (`loadtest/mock_profile.json`): turn 1 about 2.8 s, turn 2 about 7.5 s, taken from the real calibration.

| users | requests | p50 | p95 | p99 | req/s | error rate | agent CPU / mem | OpenEMR CPU / mem |
|---|---|---|---|---|---|---|---|---|
| 10 | 207 | 13440 | 15681 | 15903 | 0.65 | 0% | 5% / 82 MiB | 106% / 566 MiB |
| 50 | 453 | 35947 | 44137 | 46047 | 1.35 | **8.2%** (denials of own patients) | 29% / 108 MiB | 682% / 2069 MiB |

Model latency spreads the load, so OpenEMR coped at 10 users (authorization p50 0.4 s). At 50 users authorization p50
rose to 3.6 s and 45 requests were denied. The 20 s loop deadline is checked between steps, so slow tool calls push the
total to 36-44 s.

### `real`: actual provider, 1 and 10 users only

| run | requests | p50 | p95 | p99 | error rate | OpenEMR CPU | spend |
|---|---|---|---|---|---|---|---|
| calibration, 1 user | 12 | 13683 | 18665 | 18665 | 0% | 10% | 0.341 USD |
| burst, 10 users | 21 | 14484 | 20277 | 21496 | 0% | 102% | 0.512 USD |

By use case (real): UC-1 brief p50 14.0-14.5 s, UC-2 what changed 17.7 s, UC-3 flags 11.6-12.2 s, UC-4 follow-up in the
same conversation 7.4 s. Model turns: p50 3.0 s, p95 8.2-8.7 s. Tools, authorization and verifier together are under
1.2 s of a 14 s request. n is small (33 requests), so the p95 figures are rough.

**Mock fidelity at 10 users:** mock p50 13.4 s / p95 15.7 s against real p50 14.5 s / p95 20.3 s. The mock matches the
median within about 8% and understates the tail, because it has no slow-response outliers. Treat mock p95 as optimistic.

## Failure under load (`stub` unless stated)

| scenario | users | result | alerts after the run |
|---|---|---|---|
| `LLM_PROVIDER=unavailable`, 90 s | 10 | 388 of 402 requests ended `degraded` (chart data only), p50 438 ms, p95 584 ms, 0 `error` outcomes | all clear. Degraded is deliberately not an error, so a total model outage does not page. The outage itself is caught by `/ready` |
| OpenEMR limited to 1 CPU, 120 s | 10 | 84 of 98 requests denied (false denials: authorization timed out), p50 6.1 s, p95 36.7 s | `high_latency_p95` fired. `high_error_rate` did not, because denials are not errors |
| `mock` model answering 429 on 5% of calls, 120 s, **before** the retry fix | 10 | 8 of 88 requests degraded (9.1%) | all clear (degraded is not an error) |

Two findings. An OpenEMR slowdown is invisible to the error-rate alert: physicians get "no access" instead of an
error, so the latency alert is the only page for that case and it stays the primary one. And the 5% model 429s passed
straight through to users as degraded answers, which the retry fix addresses.

## Hardening, gated by these measurements

| item | gate (from the plan) | measured | decision |
|---|---|---|---|
| Shared HTTP client | connection setup or the agent process dominates, or agent CPU saturated | fresh TLS connection 364 ms vs reused 324 ms (about 11%); agent CPU 28% average at 50 users while OpenEMR used 810% | **not done.** Connection setup is not the cost; OpenEMR is |
| Parallel tool calls | tool spans are a large share of p95 | tools are 0.7-1.2 s of a 14 s real request (5-9%); the model is 70-80% | **not done.** It would also multiply load on the saturated component |
| LLM retry on 429/5xx | 429/5xx seen, or mock 429s push user-facing errors over 5% | none in the 33 real requests; mock with 5% injected 429s degraded 9.1% of answers | **done.** One retry, 0.5-1.0 s jittered backoff, counted by `count_retry`. After: 0 of 83 degraded, 9 retries recorded. Read timeouts are not retried (the provider may have processed them) |
| Concurrency guard | latency unbounded or 20 s deadline breached at 50 users | breached: stub 50-user p95 25.8 s, mock 50-user p95 44 s | **built, opt-in, default off** (`MAX_INFLIGHT_CHATS`); see below |
| SQLite WAL and busy timeout | lock errors, or store writes visible in latency | 0 "database is locked" lines; one audit write costs 0.7 ms p50 / 1.1 ms p95 | **not done** |
| Worker count | n/a | stays at one worker: nonce cache, rate limiter and conversation store are per-process memory | documented constraint |

### The concurrency guard: mixed result, so it ships off

With the guard on, requests over the in-flight limit get an immediate `503` with `Retry-After: 5` instead of a slow
false denial. All rows are 50 users, 5 minutes, same data source within a pair.

| source | guard | answers | 503 | false denials of own patients | p95, all requests (503s included) |
|---|---|---|---|---|---|
| stub | off | 36 | 0 | 1417 | 25.8 s |
| stub | 20 | 397 | 4060 | 8 | 14.6 s |
| stub | 10 | 309 | 5649 | 0 | 7.5 s |
| mock | off | 392 | 0 | 45 | 44.1 s |
| mock | 20 | 261 | 4209 | 0 | 20.2 s |
| mock | 30 | 256 | 2669 | 43 | 33.4 s |

(False denials are the "unexpected `denied`" counts in each report; the p95 includes the instant 503s, so it is not a
latency for the requests that ran.)

- Against the **stub**, where OpenEMR is the only thing in the loop, the guard turned 36 answers into 397 and removed
  nearly all false denials.
- Against the **mock**, where slots are held by requests waiting on the model, it lowered answers from 392 to about 260.
  Shedding is not better than queueing when the wait is the model.
- The load generator retries after a 1-3 s think time and ignores `Retry-After`, so the 503 counts are far higher than
  well-behaved clients would produce.
- Because the evidence conflicts, the default is off. Turn it on if more clinicians can reach one agent at once than
  OpenEMR can authorize (about 10-15 on this hardware). The stub 10 and 20 settings gave the best result.

## Where the bottleneck is

1. **OpenEMR authorization and FHIR reads, not the agent and not the model.** The agent used 13% of a core at 10 users
   and 28% at 50. OpenEMR went from 443% to 808% of a core. Past roughly 10-15 concurrent users, OpenEMR's per-call
   OAuth introspection plus CareTeam read (two OpenEMR requests before every tool call) plus the FHIR read itself
   saturate it. Authorization latency climbs from 1.1 s to 5-6 s until it times out, and the agent fails closed.
2. **With a real model, the model is the latency** (70-80% of a request). It also protects OpenEMR by spreading
   arrivals, which is why mock and real look healthy at 10 users.
3. Authorization is per call by design (`agent/AUTHZ_PROOF.md`). No authorization cache was added. Cutting OpenEMR
   load without weakening that rule needs a faster introspection path in OpenEMR, or a read replica.

Not measured: a 50-user run with the real model, a soak test, a second agent worker, a dedicated OpenEMR host. Anything
that depends on those is an estimate.

## The 12 s p95 alert was wrong

Healthy `real` requests have p50 14 s and p95 19-20 s, so the old 12 s threshold fired on normal traffic (it was firing
after every real run). It is now **25 s** (`agent/app/metrics.py`; RUNBOOK, README, ARCHITECTURE and tests updated).
Overloaded runs reached 31-44 s, so 25 s still fires on real trouble. This rests on 33 real requests, so revisit it
once there is production traffic.

## Rerun commands

From the repo root. The load agent is its own container and shares no data with the demo agent.

```bash
# stub model (default)
docker compose -p agent-load -f loadtest/docker-compose.yml up -d --build
agent/.venv/bin/python -m loadtest.run --source stub --users 10 --duration 300 --name stub-10u
agent/.venv/bin/python -m loadtest.run --source stub --users 50 --duration 300 --name stub-50u

# mock model: start the mock (add --error-rate 0.05 to inject 429s), then point the load agent at it
agent/.venv/bin/python -m loadtest.mock_openrouter &
LOAD_PROVIDER=openrouter docker compose -p agent-load -f loadtest/docker-compose.yml \
  -f loadtest/docker-compose.mock.yml up -d --force-recreate
agent/.venv/bin/python -m loadtest.run --source mock --users 50 --duration 300 --name mock-50u

# real model: the key reaches the container through compose env_file; the cap is enforced by the harness
LOAD_PROVIDER=openrouter docker compose -p agent-load -f loadtest/docker-compose.yml up -d --force-recreate
LOADTEST_MAX_USD=0.60 agent/.venv/bin/python -m loadtest.run --source real --calibrate --users 1 --requests 10
LOADTEST_MAX_USD=1.50 agent/.venv/bin/python -m loadtest.run --source real --users 10 --requests 20

# optional: guard on / OpenEMR slowed
LOAD_MAX_INFLIGHT=20 docker compose -p agent-load -f loadtest/docker-compose.yml up -d --force-recreate
docker update --cpus=1 development-easy-light-openemr-1     # restore afterwards with: --cpus 10
```

Run `loadtest/wait_idle.sh` between runs: OpenEMR keeps working through a backlog for a minute or two after a 50-user
run, and a run started too early measures the previous one. The final reports in `loadtest/results/` are committed.
Runs that were discarded as invalid (see below) were kept locally in `loadtest/results/superseded/`, which is git-ignored.

## Invalid runs and what they taught

- **First stub 10 and 50 user runs** counted denials as success. The harness now counts a denial of the caller's own
  patient as an error. Re-run with the corrected accounting.
- **A mock run where the mock server had died** produced 95% `degraded` in 300 ms. Discarded, mock restarted, re-run.
- **A 50-user run started while OpenEMR was still draining the previous run** crashed the OAuth step. The harness now
  retries token fetches and `wait_idle.sh` exists.
- Two reports of the same `mock-10u` run overlapped in time. The overlap was caught by identical tool p95 values, both
  were discarded and one clean run was kept.
