"""Every outbound timeout and retry budget in Week 2, in one place, so the SLO table cites real numbers.

Each call has a timeout and at most one retry, taken only on 429, 5xx or a connect failure. A read timeout is never
retried (the provider may have processed the request, and a second wait would blow the deadline). A failure degrades
instead of failing the request: the embedder falls back to sparse-only, the reranker to the lexical order.
"""
from __future__ import annotations

VLM_TIMEOUT_S = 30.0
RERANK_TIMEOUT_S = 10.0
EMBED_TIMEOUT_S = 20.0
RETRY_BASE_S = 0.4  # backoff is this plus up to the same again of jitter
SUPERVISOR_WORKER_TIMEOUT_S = 30.0  # outer bound per node in /v1/w2/chat
SUPERVISOR_DEADLINE_S = 40.0

# The SLOs (ingestion and retrieval p95) live in app/metrics.py beside the alert limits.
