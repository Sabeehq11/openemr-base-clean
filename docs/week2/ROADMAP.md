# Week 2 roadmap (reference)

Reference only. Detailed, executable plans live in [checkpoints/](checkpoints/) and are written one at a time, when we reach each checkpoint. If this file and a checkpoint plan disagree, the checkpoint plan wins.

Source requirements: the Week 2 AgentForge PRD (Multimodal Evidence Agent). Week 1 baseline: git tag `w1-baseline` (commit `2e1ed62`).

Labeling rule: anything marked **(planned)** does not exist at `w1-baseline` and must not be treated as runnable.

## Decisions

- Orchestration: a hand-written supervisor graph with typed handoffs. No LangGraph. This matches the Week 1 decision to avoid a heavy agent framework and keeps the verifier on every path.
- Vision: a VLM through OpenRouter (Claude Sonnet, already used in Week 1).
- Retrieval: local BM25 plus dense embeddings, fused, then Cohere Rerank. A deterministic offline embedder and reranker back CI so no CI run needs a live key.
- Work happens on one branch, `week2-agentforge`, with one verified commit (or short series) per checkpoint. No per-checkpoint branches or PRs.

## Verified starting state (checked at the start of C0)

- Working tree clean; `main` at `2e1ed62`, in sync with `origin/main`. No stashes. The tag `w1-baseline` was created at C0 start.
- `agent/.env` and `agent/data/` are git-ignored; no secrets are tracked.
- No git hooks installed; `gh` is not installed locally.
- No GitHub workflow touches `agent/` or `evals/`. The PRD's PR-blocking gate does not exist yet.
- Stub eval registry: 60 cases (`evals/results/stub-latest.json` says 60/60). Some Week 1 docs still say 56.
- Local venv is Python 3.10; the agent container is Python 3.12.

## Week 1 assets reused

All paths exist at `w1-baseline`.

- Agent service in `agent/app/`: `loop.py` (tool loop), `verifier.py` (only path from model output to the UI), `fhir_client.py` (timeout, retry, typed `ToolError`), `tracing.py` (SQLite traces and spans), `logging_setup.py` (JSON logs, redaction, `outbound_headers()` correlation ID), `dashboard.py` and `metrics.py` (dashboard and three alerts), `audit.py` (HMAC chain), `authz.py` (fail-closed patient authorization), `llm.py`.
- Typed contracts in `agent/contracts/`.
- Eval harness in `evals/` (`registry.py`, `harness.py`, `run.py`).
- Bruno collection in `docs/api-collection/copilot`, Playwright specs in `tests/playwright/specs`, PHP module `interface/modules/custom_modules/oe-module-copilot`, deployment files in `deploy/`, load tests in `loadtest/`.
- Data authority: OpenEMR is authoritative. The agent has no write tool today, and a Week 1 invariant eval asserts that.

## Gaps Week 2 must close

1. No CI runs `agent/` tests or `evals/`.
2. No deployed app (the README says TBD). The remote is GitHub, so GitHub Actions stands in for GitLab CI.
3. Spans have no span ID or parent ID. Worker child spans need an additive trace-schema migration.
4. `/ready` is binary (200 or 503). The PRD wants a degraded status.
5. OpenEMR FHIR cannot write Observation. `POST /api/patient/:pid/document` exists and takes the integer pid. The agent's OAuth client is read-only (`user/document.write` exists as a scope but is not registered for it).
6. Docs quote stale numbers (56 evals, 321 passed). New docs use measured numbers only.

## Isolation rules (protect Week 1)

- New code goes under `agent/app/w2/` **(planned)** and `agent/contracts/w2/` **(planned)**. Week 1 files change only at named seams: tracing, metrics, `/ready`, the verifier's citation types, the eval registry.
- `W2_ENABLED` **(planned)** defaults off. `/v1/chat` is untouched. The new flow is `/v1/w2/chat` **(planned)**.
- The supervisor wraps the existing `AgentLoop` as its chart-facts capability. The loop is not rewritten.
- The Week 1 "no write tool" invariant is amended on purpose: one server-side `attach_and_extract` **(planned)** path may write, and it is not in the chat model's tool list.
- Every Week 1 schema change carries a migration note. Trace tables get additive columns only.

