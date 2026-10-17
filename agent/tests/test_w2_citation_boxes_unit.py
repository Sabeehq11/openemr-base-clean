"""Citation boxes come from the PDF's own text, never from the model. Each test names the failure it guards."""
import pymupdf

from app.w2.render import PyMuPDFRenderer
from app.w2.support import PAGE_BOX, build_citation, verified_bbox
from contracts.w2.extraction import RawEvidence
from tests.test_w2_ingest_unit import LAB, PATIENT
from tests.w2_helpers import fx, make_ingestor


def pdf(lines_by_page, rotation=0):
    doc = pymupdf.open()
    for lines in lines_by_page:
        page = doc.new_page()
        for i, text in enumerate(lines):
            page.insert_text((72, 100 + 30 * i), text, fontsize=11)
        if rotation:
            page.set_rotation(rotation)
    return doc.tobytes()


def text_lines(data):
    return PyMuPDFRenderer().render(data, 5).lines


def ev(quote, page=1, bbox=(0.1, 0.5, 0.6, 0.6)):
    return RawEvidence(page=page, quote=quote, bbox=bbox)


def test_renderer_reports_line_boxes_that_match_the_printed_lines():
    got = {ln.text.split()[0]: ln.bbox for ln in text_lines(fx("lab_report.pdf"))}
    want = (0.082, 0.206, 0.373, 0.225)  # the Hemoglobin A1c line, measured from the file
    assert all(abs(a - b) < 0.005 for a, b in zip(got["Hemoglobin"], want))


def test_model_box_is_replaced_by_the_verified_line_box():
    lines = text_lines(fx("lab_report.pdf"))
    wrong = (0.077, 0.289, 0.391, 0.307)  # what the live model returned: the row below
    cite = build_citation(7, ev("Creatinine  1.8 mg/dL  0.6-1.3  H", bbox=wrong), "rows[1]", lines)
    assert cite.bbox != wrong
    assert abs(cite.bbox[1] - 0.236) < 0.005 and abs(cite.bbox[3] - 0.255) < 0.005


def test_unverifiable_box_falls_back_to_the_whole_page_not_the_models_guess():
    assert build_citation(7, ev("Creatinine 1.8 mg/dL"), "rows[1]", ()).bbox == PAGE_BOX  # scan or image


def test_a_quote_not_on_any_line_gets_the_page_box():
    lines = text_lines(fx("lab_report.pdf"))
    assert verified_bbox("Sodium 140 mmol/L", 1, lines) is None
    assert build_citation(7, ev("Sodium 140 mmol/L"), "rows[0]", lines).bbox == PAGE_BOX


def test_a_repeated_line_is_ambiguous_so_no_box_is_picked():
    lines = text_lines(pdf([["Potassium 4.1 mmol/L", "Potassium 4.1 mmol/L"]]))
    assert len(lines) == 2 and verified_bbox("Potassium 4.1 mmol/L", 1, lines) is None


def test_the_same_line_on_another_page_does_not_lend_its_box():
    lines = text_lines(pdf([["Page one text"], ["Potassium 4.1 mmol/L"]]))
    assert verified_bbox("Potassium 4.1 mmol/L", 1, lines) is None
    assert verified_bbox("Potassium 4.1 mmol/L", 2, lines) is not None


def test_a_quote_split_across_lines_is_not_given_one_lines_box():
    lines = text_lines(pdf([["Potassium", "4.1 mmol/L"]]))
    assert verified_bbox("Potassium 4.1 mmol/L", 1, lines) is None


def test_a_rotated_page_gives_no_boxes_because_they_could_be_wrong():
    assert text_lines(pdf([["Potassium 4.1 mmol/L"]], rotation=90)) == ()


def test_no_quote_or_no_model_region_still_means_no_citation():
    assert build_citation(7, ev(""), "f", ()) is None
    assert build_citation(7, RawEvidence(page=1, quote="Potassium 4.1", bbox=None), "f", ()) is None


async def test_full_ingest_stores_the_pdfs_own_boxes_for_every_row(tmp_path):
    ing, store = make_ingestor(tmp_path, chain=[PyMuPDFRenderer()])
    await ing.ingest(PATIENT, 7, LAB, fx("lab_report.pdf"))
    truth = {ln.text.split()[0]: ln.bbox for ln in text_lines(fx("lab_report.pdf"))}
    for row in store.get(PATIENT, 7).lab.rows:
        assert row.citation.bbox == truth[row.test_name.split()[0]]
