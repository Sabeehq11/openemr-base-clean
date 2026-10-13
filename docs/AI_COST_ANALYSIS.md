# AI cost analysis (Checkpoint 3)

Everything labelled **measured** comes from the `real` load runs (OpenRouter, `anthropic/claude-sonnet-4.5`, 33
requests). Everything labelled **assumed** or **estimated** is not a measurement. Stub and mock runs never feed a cost
number: the stub reports zero cost and the mock reports zero cost by construction.

## Actual spend in development

| item | amount | how it was obtained |
|---|---|---|
| Load-test calibration (12 requests, 1 user) | 0.3406 USD | `cost_usd` summed from the load agent's trace store |
| Load-test burst (21 requests, 10 users) | 0.5128 USD | same (cumulative store total 0.8534 minus calibration 0.3406) |
| **Checkpoint 3 total, harness side** | **0.8534 USD** | dashboard total for the load agent; ceiling was 2.00 USD |
| Checkpoint 2 live eval run (6 cases) | 0.0537 USD | from `evals/results/live-baseline-2026-10-12.md` (preserved unchanged); not counted against the 2.00 USD ceiling |
| PRD compliance live evals (4 cases, 2 with a model call), kept execution | 0.0415 USD | `cost_usd` from that run's own trace stores: UC-2 case 0.0103, UC-4 case 0.0312, two no-model-call cases 0 |
| Same 4 cases, accidental duplicate execution | 0.0415 USD | two live processes were started at once by mistake; the second one's results were discarded, but its model calls were real and are billed. See below |
| Earlier development spend (chats, manual tests, demo agent) | **not recorded** | no trace store or ledger survives for it; it cannot be reconstructed from the repo |

**Ledger total from trace stores: 0.9901 USD** (0.8534 + 0.0537 + 0.0415 + 0.0415). `evals/results/live-latest.md`
shows 0.1368 USD for the two live-eval runs (0.0537 + 0.0831), which is the same 0.0537 + 0.0415 + 0.0415 as above.
The 0.8534 USD load-test figure and the earlier-development row are separate, and the latter is unknown, so this is a
floor for what the key has spent, not a total.

**What happened with the duplicate run.** The new live cases were meant to run once, but two processes ran in parallel (the two
identical sets of trace stores and the refused merge show it; how the second one started was not established). Each saw the other's trace stores as its own spend, which is why the
first report said 0.0518 USD. The second process failed at the merge step because the case ids were already recorded, so
`live-latest.json` holds one set of results. The correct figures were recomputed from the four trace stores that
carry a cost (0.01032, 0.01032, 0.031206, 0.031206 USD). The runner now takes a lock file so a second live run refuses to
start. The 0.40 USD cap applied per process and was never close (about 0.04 USD each), but the combined spend of a run
should not depend on luck, hence the lock. Total new real-model spend in this task: 0.0831 USD, well under the 1 USD limit.

