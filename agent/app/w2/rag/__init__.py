"""Hybrid guideline retrieval (BM25 + dense, RRF, rerank) with deterministic offline stand-ins for CI."""
from __future__ import annotations

from app.config import Settings

from .corpus import load_corpus
from .embed import HashedEmbedder, HostedEmbedder
from .rerank import OpenRouterReranker, LexicalReranker
from .retriever import Retriever


def build_retriever(settings: Settings, offline: bool) -> Retriever:
    """offline=True never touches the network (CI and demos without keys); otherwise hosted embeddings and Cohere Rerank via OpenRouter,
    each degrading to the offline implementation when its key is absent."""
    chunks = load_corpus()
    lexical = LexicalReranker()
    if offline or not settings.openrouter_api_key.get_secret_value():
        reranker = lexical
    else:
        reranker = OpenRouterReranker(settings)
    if offline or not settings.openrouter_api_key.get_secret_value():
        embedder = HashedEmbedder()
    else:
        embedder = HostedEmbedder(settings, f"{settings.data_dir}/w2_embed_cache.json")
    return Retriever(chunks, embedder, reranker, fallback_reranker=lexical)
