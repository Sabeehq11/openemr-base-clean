"""Hybrid retriever: BM25 + dense embeddings, fused with reciprocal rank fusion, then reranked.

Queries hold the question and test names only. Never pass names, dates of birth or other identifiers in.
"""
from __future__ import annotations

import logging

from app import tracing
from app.logging_setup import log_event
from contracts.w2 import Citation, GuidelineChunk, RetrievalResult, RetrievalStatus, RetrievedSnippet, SourceType

from .bm25 import BM25
from .embed import EmbedError, Embedder, cosine
from .rerank import RerankError, Reranker
from ..safelog import error_code

logger = logging.getLogger("agent.w2.retrieve")

RRF_K = 60
CANDIDATES = 20
MIN_SCORE = 0.15  # below this a passage is not evidence; the result is `empty`
MAX_QUERY_CHARS = 400


def rrf(rankings: list[list[int]], k: int = RRF_K) -> list[int]:
    """Reciprocal rank fusion. Ties break by chunk index so the order is deterministic."""
    score: dict[int, float] = {}
    for ranking in rankings:
        for rank, idx in enumerate(ranking, start=1):
            score[idx] = score.get(idx, 0.0) + 1.0 / (k + rank)
    return sorted(score, key=lambda i: (-score[i], i))


class Retriever:
    def __init__(self, chunks: list[GuidelineChunk], embedder: Embedder, reranker: Reranker,
                 fallback_reranker: Reranker | None = None, min_score: float = MIN_SCORE) -> None:
        self._chunks, self._embedder, self._reranker = chunks, embedder, reranker
        self._fallback, self._min = fallback_reranker, min_score
        self._texts = [f"{c.section}. {c.text}" for c in chunks]
        self._bm25 = BM25(self._texts)
        self._vectors: list[list[float]] | None = None  # embedded on first use; an outage must not break startup
        # In-memory state for /ready, so readiness never makes a (paid) provider call of its own. None = no traffic yet.
        self.last_rerank_ok: bool | None = None
        self.last_embed_ok: bool | None = None
        self.corpus_size = len(chunks)

    def _dense(self, query: str, top: int) -> list[int]:
        try:
            with tracing.span("w2", "embed"):
                if self._vectors is None:
                    self._vectors = self._embedder.embed(self._texts)
                q = self._embedder.embed([query])[0]
            self.last_embed_ok = True
        except EmbedError:
            self.last_embed_ok = False
            return []  # sparse-only is a degraded but valid result; the corpus is retried on the next query
        sims = [(cosine(q, v), i) for i, v in enumerate(self._vectors)]
        return [i for s, i in sorted(sims, key=lambda p: (-p[0], p[1]))[:top] if s > 0]

    def retrieve(self, query: str, top: int = 5) -> RetrievalResult:
        """One `w2/retrieve` span per call, with embed and rerank as children. Counts only: hits, candidates, skipped."""
        with tracing.span("w2", "retrieve") as attrs:
            result = self._retrieve(query, top, attrs)
            attrs.update(hits=len(result.snippets), miss=int(result.status is not RetrievalStatus.OK),
                         rerank_skipped=int(result.rerank_skipped))
            return result

    def _retrieve(self, query: str, top: int, attrs: dict) -> RetrievalResult:
        query = " ".join(query.split())[:MAX_QUERY_CHARS]
        top = max(1, min(top, 5))
        fused = rrf([self._bm25.rank(query, CANDIDATES), self._dense(query, CANDIDATES)])[:CANDIDATES]
        attrs["candidates"] = len(fused)
        if not fused:
            return RetrievalResult(status=RetrievalStatus.EMPTY)
        docs = [self._texts[i] for i in fused]
        skipped = False
        attrs["candidates"] = len(fused)
        try:
            with tracing.span("w2", "rerank"):
                ranked = self._reranker.rerank(query, docs, top)
            used = self._reranker.name
            self.last_rerank_ok = True
        except RerankError as e:
            log_event(logger, "w2_rerank", status="skipped", error=error_code(e))
            self.last_rerank_ok = False
            skipped = True
            used = self._fallback.name if self._fallback else "none"
            ranked = self._fallback.rerank(query, docs, top) if self._fallback else [(i, 1.0) for i in range(top)]
        kept = [(fused[i], s) for i, s in ranked if s >= self._min][:top]
        if not kept:
            log_event(logger, "w2_retrieve", status="miss", hits=0, candidates=len(fused), rerank_skipped=skipped,
                      reranker=used)
            return RetrievalResult(status=RetrievalStatus.EMPTY, rerank_skipped=skipped)
        log_event(logger, "w2_retrieve", status="hit", hits=len(kept), candidates=len(fused), rerank_skipped=skipped,
                  reranker=used)
        return RetrievalResult(status=RetrievalStatus.OK, rerank_skipped=skipped,
                               snippets=[self._snippet(i, s) for i, s in kept])

    def _snippet(self, idx: int, score: float) -> RetrievedSnippet:
        c = self._chunks[idx]
        cite = Citation(source_type=SourceType.GUIDELINE, source_id=c.doc_id, page_or_section=c.section,
                        field_or_chunk_id=c.chunk_id, quote_or_value=c.text[:500])
        return RetrievedSnippet(chunk_id=c.chunk_id, score=round(score, 4), text=c.text, title=c.title,
                                publisher=c.publisher, year=c.year, citation=cite)
