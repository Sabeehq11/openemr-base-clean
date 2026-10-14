# C4. Observability, contracts, ops

Status: not started. High-level only; expanded when we reach C4.

Everything named here that is new in Week 2 is **(planned)**.

## Goal
Make the Week 2 flow observable and its interfaces contract-checked, extending Week 1 conventions rather than adding parallel ones.

## Scope
- Correlation ID through ingestion, VLM calls, retrieval, handoffs and OpenEMR writes. Span and parent span IDs as an additive trace-schema migration.
- W2 events in the existing structured JSON log format; dashboard metrics and alerts (extraction failure rate, retrieval latency, eval regression), each with a runbook entry.
- `/ready` returning ready, degraded or not_ready, checking document storage, vector index and reranker. SLOs, timeouts and retries on every outbound call.
- OpenAPI 3 spec with a contract test, Bruno collection updates, PHI-detection check in CI.

## Acceptance (summary)
- A full multi-agent trace can be rebuilt from the correlation ID alone.
- The dashboard shows health without reading logs.
- Contract tests pass; the PHI check passes on logs, traces and eval outputs.

## Depends on
C3.
