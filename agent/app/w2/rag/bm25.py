"""Small Okapi BM25 over chunk text. No dependency; deterministic."""
from __future__ import annotations

import math
import re
from collections import Counter

_TOKEN = re.compile(r"[a-z0-9]+(?:\.[0-9]+)?")
STOP = frozenset("a an and are as at be by for from has have in is it of on or that the this to was were with".split())


def tokenize(text: str) -> list[str]:
    return [t for t in _TOKEN.findall(text.lower()) if t not in STOP]


class BM25:
    def __init__(self, docs: list[str], k1: float = 1.5, b: float = 0.75) -> None:
        self._k1, self._b = k1, b
        self._tf = [Counter(tokenize(d)) for d in docs]
        self._len = [sum(c.values()) for c in self._tf]
        self._avg = (sum(self._len) / len(docs)) if docs else 0.0
        df: Counter[str] = Counter()
        for c in self._tf:
            df.update(c.keys())
        n = len(docs)
        self._idf = {t: math.log(1 + (n - f + 0.5) / (f + 0.5)) for t, f in df.items()}

    def scores(self, query: str) -> list[float]:
        terms = tokenize(query)
        out = []
        for tf, ln in zip(self._tf, self._len):
            s = 0.0
            for t in terms:
                f = tf.get(t, 0)
                if f:
                    s += self._idf.get(t, 0.0) * f * (self._k1 + 1) / (f + self._k1 * (1 - self._b + self._b * ln / (self._avg or 1)))
            out.append(s)
        return out

    def rank(self, query: str, top: int) -> list[int]:
        sc = self.scores(query)
        order = sorted(range(len(sc)), key=lambda i: (-sc[i], i))  # index breaks ties: deterministic
        return [i for i in order[:top] if sc[i] > 0]