**Comparison with the OpenRouter account: not a clean check.** The harness total was read back from the load agent's
trace store after the runs: 32 requests with a cost, summing to 0.8534 USD, which matches the table above. The account
figure for the same key (read through the key-usage endpoint) is much larger, because the key is shared with all earlier
development work and with the demo agent, so it cannot confirm or refute 0.8534 USD. The account total also rose by
about 0.2 USD within minutes of the final check, from traffic that was not attributable to the load tests (the demo
agent's last recorded chat was hours earlier), so that rise is unexplained here. The `cost` values in the traces come from
OpenRouter's own `usage.cost` field in each response, so a large gap would be surprising, but it is not independently
verified. To close it: open the OpenRouter activity page, filter to 12 Oct 2026, and sum the requests made through the
load agent's time windows (`loadtest/results/real-*.json`). Using a dedicated key with its own limit for load runs would
make this a one-line check and a hard cap. Spend before Checkpoint 3 (development chats, earlier manual tests) is not itemised here.

The spend cap worked as designed: the harness refuses `--source real` without `LOADTEST_MAX_USD`, never accepts more than
2.00, reads the target's own cost total before each scenario and stops when it is reached. It was not reached
(0.60 USD was set for calibration and 1.50 USD for the burst).

## Per-request cost (measured, real model only)

Price check: Sonnet 4.5 list price is 3 USD per million input tokens and 15 USD per million output tokens. A brief with
3734 input and 896 output tokens gives 0.0112 + 0.0134 = 0.0246 USD, which matches the recorded figure.

| request shape | n | model turns | tool calls | input tokens | output tokens | cost per request (mean) | range |
|---|---|---|---|---|---|---|---|
| UC-1 brief and UC-3 flags (4-5 core tools) | 21 | 2 | 5 | 3734 | 896 | **0.0246 USD** | 0.0194 - 0.0302 |
| UC-2 what changed (adds `get_changes_since`) | 8 | 4 | 6 | 8595 | 678 | **0.0359 USD** | 0.0255 - 0.0442 |
| UC-4 follow-up, same conversation | 3 | 2 | 1 | 2830 | 511 | **0.0162 USD** | 0.0161 - 0.0162 |
| declined advice request | 1 | 0 | 0 | 0 | 0 | 0 | the decision is made before any model call |

Caveats: UC-1 and UC-3 are pooled because the traces do not record the use case, only tool names and turn counts. The
follow-up row has n = 3. UC-2 costs more because the model took 4 turns (it re-reads context after each tool batch).
Input tokens dominate the count but output tokens dominate the cost (about 55% of a brief).

## Projection at 100, 1K, 10K and 100K users

A "user" is a physician. The model-token figures come from the measured table above. **Everything else here is assumed**,
and the load-test mix in `PERFORMANCE.md` is a different thing (it was built to stress the system, not to describe a
clinic day).

Assumptions (change them in one place and the table scales linearly):

| assumption | value | basis |
|---|---|---|
| patients briefed per physician per working day | 20 | assumed clinic panel |
| working days per month | 22 | assumed |
| follow-up questions per brief (UC-4) | 0.5 | assumed |
| "what changed" or flag lookups per brief (UC-2, UC-3) | 0.2 | assumed |
| cost per brief / follow-up / what-changed | 0.0246 / 0.0162 / 0.0359 USD | measured |
| model price | unchanged from today (3 / 15 USD per million tokens) | list price |
| cache hits from the 5 minute snapshot | not counted | the snapshot only saves tool calls inside one conversation, not model tokens; a physician rarely re-asks about the same patient within 5 minutes |

Cost per patient visit: 0.0246 + 0.5 x 0.0162 + 0.2 x 0.0359 = **0.040 USD**. Per physician day 0.80 USD, per
physician month **17.55 USD**.

| users | requests per month (assumed) | average requests per second in an 8 h day | model cost per month, as built | with the optimisations below (estimated) |
|---|---|---|---|---|
| 100 | 75 thousand | 0.12 (peak about 0.4) | **1,755 USD** | about 1,020 USD |
| 1,000 | 748 thousand | 1.2 (peak about 3.5) | **17,500 USD** | about 10,200 USD |
| 10,000 | 7.5 million | 12 (peak about 35) | **175,000 USD** | about 102,000 USD |
| 100,000 | 74.8 million | 118 (peak about 350) | **1.75 million USD** | about 1.02 million USD |

"Peak" assumes three times the average. The optimised column assumes (all estimates, not measured): prompt caching of
the static system prompt and tool specs saves about 22% (assumes about 2,000 static input tokens read at a 90% discount;
the real size of the static prefix was not measured) and routing follow-ups and lookups to a model at about one third of
the price. Treat it as a direction, not a quote.

At 100 users this is a small line item (about 17.55 USD per physician per month). The number that matters is the
cost per clinical decision supported, and that is roughly four cents.

## What changes in the architecture at each tier

The token bill is not the only thing that grows, and the load tests say it is not the first thing to break.

| tier | what breaks first | what to change |
|---|---|---|
| **100 users** | nothing, if OpenEMR is sized for it. Peak 0.4 requests/s is below the 10-15 concurrent users where OpenEMR started to fail closed (`PERFORMANCE.md`) | single agent container as built. Turn on prompt caching. Add a per-user daily spend limit so a runaway client cannot spend the budget |
| **1,000 users** | OpenEMR authorization (two OpenEMR calls before every tool call) and the single process. Peak about 3.5 req/s with 14 s requests is about 50 requests in flight, past where the stub 50-user run denied 97% of requests | several agent workers, which means the in-memory nonce cache, rate limiter and conversation store move to Redis. Traces and audit move from SQLite to Postgres (one writer per process is already a limit). A dedicated OpenEMR app tier. Pre-compute briefs before clinic, which also removes the 14 s wait |
| **10,000 users** | the FHIR and OAuth load on OpenEMR, then provider rate limits | a request queue with back-pressure, so overload becomes a wait and not a false denial. Model routing: Sonnet for briefs, a cheaper model for lookups and follow-ups. Batch pre-computation overnight. A short-lived, signed authorization decision cache **only if** the security review accepts the revocation delay; today the rule is a fresh decision on every call |
| **100,000 users** | cost, and OpenEMR as a shared database | a dedicated FHIR read replica, hosted tracing (for example Langfuse) instead of one SQLite file, an archive and rotation plan for the audit chain, and a negotiated model contract. The estimated model bill is about 1 million USD a month even after the optimisations above |

Growth that is easy to miss: the audit chain grows by several rows per request (about 75 million requests a month at the
top tier) and must stay verifiable, so it needs checkpointing and segmenting well before then. The dashboard reads the
trace store directly, which stops being viable far earlier than the model bill does.

## What was not measured

- OpenRouter's own account total (see above).
- The size of the static prompt prefix, and therefore the real caching saving.
- Any 50-user real-model run. Concurrency numbers at that level are `stub` and `mock` only.
- A real clinic's use pattern. Every per-physician number depends on the assumed 20 patients and 0.7 extra questions
  per patient.
- Cost of failures: a degraded answer costs the model turns it used before failing, and the retry fix adds a second
  attempt on 429 and 5xx. Neither effect appeared in 33 real requests, so it is not in the table.