## Checkpoints

Dates are from the PRD: MVP Tuesday 11:59 PM, Early Submission Thursday 11:59 PM, Final Sunday noon (Central).

| Id | Name | Status |
|---|---|---|
| C0 | Baseline and architecture defense | in progress |
| C1 | MVP: ingest, extract, retrieve | not started |
| C2 | Supervisor and two workers | not started |
| C3 | Eval gate (the PRD hard gate) | not started |
| C4 | Observability, contracts, ops | not started |
| C5 | Deploy, performance, defend | not started |

### C0. Baseline and architecture defense
Docs and records only. Tag `w1-baseline`, record a measured baseline, write `W2_ARCHITECTURE.md`, split the README into Week 1 and Week 2 sections. No application code changes. Detail: [checkpoints/C0-baseline.md](checkpoints/C0-baseline.md).

### C1. MVP: ingest, extract, retrieve
- Open with a spike on the OpenEMR write path (`user/document.write` versus a module-side PHP write).
- Strict contracts: `Citation`, `LabPdfExtraction`, `IntakeFormExtraction`. Raw VLM output never bypasses validation.
- Upload through the module, `attach_and_extract(patient_id, file_path, doc_type)`, idempotent on file hash plus patient. Derived facts in a `w2_facts` store with a link to the source document.
- Guideline corpus, hybrid BM25 plus dense retrieval, Cohere Rerank, offline stubs. Synthetic fixtures.
- Accept: local demo of upload, cited JSON and a guideline snippet; Week 1 tests and evals pass with `W2_ENABLED` both 0 and 1.

### C2. Supervisor and two workers
- Supervisor state machine with typed `Handoff` objects, intake-extractor, evidence-retriever.
- Answer model sees chart or extracted facts and guideline evidence in separate labelled blocks. Citation `source_type`: `chart | extracted | guideline`.
- Additive verifier changes for extracted-fact and guideline-chunk citations.
- Accept: every handoff is a logged event; a trace is reconstructable from the correlation ID.

### C3. Eval gate
- 50-case golden set in the repo with boolean rubrics: `schema_valid`, `citation_present`, `factually_consistent`, `safe_refusal`, `no_phi_in_logs`.
- Baseline file and a gate script that fails on a drop of more than 5 points in any category or below an absolute threshold.
- PR-blocking GitHub workflow plus a local hook. Stub tier only in the gate.
- Accept: an injected regression fails the gate, with evidence kept.

### C4. Observability, contracts, ops
- Correlation ID through ingestion, VLM, retrieval, handoffs and OpenEMR writes; parent span IDs; W2 events in the existing structured log format.
- Dashboard metrics and alerts for ingestion, extraction pass rate, retrieval hit rate, routing and eval regression, each with a runbook entry.
- `/ready` returning ready, degraded or not_ready; SLOs; timeouts and retries on every outbound call.
- OpenAPI 3 spec with a contract test, Bruno collection updates, PHI-detection CI check.

### C5. Deploy, performance, defend
- Deployment with the agent service, click-to-source UI with bounding-box overlay, baselines and a cost and latency report, backup and recovery drill, demo video.
- Stretch only if time remains: critic agent, third document type, lab trend chart.

## Data authority

- Source documents and chart facts: OpenEMR (authoritative; the agent reads only).
- Extracted facts: agent-side `w2_facts` **(planned)** (derived; never overwrites chart data; conflicts are shown, not merged).
- Guideline chunks and the eval golden set: the repo (git).

## Risks

- OpenEMR write path (scope or pid mismatch): spike first, fall back to the module PHP path.
- VLM hallucination: strict schemas, required quote, `unsupported` marking.
- Latency: Week 1 real-model p95 is about 19 s; measure Week 2 separately and set per-worker timeouts.
- Eval flakiness: the gate uses the deterministic stub tier only.
- PHI leakage: synthetic fixtures only, scrubbing plus a CI check.
- Time: C3 cannot slip. Cut stretch items first, and scope C1 and C2 to the minimum C3 needs.
