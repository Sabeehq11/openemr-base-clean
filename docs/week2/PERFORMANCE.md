# Week 2 performance and cost

Harness: `scripts/w2/measure.py`. Every number below is labelled **stub** or **real**. Stub numbers come from the real
routers, supervisor, ingestor and retriever running in process with a canned VLM, the offline embedder and reranker, a
scripted chat model and mocked FHIR. They measure plumbing overhead. They say nothing about model latency or cost.

## Status

| Part | State |
|---|---|
| Harness, stub tier | Done, tested (`agent/tests/test_w2_measure_unit.py`) |
| Week 1 shared path with W2 off and on | Done (stub) |
| Real-model traces | Recorded on the isolated agent (`tmp/agent-real-data/traces.db`). Not a 10-by-10 harness run. See below. |

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

## Real model (isolated agent, 2026-10-16)

Source: `tmp/agent-real-data/traces.db` on `agent-real` (`127.0.0.1:8402`). The live agent (`agent-agent-1`, started
2026-10-16 03:03:43Z) was not recreated and was not called. Model: `anthropic/claude-sonnet-4.5`. Every p95 below with
n under 20 is the near-maximum, not a stable tail.

| Flow | n | p50 ms | p95 ms | SLO | Result |
|---|---|---|---|---|---|
| W2 chat, outcome `answer` | 9 | 19 771 | 24 647 | none set | n/a. One extra chat was a denial at 298 ms and $0; it is not in this row |
| Retrieval span `retrieve` | 11 | 867 | 2 551 | p95 under 3 000 | met for this sample. Close to the line |
| Ingestion, outcome `extracted` | 1 | 10 705 | not estimated | p95 under 30 000 | that one sample is under the line. A p95 needs more than one extraction |
| Ingestion, outcome `duplicate` | 2 | 314 | 325 | none | no VLM call, $0 |

Per step, real (p50 / p95 ms): `chart_facts` 14 219 / 18 389 (the Week 1 tool loop plus Sonnet), `w2_synthesis`
4 743 / 5 221, `vlm_extract` 10 353 (n=1, 97% of that upload), `rerank` 370 / 1 599, `embed` 0.39 / 2 165.
Six of eleven embeds were a cache hit (at most 1 ms). The five misses were the network embedder.

Bottleneck: a Week 2 chat is the chart loop, not retrieval. `chart_facts` is about 70% of the 20 s chat. Retrieval is
second, and only when the embed cache is cold. For the one real extraction, the VLM call is the ingestion bottleneck.

### CPU, memory, throughput

| Measurement | What ran | CPU | Memory | Throughput |
|---|---|---|---|---|
| Isolated container | `agent-real`, 20 s of `GET /ready` (no model call), 3 `docker stats` samples | 0.3% average, 0.5% max, of one core | 78.2 MiB | 1.19 ready checks/s. Each check takes about 440 ms because it probes OpenEMR |
| Stub, in process | 30 uploads + 30 chats, canned VLM, one core, sequential | 98% of one core for that 0.6 s | 118 MiB RSS | 97 calls/s until the test client's rate limit stopped it |

CPU and memory during a real VLM or chat call inside the container were not sampled. A model call is mostly waiting on
the network, so the idle container numbers are the resident size, not the cost of a call. Concurrent real-model
throughput was not measured: one answered chat takes about 20 s, so one worker does about three a minute, and that is
an arithmetic consequence of the latency, not a load test.

### Spend

| Item | USD | Basis |
|---|---|---|
| Nine answered chats in `traces.db` | 0.4485 | OpenRouter `usage.cost` recorded by the agent. Mean 0.0498 per chat (range 0.0368-0.0644) |
| One lab extraction in `traces.db` | 0.0174 | same. 1 717 prompt tokens, 818 completion tokens, 10.7 s |
| Two direct VLM calls this session (lab and intake fixtures, short prompt, no agent, no OpenEMR write) | 0.0195 | provider `usage.cost` on the HTTP response: lab 0.0106 at 4.7 s, intake 0.0088 at 3.8 s. Not the production extraction prompt, so not an ingestion latency |
| **Recorded this evidence set** | **0.4854** | 0.4659 in the isolated trace store plus 0.0195 direct |
| Embedding and rerank | not in `traces.db` | five embed misses and eleven reranks happened. Their provider cost is untracked. At list price an embed of a short query is negligible; a Cohere rerank is billed per search and is the untracked line. Not confirmed against a statement |
| Provider account total | unverified | the key is shared with Week 1 and the demo agent, so an account total would not confirm this table |

Week 1 comparison, same model, real, from `docs/week1/PERFORMANCE.md` and `docs/week1/AI_COST_ANALYSIS.md`: a brief was
p50 about 14.5 s and p95 about 20.3 s at 10 users, and 0.0246 USD. A Week 2 chat is about 5 s slower at the median and
about twice the price, because it keeps that chart loop and adds retrieval plus a synthesis call.

Projection, assumed, not measured. Using the Week 1 clinic-day assumptions (20 briefs a physician a day, 22 days) and
the measured 0.0498 USD chat: about 21.90 USD per physician per month if every brief is a Week 2 chat, against about
10.80 USD for the Week 1 brief. Two new documents a day at the one measured 0.0174 USD adds about 0.77 USD. The
untracked rerank is extra and is not in either figure. Supervisor caps were not changed; the samples do not breach the
40 s cap or the 30 s ingestion SLO. Retrieval p95 at 2 551 ms is inside 3 000 ms but has no slack for a slower tail.
