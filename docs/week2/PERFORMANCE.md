# Week 2 performance and cost

Harness: `scripts/w2/measure.py`. Every number below is labelled **stub** or **real**. Stub numbers come from the real
routers, supervisor, ingestor and retriever running in process with a canned VLM, the offline embedder and reranker, a
scripted chat model and mocked FHIR. They measure plumbing overhead. They say nothing about model latency or cost.

## Status

| Part | State |
|---|---|
| Harness, stub tier | Done, tested (`agent/tests/test_w2_measure_unit.py`) |
| Week 1 shared path with W2 off and on | Done (stub) |
| Real-model run (10 uploads, 10 chats) | **Pending approval. Not run. No paid call has been made.** |

## How the real run is gated

`--tier real` is blocked in code until both are present:

1. `--approved-budget uploads=N,chats=M,usd=X`, with N and M at most 10 and X at most 5.00.
2. `W2_MEASURE_APPROVED=approve-<N>u-<M>c-<X>usd`, a token bound to those exact numbers.

**Two different dollar figures.** `$5.00` is a hard ceiling compiled into the script: no approval can ask for more.
It is not the plan. The proposed budget is **$1.50** (10 uploads, 10 chats, token `approve-10u-10c-1.50usd`), and the
run enforces whatever `usd=` was approved, so with that token the cap in force is $1.50. The ceiling only limits what a
future approval could request. The upload and chat counts are separate caps (10 each) and bind regardless of dollars.

**The $1.50 is an internal estimated budget, reserved before each call, not a billing ceiling.** A call is allowed only
when its reserve still fits: $0.15 per upload and $0.10 per chat. No provider-side spending limit is assumed, so actual
charges may exceed the reservations. The budget splits by worst-case need into $0.90 for uploads and $0.60 for chats,
which funds 6 uploads and 6 chats on a reserve basis. The approved 10 and 10 are call-count ceilings, not a promise that
all 20 run. An unused share does not roll over, so uploads cannot spend the chat share.

An unknown cost stops the run. A missing trace row, a successful call whose provider response has no `usage.cost` (or
$0), or a duplicate upload is charged at that kind's reserve and the run stops. A call whose recorded cost exceeds its
reserve also stops the run. Embedding and rerank spend is never written to `traces.db`, so `agent_recorded_usd`
understates the charge. `provider_verified_usd` stays null unless the operator supplies it afterward from the provider's
own statement.

The script prints the shares, the reserves and how many calls they fund before it builds any client, checks approval
first, stops at the first provider error, and never retries. These guards are unit tested with a fake provider. The real
run targets a separate agent (own `DATA_DIR`), not the live one.

## Stub tier (free)

30 uploads (15 lab, 15 intake) and 30 chats, in process, `python scripts/w2/measure.py --uploads 30 --chats 30`.
Each upload has distinct bytes, so none is a duplicate.

| Measure | n | p50 ms | p95 ms | SLO | Result |
|---|---|---|---|---|---|
| Upload to stored extraction | 30 | 19.2 | 22.6 | p95 under 30 000 | met (stub, not meaningful) |
| Retrieval (evidence worker) | 30 | 0.57 | 0.90 | p95 under 3 000 | met (stub, not meaningful) |
| Full W2 chat (supervisor, workers, synthesis) | 30 | 4.75 | 5.33 | none set | n/a |

Per step (p50 / p95 ms, stub): ingest 17.4 / 19.7 (page render dominates; `vlm_extract` is 0.01 because the VLM is
canned), supervisor 3.1 / 3.6 (includes the child workers), chart_facts 1.0 / 1.35, intake_extractor 1.3 / 1.6,
evidence_retriever 0.57 / 0.90 (embed 0.04, rerank 0.18), synthesis about 0, verify 0.01.

Reading: with the model removed, the Week 2 code adds about 20 ms per upload and about 5 ms per chat. Nothing in our code
is near either SLO. Whether the SLOs hold depends on the VLM call, which is why the real run exists.

## Week 1 shared path, W2 off versus on (stub)

`POST /v1/chat` through the real app, same mocks, interleaved requests, 60 per side, 0 failures.

| W2_ENABLED | p50 ms | p95 ms |
|---|---|---|
| off | 4.94 | 5.85 |
| on | 4.93 | 5.66 |

p50 difference: -0.2 percent, inside noise. Turning Week 2 on does not slow the shared Week 1 path. (An earlier run of
this check showed 2 failures per 60 because the per-user rate limit of 60 a minute applies to the harness; the
comparison now disables the limit on both sides, and says so in the code.)

## Real-model run: pending approval

To be filled in only after the budget owner approves in chat. Planned: 10 uploads (5 lab, 5 intake) and 10 chats from
committed synthetic fixtures only, one run. Expected, to be confirmed or refuted by the data:

- The VLM call is the bottleneck for ingestion (the VLM timeout of 30 s is also the ceiling for the ingestion SLO).
- Retrieval is dominated by the rerank call over the network.
- Cost per document and per chat, read from `traces.db` (`cost_usd`, tokens), then projected to production volume.

What the report will contain: per-step p50 and p95 from spans, ingestion p95 and retrieval p95 against the SLOs, actual
dev spend, projected cost per document and per chat, and the bottleneck.

Honest limit for that run: with about 10 samples, p95 is the near-maximum, not a stable tail estimate. The report will
say so beside every p95. Supervisor caps (6 steps, 40 s) and the SLO constants in `app/metrics.py` change only if that
data requires it.

| Real-model measure | Value |
|---|---|
| Ingestion p50 / p95 | pending |
| Retrieval p50 / p95 | pending |
| Chat p50 / p95 | pending |
| Spend for the run | pending |
| Cost per document / per chat | pending |
