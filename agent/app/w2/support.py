"""Support checks: decide whether an extracted value is backed by its own quote and region.

The model supplies evidence only. Citations are always built here, never taken from model output.
"""
from __future__ import annotations

import json
import re

from contracts.w2 import Citation, SourceType
from contracts.w2.extraction import RawEvidence

from .render import TextLine

NATIVE_CONFIDENCE_CAP = 0.5


class ExtractionError(Exception):
    """Raw output was not valid. The message is a fixed code, never document content."""


_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.I)


def parse_json(raw: str) -> object:
    try:
        return json.loads(_FENCE.sub("", raw.strip()))
    except ValueError:
        raise ExtractionError("schema_invalid") from None


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s.replace(",", "").strip().lower())


def _squash(s: str) -> str:
    return re.sub(r"\s+", "", s.lower())


def contains_value(quote: str, value: str) -> bool:
    """The value must appear in the quote as a whole token after normalization ('7.9' is not inside '17.95')."""
    v = _norm(value)
    if not v:
        return False
    return re.search(r"(?<![\w.])" + re.escape(v) + r"(?![\w]|\.\d)", _norm(quote)) is not None


def in_text_layer(quote: str, text_layer: str) -> bool:
    return _squash(quote) in _squash(text_layer)


def valid_bbox(b: tuple[float, float, float, float] | None) -> bool:
    if b is None:
        return False
    x0, y0, x1, y1 = b
    return all(0.0 <= c <= 1.0 for c in b) and x0 < x1 and y0 < y1


PAGE_BOX: tuple[float, float, float, float] = (0.0, 0.0, 1.0, 1.0)  # "somewhere on this page": claims no position


def verified_bbox(quote: str, page: int, lines: tuple[TextLine, ...]) -> tuple[float, float, float, float] | None:
    """The box of the one PDF text line on `page` that contains `quote`. None when there is no such line or more than
    one (a repeated line cannot be told apart), so the caller never picks a box it cannot stand behind."""
    q = _squash(quote)
    if not q:
        return None
    boxes = {ln.bbox for ln in lines if ln.page == page and q in _squash(ln.text)}
    return next(iter(boxes)) if len(boxes) == 1 else None


def build_citation(document_id: int, ev: RawEvidence, field: str,
                   lines: tuple[TextLine, ...] = ()) -> Citation | None:
    """A citation exists only when there is a quote and the model pointed at a region; otherwise nothing can be
    pointed at. The stored box is never the model's: it is the PDF's own line box when that can be verified, and the
    whole page when it cannot (scans, images, no text layer, a quote spanning lines or repeated)."""
    quote = ev.quote.strip()
    if not quote or not valid_bbox(ev.bbox):
        return None
    box = verified_bbox(quote, ev.page, lines) or PAGE_BOX
    return Citation(source_type=SourceType.EXTRACTED, source_id=f"Document/{document_id}",
                    page_or_section=str(ev.page), field_or_chunk_id=field, quote_or_value=quote[:2000], bbox=box)


def is_supported(value: str, ev: RawEvidence, citation: Citation | None, text_layer: str, native: bool) -> bool:
    """Supported = has a citation, the value is in its quote, and (when a text layer exists) the quote is really on
    the page. In native-PDF mode there is no region we can check, so the quote must be found in the PDF text."""
    if citation is None or not contains_value(ev.quote, value):
        return False
    if text_layer:
        return in_text_layer(ev.quote, text_layer)
    return not native
