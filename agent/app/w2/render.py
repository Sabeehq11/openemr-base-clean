"""PDF page rendering with a fallback chain. PDF ingestion never depends on one library.

Order: PyMuPDF, pypdfium2, Poppler (pdftoppm). A renderer that raises, times out or returns no pages is skipped and
the next is tried; attempts are logged by renderer name and error class only, never document content.
"""
from __future__ import annotations

import io
import logging
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from app.logging_setup import log_event
from .safelog import error_code

logger = logging.getLogger("agent.w2.render")

DPI = 110
RENDER_TIMEOUT_S = 20


class RenderError(Exception):
    """A renderer could not produce pages. Carries only an error class name, never document content."""


@dataclass(frozen=True)
class TextLine:
    """One line of the PDF's own text and its box as fractions of the page (origin top-left). Read from the file,
    never from a model, so a citation built on it points at the printed line."""

    page: int  # 1-based
    text: str
    bbox: tuple[float, float, float, float]


@dataclass(frozen=True)
class Rendered:
    pages: list[bytes]  # PNG bytes, at most max_pages
    total_pages: int
    renderer: str
    text_layer: str = ""  # embedded text when the renderer exposes it (used to check quotes)
    lines: tuple[TextLine, ...] = ()  # verified line boxes; empty when the renderer cannot give them


class PageRenderer(Protocol):
    name: str

    def available(self) -> bool: ...

    def render(self, data: bytes, max_pages: int) -> Rendered: ...


def _page_lines(page, number: int) -> list[TextLine]:
    """Text lines with page-relative boxes. A rotated page or a box outside the page is skipped: no box beats a
    wrong box."""
    if page.rotation:
        return []
    width, height = page.rect.width, page.rect.height
    if width <= 0 or height <= 0:
        return []
    out: list[TextLine] = []
    for block in page.get_text("dict").get("blocks", []):
        for line in block.get("lines", []):
            text = "".join(span.get("text", "") for span in line.get("spans", [])).strip()
            x0, y0, x1, y1 = line["bbox"]
            box = (round(x0 / width, 4), round(y0 / height, 4), round(x1 / width, 4), round(y1 / height, 4))
            if text and all(0.0 <= c <= 1.0 for c in box) and box[0] < box[2] and box[1] < box[3]:
                out.append(TextLine(number, text, box))
    return out


class PyMuPDFRenderer:
    name = "pymupdf"

    def available(self) -> bool:
        try:
            import pymupdf  # noqa: F401
        except Exception:
            return False
        return True

    def render(self, data: bytes, max_pages: int) -> Rendered:
        try:
            import pymupdf

            doc = pymupdf.open(stream=data, filetype="pdf")
            total = doc.page_count
            pages, text, lines = [], [], []
            for i in range(min(total, max_pages)):
                pages.append(doc[i].get_pixmap(dpi=DPI).tobytes("png"))
                text.append(doc[i].get_text())
                lines.extend(_page_lines(doc[i], i + 1))
        except Exception as e:  # any library failure falls through to the next renderer
            raise RenderError(type(e).__name__) from None
        return Rendered(pages, total, self.name, "\n".join(text), tuple(lines))


class PdfiumRenderer:
    name = "pypdfium2"

    def available(self) -> bool:
        try:
            import pypdfium2  # noqa: F401
        except Exception:
            return False
        return True

    def render(self, data: bytes, max_pages: int) -> Rendered:
        try:
            import pypdfium2 as pdfium

            pdf = pdfium.PdfDocument(data)
            total = len(pdf)
            pages, text = [], []
            for i in range(min(total, max_pages)):
                buf = io.BytesIO()
                pdf[i].render(scale=DPI / 72).to_pil().save(buf, format="PNG")
                pages.append(buf.getvalue())
                text.append(pdf[i].get_textpage().get_text_range())
        except Exception as e:
            raise RenderError(type(e).__name__) from None
        return Rendered(pages, total, self.name, "\n".join(text))


class PopplerRenderer:
    name = "poppler"

    def available(self) -> bool:
        return shutil.which("pdftoppm") is not None

    def render(self, data: bytes, max_pages: int) -> Rendered:
        try:
            with tempfile.TemporaryDirectory() as tmp:
                src = Path(tmp) / "in.pdf"
                src.write_bytes(data)
                subprocess.run(
                    ["pdftoppm", "-png", "-r", str(DPI), "-l", str(max_pages + 1), str(src), str(Path(tmp) / "p")],
                    check=True, capture_output=True, timeout=RENDER_TIMEOUT_S)
                files = sorted(Path(tmp).glob("p-*.png"))
                pages = [f.read_bytes() for f in files[:max_pages]]
                total = len(files)  # one page past the cap is enough to know the file is over it
        except Exception as e:
            raise RenderError(type(e).__name__) from None
        return Rendered(pages, total, self.name)


def default_chain() -> list[PageRenderer]:
    return [PyMuPDFRenderer(), PdfiumRenderer(), PopplerRenderer()]


def available_renderers(chain: list[PageRenderer] | None = None) -> list[str]:
    return [r.name for r in (chain or default_chain()) if r.available()]


def render_pdf(data: bytes, max_pages: int, chain: list[PageRenderer] | None = None) -> Rendered | None:
    """First renderer that yields at least one page wins; None when every renderer failed."""
    for r in chain or default_chain():
        if not r.available():
            log_event(logger, "render_skipped", renderer=r.name, why="unavailable")
            continue
        try:
            out = r.render(data, max_pages)
        except RenderError as e:
            log_event(logger, "render_failed", renderer=r.name, error=error_code(e))
            continue
        if not out.pages:
            log_event(logger, "render_failed", renderer=r.name, error="no_pages")
            continue
        log_event(logger, "render_ok", renderer=r.name, pages=len(out.pages))
        return out
    return None
