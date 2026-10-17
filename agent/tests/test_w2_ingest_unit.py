"""Ingest, idempotency and the PDF fallback chain. Each test names the failure it guards."""
import logging
import shutil

import pytest

from app.w2.ingest import RejectedUpload, attach_and_extract
from app.w2.render import PdfiumRenderer, PopplerRenderer, PyMuPDFRenderer, render_pdf
from app.w2.vlm import StubVlm, VlmError
from contracts.w2 import DocType
from tests.w2_helpers import OTHER, PATIENT, BrokenRenderer, EmptyRenderer, fx, make_ingestor, stub

LAB, INTAKE = DocType.LAB_PDF, DocType.INTAKE_FORM


async def test_lab_pdf_extracts_and_stores_rows(tmp_path):
    ing, store = make_ingestor(tmp_path)
    res = await ing.ingest(PATIENT, 7, LAB, fx("lab_report.pdf"))
    assert res.record.status.value == "extracted" and len(res.record.lab.rows) == 5
    assert store.get(PATIENT, 7).lab.rows[0].citation.bbox


async def test_intake_form_extracts(tmp_path):
    ing, _ = make_ingestor(tmp_path)
    res = await ing.ingest(PATIENT, 8, INTAKE, fx("intake_form.pdf"))
    assert res.record.intake.demographics.last_name.value == "Syntheticson"


async def test_repeat_upload_returns_existing_row_and_makes_no_second_vlm_call(tmp_path):
    vlm = stub()
    ing, store = make_ingestor(tmp_path, vlm)
    first = await ing.ingest(PATIENT, 7, LAB, fx("lab_report.pdf"))
    again = await ing.ingest(PATIENT, 99, LAB, fx("lab_report.pdf"))
    # The extraction moves to the id this request owns. Citing the first id would point at a document OpenEMR does
    # not have, so the page viewer could not highlight it.
    assert again.duplicate and again.record.document_id == 99 and store.get(PATIENT, 7) is None
    assert again.record.lab.rows[0].citation.source_id == "Document/99"
    assert len(vlm.calls) == 1 and store.find_by_hash(PATIENT, first.record.sha256).document_id == 99


async def test_same_file_for_another_patient_is_a_separate_record(tmp_path):
    ing, store = make_ingestor(tmp_path)
    await ing.ingest(PATIENT, 7, LAB, fx("lab_report.pdf"))
    other = await ing.ingest(OTHER, 8, LAB, fx("lab_report.pdf"))
    assert not other.duplicate and store.get(PATIENT, 8) is None and store.get(OTHER, 7) is None


async def test_oversize_wrong_type_and_too_many_pages_are_rejected_before_any_vlm_call(tmp_path):
    vlm = stub()
    ing, _ = make_ingestor(tmp_path, vlm, max_bytes=1000)
    with pytest.raises(RejectedUpload, match="too_large"):
        await ing.ingest(PATIENT, 1, LAB, b"%PDF-" + b"0" * 2000)
    ing, _ = make_ingestor(tmp_path, vlm)
    with pytest.raises(RejectedUpload, match="unsupported_type"):
        await ing.ingest(PATIENT, 2, LAB, fx("wrong_type.pdf"))
    import pymupdf

    doc = pymupdf.open()
    for _ in range(3):
        doc.new_page()
    ing, _ = make_ingestor(tmp_path, vlm, max_pages=2)
    with pytest.raises(RejectedUpload, match="too_many_pages"):
        await ing.ingest(PATIENT, 3, LAB, doc.tobytes())
    assert vlm.calls == []


async def test_png_scan_needs_no_renderer(tmp_path):
    vlm = stub()
    ing, _ = make_ingestor(tmp_path, vlm, chain=[BrokenRenderer()])
    res = await ing.ingest(PATIENT, 4, LAB, fx("lab_scan.png"))
    assert res.record.status.value == "extracted" and vlm.calls == [(LAB, 1, False)]


async def test_vlm_timeout_keeps_document_as_extraction_failed_with_zero_facts(tmp_path):
    ing, store = make_ingestor(tmp_path, StubVlm(raises=VlmError("timeout")))
    res = await ing.ingest(PATIENT, 7, LAB, fx("lab_report.pdf"))
    assert res.record.status.value == "extraction_failed" and res.record.error_code.value == "vlm_unavailable"
    assert res.record.lab is None and store.get(PATIENT, 7).lab is None


