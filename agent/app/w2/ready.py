"""Week 2 readiness checks for /ready. They never call a provider.

A paid or slow request on every health probe is a cost and an outage amplifier, so each check reads configuration and
the in-memory result of the last real call (kept by the retriever). A provider that has not been used yet is reported
ok with the detail "no traffic yet"; real traffic is what refreshes the state. Only the local store is actually opened.
A failing check makes the service `degraded` (Week 2 is impaired, Week 1 chat is not), never `not_ready`.
"""
from __future__ import annotations

from typing import Callable

from app.config import Settings

from .rag.retriever import Retriever
from .store import W2Store

Check = dict[str, object]


def _check(ok: bool, detail: str) -> Check:
    return {"ok": ok, "detail": detail}


def w2_checks(settings: Settings, store: W2Store, retriever: Retriever) -> dict[str, Check]:
    offline = settings.w2_use_stub_vlm
    keyed = bool(settings.openrouter_api_key.get_secret_value())
    try:
        store.ping()
        document_store = _check(True, "w2.db answers")
    except Exception as e:  # the class name only, never the message
        document_store = _check(False, type(e).__name__)

    if retriever.corpus_size == 0:
        index = _check(False, "guideline corpus is empty")
    elif retriever.last_embed_ok is False:
        index = _check(False, f"{retriever.corpus_size} chunks; last embedding call failed, sparse-only")
    else:
        index = _check(True, f"{retriever.corpus_size} chunks; " + ("offline embedder" if offline or not keyed
                                                                     else "hosted embedder"))

    if offline:
        reranker = _check(True, "offline lexical reranker")
    elif not keyed:
        reranker = _check(False, "OPENROUTER_API_KEY not set; lexical fallback in use")
    elif retriever.last_rerank_ok is False:
        reranker = _check(False, "last rerank call failed; fused order in use")
    else:
        reranker = _check(True, "configured; " + ("last call ok" if retriever.last_rerank_ok else "no traffic yet"))
    return {"document_store": document_store, "vector_index": index, "reranker": reranker}


ChecksFn = Callable[[], dict[str, Check]]
