"""Rerankers behind one interface: OpenRouterReranker (live, Cohere Rerank v3.5 via OpenRouter) and
LexicalReranker (deterministic, offline).

Both return (index, score) pairs, best first, with scores in 0..1 so one minimum-score cutoff works for either.
"""
from __future__ import annotations

import time
from typing import Protocol

import httpx

from app.config import Settings
from app.logging_setup import outbound_headers

from ..limits import RERANK_TIMEOUT_S, RETRY_BASE_S
from .bm25 import tokenize


class RerankError(Exception):
    pass


class Reranker(Protocol):
    name: str

    def rerank(self, query: str, docs: list[str], top: int) -> list[tuple[int, float]]: ...


class LexicalReranker:
    """Query-term coverage weighted toward rarer terms in the candidate set. No network, fully deterministic."""

    name = "lexical"

    def rerank(self, query: str, docs: list[str], top: int) -> list[tuple[int, float]]:
        q = set(tokenize(query))
        if not q or not docs:
            return []
        sets = [set(tokenize(d)) for d in docs]
        weight = {t: 1.0 / (1 + sum(t in s for s in sets)) for t in q}
        total = sum(weight.values())
        scored = [(i, sum(weight[t] for t in q if t in s) / total) for i, s in enumerate(sets)]
        scored.sort(key=lambda p: (-p[1], p[0]))
        return scored[:top]


class OpenRouterReranker:
    """Rerank through OpenRouter's /rerank endpoint (default model cohere/rerank-v3.5) with the existing OpenRouter key.

    10 s timeout, one retry on 429, 5xx or connect failure; any other failure raises RerankError. Only the query and
    guideline passages are sent. `last_meta` holds the provider and usage OpenRouter reported for the latest call.
    """

    def __init__(self, settings: Settings, transport: httpx.BaseTransport | None = None,
                 sleep=time.sleep) -> None:
        self._key = settings.openrouter_api_key.get_secret_value()
        self._url = f"{settings.openrouter_base_url.rstrip('/')}/rerank"
        self._model = settings.w2_rerank_model
        self.name = f"openrouter:{self._model}"
        self.last_meta: dict[str, object] = {}
        self._transport, self._sleep = transport, sleep

    def rerank(self, query: str, docs: list[str], top: int) -> list[tuple[int, float]]:
        if not self._key:
            raise RerankError("not_configured")
        payload = {"model": self._model, "query": query, "documents": docs, "top_n": top}
        for attempt in (1, 2):
            try:
                with httpx.Client(timeout=RERANK_TIMEOUT_S, transport=self._transport) as c:
                    r = c.post(self._url, json=payload, headers=outbound_headers({"Authorization": f"Bearer {self._key}"}))
            except httpx.HTTPError:
                if attempt == 2:
                    raise RerankError("unreachable") from None
            else:
                if r.status_code == 200:
                    return self._parse(r, len(docs))
                if (r.status_code != 429 and r.status_code < 500) or attempt == 2:
                    raise RerankError(f"http_{r.status_code}")
            self._sleep(RETRY_BASE_S)
        raise RerankError("unreachable")

    def _parse(self, r: httpx.Response, n: int) -> list[tuple[int, float]]:
        try:
            body = r.json()
            out = [(int(x["index"]), float(x["relevance_score"])) for x in body["results"]]
        except (ValueError, KeyError, TypeError):
            raise RerankError("unusable_response") from None
        if any(not 0 <= i < n for i, _ in out):
            raise RerankError("unusable_response")
        self.last_meta = {"model": body.get("model"), "provider": body.get("provider"), "usage": body.get("usage")}
        return out