async def test_malformed_vlm_output_persists_no_partial_facts(tmp_path):
    ing, store = make_ingestor(tmp_path, StubVlm({LAB: open("tests/fixtures/w2/vlm_malformed.txt").read()}))
    res = await ing.ingest(PATIENT, 7, LAB, fx("lab_report.pdf"))
    assert res.record.error_code.value == "schema_invalid" and res.record.lab is None


async def test_failed_extraction_can_be_retried_without_reupload_row_clash(tmp_path):
    ing, store = make_ingestor(tmp_path, StubVlm(raises=VlmError("timeout")))
    await ing.ingest(PATIENT, 7, LAB, fx("lab_report.pdf"))
    ing2, _ = make_ingestor(tmp_path)
    res = await ing2.ingest(PATIENT, 7, LAB, fx("lab_report.pdf"))
    assert res.record.status.value == "extracted"


async def test_attach_and_extract_reads_a_file_path(tmp_path):
    ing, _ = make_ingestor(tmp_path)
    res = await attach_and_extract(ing, PATIENT, "tests/fixtures/w2/intake_form.pdf", INTAKE, 11)
    assert res.record.document_id == 11


# ------------------------------------------------------------------ PDF fallback chain
def test_pymupdf_forced_to_fail_still_renders_through_pypdfium2():
    out = render_pdf(fx("lab_report.pdf"), 5, [BrokenRenderer("pymupdf"), PdfiumRenderer()])
    assert out and out.renderer == "pypdfium2" and out.pages[0].startswith(b"\x89PNG")


async def test_extraction_is_identical_through_either_renderer(tmp_path):
    a, _ = make_ingestor(tmp_path / "a" if (tmp_path / "a").mkdir() is None else tmp_path, chain=[PyMuPDFRenderer()])
    b, _ = make_ingestor(tmp_path / "b" if (tmp_path / "b").mkdir() is None else tmp_path,
                         chain=[BrokenRenderer(), PdfiumRenderer()])
    ra = await a.ingest(PATIENT, 7, LAB, fx("lab_report.pdf"))
    rb = await b.ingest(PATIENT, 7, LAB, fx("lab_report.pdf"))
    assert ra.record.renderer == "pymupdf" and rb.record.renderer == "pypdfium2"
    # Same rows either way. Only the citation box differs: pdfium gives no line boxes, so it cites the whole page.
    strip = lambda lab: [r.model_copy(update={"citation": None}) for r in lab.rows]  # noqa: E731
    assert strip(ra.record.lab) == strip(rb.record.lab)
    assert all(r.citation.bbox != (0.0, 0.0, 1.0, 1.0) for r in ra.record.lab.rows)
    assert all(r.citation.bbox == (0.0, 0.0, 1.0, 1.0) for r in rb.record.lab.rows)


@pytest.mark.skipif(shutil.which("pdftoppm") is None, reason="pdftoppm (poppler-utils) not installed here")
def test_poppler_renders_when_first_two_fail():
    out = render_pdf(fx("lab_report.pdf"), 5, [BrokenRenderer("a"), EmptyRenderer("b"), PopplerRenderer()])
    assert out and out.renderer == "poppler"


async def test_all_renderers_failing_without_native_keeps_document_marked_render_failed(tmp_path):
    ing, store = make_ingestor(tmp_path, chain=[BrokenRenderer(), EmptyRenderer()], native=False)
    res = await ing.ingest(PATIENT, 7, LAB, fx("lab_report.pdf"))
    assert res.record.status.value == "extraction_failed" and res.record.error_code.value == "render_failed"
    assert res.record.lab is None


async def test_all_renderers_failing_uses_native_pdf_with_capped_confidence(tmp_path):
    vlm = stub()
    ing, _ = make_ingestor(tmp_path, vlm, chain=[BrokenRenderer()], native=True)
    res = await ing.ingest(PATIENT, 7, LAB, fx("lab_report.pdf"))
    assert vlm.calls == [(LAB, 0, True)] and res.record.render_mode.value == "native_pdf"
    assert all(r.confidence <= 0.5 and r.support.value == "unsupported" for r in res.record.lab.rows)


def test_render_attempt_log_carries_names_and_error_classes_only(caplog):
    caplog.set_level(logging.INFO)
    render_pdf(fx("lab_report.pdf"), 5, [BrokenRenderer("pymupdf"), PdfiumRenderer()])
    text = " ".join(str(getattr(r, "fields", "")) + r.getMessage() for r in caplog.records)
    assert "pymupdf" in text and "RuntimeError" in text
    assert "Hemoglobin" not in text and "SYNTHETIC" not in text


def test_truncated_pdf_is_not_silently_accepted():
    assert render_pdf(fx("truncated.pdf"), 5) is None
