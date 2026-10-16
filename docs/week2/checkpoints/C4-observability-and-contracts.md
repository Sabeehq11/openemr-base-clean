# C4. Observability, contracts, ops

Status: **built and verified (stub tier)**. Everything here is additive and tested with stubs and mock transports only, so there are no paid calls. Week 1 behavior with `W2_ENABLED` off is unchanged.

## Goal
Make the Week 2 flow observable and its interfaces contract-checked, extending Week 1 conventions rather than adding parallel ones.

## What was built
| Piece | File |
|---|---|
| Span ids, parent ids, counts-only `attrs`, trace `flow`; idempotent migration | `agent/app/tracing.py` |
| `w2/supervisor` root span; workers, `embed`, `rerank`, `retrieve`, `ingest`, `vlm_extract` as child spans | `app/w2/supervisor.py`, `rag/retriever.py`, `ingest.py` |
| Traces for `/preflight`, `/documents`, `/retrieve`; `w2_ingest_start`, `w2_ingest_complete`, `w2_extract` (field counts, average confidence), `w2_retrieve` hit or miss | `app/w2/api.py`, `ingest.py`, `rag/retriever.py` |
| VLM token usage and cost into the trace (stub reports zero) | `app/w2/vlm.py` |
| OpenEMR write and link log line (`w2_openemr_write` for the `openemr_write` and `link` steps: correlation id, step, status, row count) | `oe-module-copilot/src/UploadProxy.php` |
| One place for timeouts and retries; one jittered embedder retry | `app/w2/limits.py`, `rag/embed.py` |
| `/ready` as ready, degraded or not_ready | `app/w2/ready.py`, `app/main.py` |
| `w2_summary()`, three alerts, SLO constants, dashboard section, `w2` key in `dashboard.json` | `app/metrics.py`, `app/dashboard.py`, `app/cli.py` |
| Runbook entries for each alert and for a degraded `/ready` | `agent/RUNBOOK.md` |
| OpenAPI 3 spec and contract test | `docs/week2/openapi.yaml`, `agent/tests/test_w2_openapi_unit.py` |
| End-to-end PHI scan (upload, retrieve, chat, errors) over logs, `traces.db`, audit sink and eval results | `agent/tests/test_w2_phi_scan_unit.py` |
| Bruno requests and README | `docs/api-collection/copilot/` (`ready.bru`, `w2-ready.bru`, `w2-dashboard.bru`) |
| Tracing, metrics and retry tests | `test_w2_tracing_unit.py`, `test_w2_metrics_unit.py`, `test_w2_rag_unit.py` |

## Decisions worth knowing
- **`/ready` never probes a provider.** The reranker, embedder and VLM checks read configuration and the last real call recorded in memory. "No traffic yet" is reported as ok with a detail. A Week 1 failure is `not_ready` (503); a Week 2-only failure is `degraded` (200), because sparse-only retrieval and the lexical reranker still work.
- **Trace rebuild by correlation id.** A trace is stored with its correlation id; spans carry `span_id` and `parent_span_id`. Old rows keep `NULL` in the new columns and Week 1 readers are unaffected.
- **Counts only in `attrs`.** The attributes column holds numbers (hits, field counts, route counts), never text, so it cannot carry PHI.
- **Retry scope.** At most one retry, on 429, 5xx or a connect failure, with jitter. A read timeout is not retried, so the worst case stays inside the 20 s embedder budget plus one short retry.
- **OpenAPI scope.** Only `/v1/w2/*`, `/health` and `/ready`. Week 1 routes are not in the spec and are ignored by the test.
- **A Week 1 logging side effect.** The PHI scan caught httpx's INFO request lines, which included `?patient=<uuid>` from FHIR calls, and uvicorn's access log has the same problem for `GET /v1/w2/documents/{id}?patient_uuid=<uuid>`. `httpx`, `httpcore` and `uvicorn.access` are now held at WARNING, and the image starts uvicorn with `--no-access-log`. This removes log lines only.
- **Eval regression is enforced in CI, not in the deployed service.** The alert and the dashboard panel read `evals/results/w2-golden-latest.json` and `evals/w2/baseline.json`. The agent image copies only `agent/`, so deployed there is nothing to compare: the dashboard shows `n/a (CI)` and the alert cannot fire. `w2-eval-gate.yml` is what blocks a regression. The alert is useful on a source checkout or dev stack. No eval files are copied into the image on purpose.
- **Week 1 numbers are chat-only.** `summary()` and the three Week 1 alerts filter to `flow` NULL, `chat` or `w2_chat`. Upload, retrieve and preflight traces are counted only in the Week 2 section, so a normal 28 s ingestion cannot fire `high_latency_p95`.
- **PyYAML** is added as one pinned dependency (`6.0.3`); the dependency audit reports no new vulnerabilities.

