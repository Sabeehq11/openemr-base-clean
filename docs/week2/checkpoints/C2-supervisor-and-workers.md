# C2. Supervisor and two workers

Status: **built**, behind `W2_ENABLED`. `POST /v1/w2/chat` is new; `/v1/chat`, `loop.py`, `verifier.py` and `prompts.py` are not edited. The module reaches the new route only with `w2_chat` set in its local config (default off).

## Goal
One supervisor and two workers (intake-extractor, evidence-retriever) with explicit, logged handoffs, wrapping the Week 1 `AgentLoop` rather than rewriting it.

## Decisions
- **Stored facts only.** The intake-extractor reads supported facts already in `w2.db`. It never extracts at chat time, and no file bytes reach the agent. `unsupported` rows are counted and never used. Demographics never reach the model.
- **Two-stage synthesis.** Stage A is the unchanged `AgentLoop` plus `Verifier` (chart claims). Stage B is one model call with no tools over two labelled blocks, `[PATIENT DOCUMENT FACTS]` and `[GUIDELINE EVIDENCE]`, followed by a W2 verifier.
- **Chart claims are frozen.** Stage A's verified answer (claims, rule-based flags, notes, source cards) is the `chart` group of the final answer, passed through without a copy or edit. Stage B never receives it, cannot cite a chart id (such claims are dropped as `unknown_source`), and a Stage B failure leaves it intact.

## What was built
| Piece | File |
|---|---|
| `Handoff`, `WorkerResult`, `GraphState`, `Node`, enums | `agent/contracts/w2/graph.py` |
| `W2Draft`, `W2Claim`, `W2VerifiedAnswer` | `agent/contracts/w2/answer.py` |
| `next_node` routing and `Supervisor` runner | `agent/app/w2/supervisor.py` |
| Intake-extractor and evidence-retriever workers | `agent/app/w2/workers.py`, `facts.py` |
| Stage A adapter (Week 1 loop as a node) | `agent/app/w2/stage_a.py` |
| Stage B synthesis, W2 verifier, assembly | `synthesis.py`, `verify.py`, `assemble.py` |
| `POST /v1/w2/chat` | `agent/app/w2/chat.py`, mounted in `wiring.py` |
| Module routing (`w2_chat` config, default off) | `ChatProxy.php`, `ModuleConfig.php`, `public/chat.php` |

Seams outside `app/w2/`: `main.py` passes the runner, model factory, verifier, conversations, traces and load gauge into `build_w2` at the existing `if cfg.w2_enabled` block. `W2Store.list_document_ids` is a new read-only query.

## Routing
Evaluated on every step, first match wins: documents not read -> intake-extractor; chart not fetched -> chart (Week 1 loop); evidence wanted and not retrieved -> evidence-retriever; otherwise synthesis. Chart runs before retrieval so a denied or declined request stops before any retrieval or second model call. Caps: 6 steps, 40 s total, 30 s per node (the supervisor's own default is 15 s; `chat.py` passes 30 s so the Week 1 loop's 20 s deadline fits inside it). A failed or timed-out worker is recorded once and never retried. A `denied`, `declined` or `llm_unavailable` result ends the run.

## Observability
Each handoff is one `w2_handoff` log event (from, to, reason, step, correlation id; no values or text) and each worker run a `w2_worker_result` event with status, counts and latency. The correlation id alone rebuilds the route. Each node runs in a `tracing.span("w2", node)`. The request trace records the outcome. The audit event `w2_chat_finished` carries ids and counts only.

## Verifier rules for Stage B
Each claim must cite at least one id that was offered in its request. It cannot mix a document id with a guideline id. Every number and date in the claim must appear in the cited value. Week 1 `violates_output_rules` still applies. Chart ids and invented ids are rejected. Reason codes are counted, never claim text: `uncited`, `unknown_source`, `mixed_groups`, `number_mismatch`, `date_mismatch`, `output_rules`.

