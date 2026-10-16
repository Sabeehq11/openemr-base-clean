"""Embedders behind one interface: HashedEmbedder (deterministic, offline, for CI) and HostedEmbedder (live tier).

Only guideline text and de-identified query terms are ever embedded. Hosted vectors are cached on disk by text hash.
"""
from __future__ import annotations

import hashlib
import json
import logging
import math
import random
import time
from pathlib import Path
from typing import Protocol

import httpx

from app.config import Settings
from app.logging_setup import outbound_headers

from ..limits import EMBED_TIMEOUT_S, RETRY_BASE_S
from .bm25 import tokenize

logger = logging.getLogger("agent.w2.embed")
DIM = 256


class EmbedError(Exception):
    pass


class Embedder(Protocol):
    name: str

    def embed(self, texts: list[str]) -> list[list[float]]: ...


def _normalize(v: list[float]) -> list[float]:
    n = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / n for x in v]


def cosine(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b))


class HashedEmbedder:
    """Hashed bag-of-words vectors. Same input, same output, no network: the CI embedder."""

    name = "hashed-bow-256"

    def embed(self, texts: list[str]) -> list[list[float]]:
        out = []
        for t in texts:
            v = [0.0] * DIM
            for tok in tokenize(t):
                h = int.from_bytes(hashlib.sha256(tok.encode()).digest()[:4], "big")
                v[h % DIM] += 1.0
            out.append(_normalize(v))
        return out


class HostedEmbedder:
    """OpenRouter embeddings with an on-disk cache. Raises EmbedError on any failure so the caller can fall back."""

    def __init__(self, settings: Settings, cache_path: str, transport: httpx.BaseTransport | None = None,
                 sleep=time.sleep) -> None:
        self._s, self._transport, self._sleep = settings, transport, sleep
        self.name = f"hosted:{settings.w2_embed_model}"
        self._cache_path = Path(cache_path)
        try:
            self._cache: dict[str, list[float]] = json.loads(self._cache_path.read_text())
        except (OSError, ValueError):
            self._cache = {}

    def _key(self, text: str) -> str:
        return hashlib.sha256(f"{self._s.w2_embed_model}\n{text}".encode()).hexdigest()

    def embed(self, texts: list[str]) -> list[list[float]]:
        missing = [t for t in texts if self._key(t) not in self._cache]
        if missing:
            self._fetch(missing)
        return [self._cache[self._key(t)] for t in texts]

    def _post(self, texts: list[str], key: str) -> httpx.Response:
        """One retry, jittered, on 429, 5xx or a connect failure. A read timeout is not retried."""
        for attempt in (1, 2):
            try:
                with httpx.Client(timeout=EMBED_TIMEOUT_S, transport=self._transport) as c:
                    r = c.post(f"{self._s.openrouter_base_url}/embeddings",
                               json={"model": self._s.w2_embed_model, "input": texts},
                               headers=outbound_headers({"Authorization": f"Bearer {key}"}))
            except httpx.TimeoutException:
                raise EmbedError("timeout") from None
            except httpx.HTTPError:
                if attempt == 2:
                    raise EmbedError("unreachable") from None
            else:
                if r.status_code == 200:
                    return r
                if (r.status_code != 429 and r.status_code < 500) or attempt == 2:
                    raise EmbedError(f"http_{r.status_code}")
            self._sleep(RETRY_BASE_S + random.uniform(0, RETRY_BASE_S))
        raise EmbedError("unreachable")

    def _fetch(self, texts: list[str]) -> None:
        key = self._s.openrouter_api_key.get_secret_value()
        if not key:
            raise EmbedError("not_configured")
        r = self._post(texts, key)
        try:
            data = sorted(r.json()["data"], key=lambda d: d["index"])
            vectors = [_normalize([float(x) for x in d["embedding"]]) for d in data]
        except (ValueError, KeyError, TypeError):
            raise EmbedError("unusable_response") from None
        if len(vectors) != len(texts):
            raise EmbedError("count_mismatch")
        for t, v in zip(texts, vectors):
            self._cache[self._key(t)] = v
        try:
            self._cache_path.parent.mkdir(parents=True, exist_ok=True)
            self._cache_path.write_text(json.dumps(self._cache))
        except OSError:
            logger.warning("embedding cache not writable")
