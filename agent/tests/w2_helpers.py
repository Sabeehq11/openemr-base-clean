"""Shared Week 2 test helpers: fixture loading and a store/ingestor factory."""
from pathlib import Path

from app.w2.ingest import Ingestor
from app.w2.render import PageRenderer, Rendered, RenderError
from app.w2.store import W2Store
from app.w2.vlm import StubVlm
from contracts.w2 import DocType

FIX = Path(__file__).parent / "fixtures" / "w2"
PATIENT = "11111111-2222-3333-4444-555555555555"
OTHER = "99999999-2222-3333-4444-555555555555"


def fx(name: str) -> bytes:
    return (FIX / name).read_bytes()


def fxt(name: str) -> str:
    return (FIX / name).read_text()


def stub(lab: str = "vlm_lab_good.json", intake: str = "vlm_intake_good.json") -> StubVlm:
    return StubVlm({DocType.LAB_PDF: fxt(lab), DocType.INTAKE_FORM: fxt(intake)})


def make_ingestor(tmp_path, vlm=None, chain=None, native=True, max_bytes=1_000_000, max_pages=5):
    store = W2Store(str(tmp_path / "w2.db"))
    return Ingestor(store, vlm or stub(), max_bytes, max_pages, chain, native), store


class BrokenRenderer:
    def __init__(self, name="broken"):
        self.name = name
        self.calls = 0

    def available(self) -> bool:
        return True

    def render(self, data: bytes, max_pages: int) -> Rendered:
        self.calls += 1
        raise RenderError("RuntimeError")


class EmptyRenderer(BrokenRenderer):
    def render(self, data: bytes, max_pages: int) -> Rendered:
        self.calls += 1
        return Rendered([], 0, self.name)


_: PageRenderer