## Degraded paths (all return, none hang)
| Case | Result |
|---|---|
| No documents on file | answer with a note; no document claims |
| Document extraction failed | note; the failed document's values are not shown |
| Empty retrieval | note "No guideline evidence was found" |
| Stage B model down or unparseable | document facts and snippets shown as recorded with citations, `unsynthesised: true` |
| Question names a report kind that is never stored (genetic, pathology, imaging, ...) | no document facts are read; note "No <kind> report is on file for this patient. Only lab reports and intake forms can be uploaded." A question that also names a lab or intake topic still reads the documents |
| Chart step failed | answer with a note that chart data is missing |
| Model down for Stage A | `degraded` event with raw chart records, as in Week 1 |
| Step or time cap | answer with a note naming the cap |
| Not authorized | `denied` before any stored fact is read |

## Logging and PHI
Caught exceptions are logged as codes, never as text or tracebacks. `app/w2/safelog.py::error_code` returns a W2 error type's message only when it is a single identifier (`schema_invalid`, `http_503`, a class name), and the class name for anything else. Applied to the VLM, extraction, render and rerank logs. The supervisor's worker-raised path and the answer-assembly failure now log `w2_worker_raised` / `w2_answer_failed` with `error_code` set to the exception class, replacing `logger.exception`. The worker result keeps its `worker_error` code. Covered by `test_w2_safelog_unit.py`.

## Tests
Files: `test_w2_graph_contracts_unit.py`, `test_w2_supervisor_unit.py`, `test_w2_workers_unit.py`, `test_w2_synthesis_unit.py`, `test_w2_chat_unit.py` (stubbed model, offline retriever, real gateway, authorization, store and verifier), `test_w2_safelog_unit.py`, a PHP isolated test for path selection, and six stub evals in `evals/cases_w2.py`.

Results (offline, no paid calls):
- Agent suite: **720 passed, 31 skipped** at `W2_ENABLED` 0 and at 1.
- Stub evals: **67/67** (the 61 Week 1 cases plus 6 new). `evals/results/stub-latest.*` keep Week 1 per-case timings and add only the 6 new cases, so the diff is the meaningful change.
- PHP: `openemr-cmd phpunit-isolated` equivalent in the OpenEMR container, full isolated suite: **3520 tests, 8787 assertions, OK** (warnings, skips and incomplete tests are pre-existing and outside the Copilot module). The Copilot module tests alone: 46 tests, 154 assertions.
- Live, spend-capped (about $0.066 against a $0.10 cap): one real OpenRouter Stage B and retrieval run on synthetic patient data confirmed chart-claim preservation, accurate document and guideline citations, and rejection of unsupported claims.

## Manual browser testing (patient 100, synthetic)
- **Test 1, latest labs and A1c monitoring:** failed at first with "No verified statements could be produced". Two causes, both fixed. (a) `copilot.js` read a top-level `claims` list, but `/v1/w2/chat` returns `chart.claims`, `document_claims` and `guideline_claims`; the UI now renders all three groups with clickable citations. (b) The W2 verifier rejected a claim restating a recorded dose, because only the citation quote was allowed as verbatim text; the fact value is now allowed too (`verify.py`, with a regression test).
- **Test 2, genetic testing report:** returned unrelated lab and intake facts. The extractor read every stored document regardless of the question. Fixed by the report-kind check above, with worker and end-to-end regression tests.
- **Stale browser assets (Safari):** `Bootstrap.php` now adds `?v=<file mtime>` to `copilot.css`, `copilot.js` and `copilot-upload.js`.
- **Test 3, chart medications:** the four medications on patient 100's chart were returned correctly, each cited to its chart record.
- **Test 4, dose-increase request:** a request to increase the metformin dose was declined immediately, as the Week 1 boundary requires, with no retrieval or second model call.

## Not done here
Span and parent ids, `/ready` degraded reporting and OpenAPI (C4); click-to-source UI and deploy (C5). The step cap and deadline are initial values to tune from measurements in C5.

## Depends on
C1.
