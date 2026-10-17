"""Click-to-source page endpoint: POST /v1/w2/documents/{id}/page.

Guards: a page shown for a document that was not the one extracted, another patient's document, a page past the end,
and document content reaching logs or the audit sink.
"""
import base64
import logging

from app.w2.render import Rendered
from tests.test_w2_api_unit import build, call, upload_payload
from tests.w2_helpers import OTHER, PATIENT, fx


def page_payload(name="lab_report.pdf", page=1, patient=PATIENT, data=None):
    raw = fx(name) if data is None else data
    return {"patient_uuid": patient, "page": page, "content_b64": base64.b64encode(raw).decode()}


def stored(tmp_path, name="lab_report.pdf", doc_id=7, doc_type="lab_pdf"):
    c, audit, store = build(tmp_path)
    assert call(c, "POST", "/v1/w2/documents", upload_payload(name, doc_id, doc_type=doc_type)).status_code == 200
    return c, audit, store


def test_rebinding_a_duplicate_lets_the_new_document_id_render_its_page(tmp_path):
    c, _, store = stored(tmp_path)
    again = call(c, "POST", "/v1/w2/documents", upload_payload(doc_id=42))
    assert again.json()["duplicate"] is True and again.json()["document"]["document_id"] == 42
    page = call(c, "POST", "/v1/w2/documents/42/page", page_payload())
    assert page.status_code == 200 and base64.b64decode(page.json()["content_b64"]).startswith(b"\x89PNG")
    assert call(c, "POST", "/v1/w2/documents/7/page", page_payload()).status_code == 404
    assert store.get(PATIENT, 7) is None


def test_page_of_the_extracted_pdf_is_a_png(tmp_path):
    c, audit, _ = stored(tmp_path)
    r = call(c, "POST", "/v1/w2/documents/7/page", page_payload())
    assert r.status_code == 200
    body = r.json()
    assert body["page"] == 1 and body["total_pages"] >= 1 and body["mime"] == "image/png"
    assert base64.b64decode(body["content_b64"]).startswith(b"\x89PNG")
    assert audit.events[-1][0] == "w2_page"


def test_image_upload_is_returned_as_is_for_page_one_only(tmp_path):
    c, _, _ = stored(tmp_path, "lab_scan.png", 9)
    ok = call(c, "POST", "/v1/w2/documents/9/page", page_payload("lab_scan.png"))
    assert ok.status_code == 200 and ok.json()["mime"] == "image/png" and ok.json()["total_pages"] == 1
    assert call(c, "POST", "/v1/w2/documents/9/page", page_payload("lab_scan.png", page=2)).status_code == 404


def test_bytes_that_are_not_the_extracted_document_are_refused(tmp_path):
    """Guards showing document B's pixels under document A's citation."""
    c, _, _ = stored(tmp_path)
    r = call(c, "POST", "/v1/w2/documents/7/page", page_payload("intake_form.pdf"))
    assert r.status_code == 400 and r.json()["error"] == "invalid_request"


def test_another_patient_is_denied_and_an_unknown_document_is_not_found(tmp_path):
    c, _, _ = stored(tmp_path)
    assert call(c, "POST", "/v1/w2/documents/7/page", page_payload(patient=OTHER)).status_code == 403
    assert call(c, "POST", "/v1/w2/documents/999/page", page_payload()).status_code == 404


def test_bad_page_numbers_and_bodies_are_rejected(tmp_path):
    c, _, _ = stored(tmp_path)
    assert call(c, "POST", "/v1/w2/documents/7/page", page_payload(page=0)).status_code == 400
    assert call(c, "POST", "/v1/w2/documents/7/page", page_payload(page=51)).status_code == 400
    assert call(c, "POST", "/v1/w2/documents/7/page", page_payload(page=40)).status_code == 404  # past the last page
    assert call(c, "POST", "/v1/w2/documents/7/page", {**page_payload(), "content_b64": "***"}).status_code == 400
    assert call(c, "POST", "/v1/w2/documents/7/page", {**page_payload(), "extra": 1}).status_code == 400


def test_unsigned_request_is_401(tmp_path):
    c, _, _ = stored(tmp_path)
    assert c.post("/v1/w2/documents/7/page", json=page_payload()).status_code == 401


def test_when_no_renderer_works_the_page_is_not_found_not_an_error(tmp_path):
    from tests.w2_helpers import EmptyRenderer

    c, _, _ = stored(tmp_path)
    from app.w2 import render

    # the stored extraction exists; only rendering is unavailable
    orig = render.default_chain
    render.default_chain = lambda: [EmptyRenderer()]
    try:
        assert call(c, "POST", "/v1/w2/documents/7/page", page_payload()).status_code == 404
    finally:
        render.default_chain = orig


def test_logs_and_audit_carry_ids_only(tmp_path, caplog):
    c, audit, _ = stored(tmp_path)
    with caplog.at_level(logging.DEBUG):
        call(c, "POST", "/v1/w2/documents/7/page", page_payload())
    text = caplog.text + repr(audit.events)
    for leaked in ("Hemoglobin", "7.9", base64.b64encode(fx("lab_report.pdf"))[:40].decode()):
        assert leaked not in text
    assert "w2_page" in caplog.text


_: Rendered
