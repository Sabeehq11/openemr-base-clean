"""Loads the guideline corpus: markdown with front matter, one chunk per `##` section. Public text only."""
from __future__ import annotations

import re
from pathlib import Path

from contracts.w2 import GuidelineChunk

_FRONT = re.compile(r"\A---\n(.*?)\n---\n(.*)\Z", re.S)
REQUIRED = ("doc_id", "title", "publisher", "year", "licence")


class CorpusError(Exception):
    pass


def default_corpus_dir() -> Path:
    """The corpus ships with the agent (agent/corpus), so it is inside the Docker build context and the image."""
    return Path(__file__).resolve().parents[3] / "corpus"


def _front_matter(text: str, name: str) -> tuple[dict[str, str], str]:
    m = _FRONT.match(text)
    if not m:
        raise CorpusError(f"{name}: missing front matter")
    meta: dict[str, str] = {}
    for line in m.group(1).splitlines():
        key, _, value = line.partition(":")
        meta[key.strip()] = value.strip()
    missing = [k for k in REQUIRED if not meta.get(k)]
    if missing:
        raise CorpusError(f"{name}: front matter lacks {', '.join(missing)}")
    return meta, m.group(2)


def load_corpus(directory: Path | None = None) -> list[GuidelineChunk]:
    chunks: list[GuidelineChunk] = []
    for path in sorted((directory or default_corpus_dir()).glob("*.md")):
        meta, body = _front_matter(path.read_text(encoding="utf-8"), path.name)
        sections = re.split(r"^## +", body, flags=re.M)[1:]
        for n, sec in enumerate(sections, start=1):
            title, _, text = sec.partition("\n")
            chunks.append(GuidelineChunk(
                chunk_id=f"{meta['doc_id']}#{n}", doc_id=meta["doc_id"], title=meta["title"],
                publisher=meta["publisher"], year=int(meta["year"]), section=title.strip(),
                url=meta.get("url", ""), licence=meta["licence"], text=text.strip()))
    if not chunks:
        raise CorpusError("corpus is empty")
    ids = [c.chunk_id for c in chunks]
    if len(set(ids)) != len(ids):
        raise CorpusError("duplicate chunk ids")
    return chunks
