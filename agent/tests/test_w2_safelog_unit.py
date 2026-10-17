"""Caught exceptions are logged as codes or class names, never as their text."""
import json
import logging

from app.logging_setup import JsonFormatter
from app.w2.render import RenderError
from app.w2.safelog import error_code
from app.w2.supervisor import Supervisor
from app.w2.vlm import VlmError
from contracts.w2 import Node, WorkerResult, WorkerStatus

SENTINEL = "Jane Q Patient glucose 182 mg/dL"


def test_short_codes_pass_through_and_free_text_becomes_the_class_name():  # guards: exception text reaching a log
    assert error_code(VlmError("http_503")) == "http_503"
    assert error_code(RenderError("timeout")) == "timeout"
    assert error_code(ValueError(SENTINEL)) == "ValueError"
    assert error_code(VlmError("Jane: 182")) == "VlmError"
    assert error_code(RenderError("RuntimeError")) == "RuntimeError"  # RenderError carries the underlying class name
    assert error_code(ValueError("http_503")) == "ValueError"  # a foreign exception is never trusted to hold a code
    assert error_code(ValueError("Smith")) == "ValueError"
    assert error_code(KeyError("")) == "KeyError"


async def test_raising_worker_logs_type_only_no_message_or_traceback(caplog):  # guards: PHI in a worker error log
    async def boom():
        raise ValueError(SENTINEL)

    async def ok():
        return WorkerResult(worker=Node.EVIDENCE_RETRIEVER, status=WorkerStatus.OK)

    nodes = {Node.INTAKE_EXTRACTOR: boom, Node.CHART_FACTS: ok, Node.EVIDENCE_RETRIEVER: ok}
    with caplog.at_level(logging.INFO):
        out = await Supervisor(nodes, lambda s: True).run()
    lines = [JsonFormatter().format(r) for r in caplog.records]
    assert SENTINEL not in "\n".join(lines) and "Traceback" not in "\n".join(lines)
    raised = [json.loads(x) for x in lines if '"w2_worker_raised"' in x]
    assert raised and raised[0]["error_code"] == "ValueError"
    assert out.state.result_for(Node.INTAKE_EXTRACTOR).error_code == "worker_error"


async def test_vlm_failure_logs_the_code_not_the_text(tmp_path, caplog):  # guards: PHI in an extraction error log
    from contracts.w2 import DocType
    from tests.w2_helpers import PATIENT, fx, make_ingestor

    class Down:
        model = "stub"

        async def extract(self, doc_type, pages, native_pdf):
            raise VlmError("http_503")

    ing, _ = make_ingestor(tmp_path, vlm=Down())
    with caplog.at_level(logging.INFO):
        await ing.ingest(PATIENT, 1, DocType.LAB_PDF, fx("lab_report.pdf"))
    events = [json.loads(JsonFormatter().format(r)) for r in caplog.records if r.getMessage() == "w2_extract"]
    assert events and events[0]["error"] == "http_503"