## Alerts and SLOs
| Alert | Fires when | Minimum samples |
|---|---|---|
| `w2_extraction_failure_rate` | failed share of extractions above 20 % in 10 min | 5 |
| `w2_retrieval_latency_p95` | retrieval p95 above 3 s in 5 min | 5 |
| `w2_eval_regression` | a rubric is more than 5 points under the baseline in the latest golden result | needs the eval files (source checkout only) |

Initial SLOs: ingestion p95 under 30 s, retrieval p95 under 3 s. Both are measured against real models in C5; the values here are targets, not measurements.

## Acceptance
- A full multi-agent trace can be rebuilt from the correlation id alone: met (the gateway, spans, log events and the PHP write line all carry it).
- The dashboard shows health without reading logs: met for Week 2 (ingestion, extraction pass rate, retrieval, routing, per-worker p95, alerts).
- Contract tests pass; the PHI check passes on logs, traces and eval outputs: met, stub tier.

## Verification
Run on 2026-10-15 with `OPENROUTER_API_KEY` blank and the stub LLM and VLM, so no paid calls were possible.

### Automated
| Check | Result |
|---|---|
| Full agent suite, `pytest tests -m "not live"`, run once with `W2_ENABLED=true` | **828 passed, 1 skipped**, 30 live tests deselected |
| Eval gate, `python -m evals.gate`, `W2_ENABLED=true` and `false` | **PASS** both times (50/50 golden cases, no regression against the baseline, stub suite 67/67) |
| Isolated PHP, `phpunit -c phpunit-isolated.xml --filter UploadProxy`, in the OpenEMR container | **OK, 15 tests, 88 assertions** (includes the `openemr_write` then `link` log order, correlation id, counts-only fields, and a failed link logged as `failed`) |
| Focused C4 Python tests (metrics, OpenAPI, PHI scan, tracing, dashboard, correlation, supervisor, chat) | 115 passed |

The suite was run once, at `W2_ENABLED=true`; the 522 passed, 30 skipped figure in `BASELINE.md` is the Week 1 count at `w1-baseline` and is not comparable to this total. The gate rewrites `evals/results/{gate-latest.md,w2-golden-latest.*}` with a new timestamp and timings; those files were restored with `git checkout` so the working tree carries no eval-result churn.

### Manual, on the local stack (stub LLM and VLM; all read-only inspection of `traces.db`, logs and `dashboard.json`)
| # | Scenario | Result | Evidence |
|---|---|---|---|
| 1 | Duplicate upload (correlation id `aa7118ede53a9817`) | Pass | Trace `w2_ingest` outcome `duplicate`; no `vlm_extract` span, as the duplicate path never calls the VLM. PHP lines `openemr_write` (rows 0) and `link` (rows 5) carry the same id. No chart values in agent or PHP logs. Week 1 summary unchanged (0 requests). |
| 2 | Non-duplicate upload (`3ec43ee0ab4b5077`, new document, hash changed by a trailing comment) | Pass | `ingest` root span with `vlm_extract` as its child; `w2_extract` event with counts only (5 of 5 fields, confidence 0.93); `openemr_write` rows 5 then `link` ok in the PHP log; Week 2 dashboard shows the ingestion; Week 1 summary still 0 requests, three Week 1 alerts not firing; 19 stored lab strings found 0 times in logs. |
| 3 | Chat request (`823744934eb8fc0f`, trace `w2_chat`) | Pass | `supervisor` root with `intake_extractor`, `chart_facts`, `evidence_retriever` as children; `retrieve` under `evidence_retriever`, with `embed` and `rerank` under it; 7 handoffs and routing counts match the dashboard; 40 log lines and the audit rows share the id; Week 1 counts the chat exactly once (requests 1, tools 4); 34 stored strings found 0 times. |
| 4 | Readiness (see the note below) | Pass | A Week 2 failure gave HTTP 200 `degraded` with all four Week 1 checks ok and `reranker` failed; the live agent stayed HTTP 200 `ready` before, during and after. |

Notes from the manual runs, none of them blockers:
- The degraded state was produced on a throwaway second agent process (a copy of the data directory, `OPENROUTER_API_KEY` blank, stub VLM off), not by editing the live `.env` or `w2.db`. It exercised the reranker check. A missing `w2.db` and an empty guideline index are covered by unit tests, not by the manual run.
- Duplicate uploads count in the Week 2 ingest p95 and field pass rate, so re-uploads can skew those two figures.
- `/v1/w2/*` requests that fail authentication still leave an `error` trace. They are excluded from Week 1 and from the Week 2 alerts.
- The two `w2_synthesis` LLM spans are roots, because synthesis runs after the supervisor span closes; a tree rebuild must expect three roots.
- Chat requests are correlated in the agent logs and the audit chain only. C4 added PHP logging for the upload path, not chat.
- Stub timings say nothing about the 30 s and 3 s SLOs; C5 measures them with real models.

## Not done here
A critic agent, a hosted metrics stack, a load test, deploy work and real-model latency numbers (C5). Nothing is committed or pushed.

## Depends on
C3.
