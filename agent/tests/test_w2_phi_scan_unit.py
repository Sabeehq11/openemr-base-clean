"""End-to-end PHI scan: upload, chat and retrieve with stubs, then search every place a value could land.

The static scan (test_w2_phi_static_unit.py) reads the code. This one runs the flow and reads the output: the JSON log
lines, every row in traces.db (spans and their attrs included), the audit rows the routes wrote, and the eval result
files. It looks for the fixtures' synthetic names, dates of birth, phone numbers, quotes and lab lines, and for SSN,
email and phone shapes in general. No network, no golden set, no baseline.
"""
import base64
import json
import logging
import re
from pathlib import Path

from app.logging_setup import JsonFormatter, correlation_id_var, quiet_client_loggers
from app.llm import LLMError
from app.tracing import TraceStore
from tests.w2_chat_helpers import PATIENT, Q, TwoStageLLM, ask, build, post, stage_b
from tests.w2_helpers import fx

ROOT = Path(__file__).resolve().parents[2]
# What the fixtures hold. Specific strings, so a lab name or the word "diabetes" in a guideline title cannot trip it.
SENTINELS = ["Syntheticson", "1970-03-14", "555-010-0199", "Follow-up for diabetes", "Metformin 500 mg twice daily",
             "Lisinopril 10 mg daily", "Penicillin (rash)", "Father, type 2 diabetes", "Hemoglobin A1c   7.9",
             "Creatinine   1.8 mg/dL", "Glucose   142 mg/dL", "First name: Sam", "Date of birth", PATIENT, Q]
SHAPES = {
    "ssn": re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),
    "email": re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b"),
    "phone": re.compile(r"\b\(?\d{3}\)?[-. ]\d{3}[-. ]\d{4}\b"),
}


class Capture(logging.Handler):
    def __init__(self):
        super().__init__(logging.DEBUG)
        self.lines: list[str] = []

    def emit(self, record):
        self.lines.append(JsonFormatter().format(record))


def b64(name):
    return base64.b64encode(fx(name)).decode()


def problems(label: str, text: str, allow: tuple[str, ...] = ()) -> list[str]:
    found = [f"{label}: {s!r}" for s in SENTINELS if s in text and s not in allow]
    found += [f"{label}: {name} pattern {m.group(0)!r}" for name, rx in SHAPES.items() for m in rx.finditer(text)]
    return found


def dump_db(store: TraceStore) -> str:
    out = []
    for (table,) in store.query("SELECT name FROM sqlite_master WHERE type='table'"):
        out += [json.dumps(row, default=str) for row in store.query(f"SELECT * FROM {table}")]  # noqa: S608 (own table)
    return "\n".join(out)


def test_no_value_reaches_logs_traces_audit_or_eval_results(tmp_path):
    # guards: a document value, name, date of birth, phone or quote leaking into any stored or logged output
    store = TraceStore(str(tmp_path / "traces.db"))
    cap, root = Capture(), logging.getLogger()
    old_level = root.level
    root.addHandler(cap)
    root.setLevel(logging.DEBUG)
    token = correlation_id_var.set("corr-phi-0001")
    quiet_client_loggers()  # what setup_logging does at startup; httpx would otherwise log the FHIR URLs
    try:
        llm = TwoStageLLM(stage_b(("Hemoglobin A1c is 7.9 %.", ["doc:7#0"])))
        client, audit = build(tmp_path / "app", llm, traces=store)  # uploads the lab report as document 7
        r = post(client, "/v1/w2/documents", {"patient_uuid": PATIENT, "document_id": 8, "doc_type": "intake_form",
                                              "content_b64": b64("intake_form.pdf")})
        assert r.status_code == 200 and r.json()["document"]["intake"], "the intake fixture must extract"
        assert ask(client, Q).status_code == 200
        assert post(client, "/v1/w2/retrieve", {"patient_uuid": PATIENT, "question": "A1c testing frequency",
                                                "test_names": ["Hemoglobin A1c"]}).status_code == 200
        # A failing model and a denied caller are the paths where an error message could carry text.
        broken, _ = build(tmp_path / "broken", TwoStageLLM(LLMError("provider echoed Syntheticson 1970-03-14")),
                          traces=store)
        ask(broken, Q)
        post(broken, "/v1/w2/documents", {"patient_uuid": "99999999-8888-7777-6666-555555555555", "document_id": 9,
                                           "doc_type": "intake_form", "content_b64": b64("intake_form.pdf")})
    finally:
        correlation_id_var.reset(token)
        root.removeHandler(cap)
        root.setLevel(old_level)

    assert any('"w2_ingest_complete"' in line for line in cap.lines), "the flow must have logged something to scan"
    assert store.query("SELECT count(*) FROM traces WHERE flow IN ('w2_ingest','w2_retrieve','w2_chat')")[0][0] >= 4
    found = problems("logs", "\n".join(cap.lines))
    found += problems("traces.db", dump_db(store))
    # The audit trail names the patient on purpose (who accessed whom); everything else in it must still be clean.
    found += problems("audit", json.dumps(audit.events, default=str), allow=(PATIENT,))
    for path in sorted((ROOT / "evals" / "results").glob("w2-golden-latest.*")) + [ROOT / "evals" / "w2" / "baseline.json"]:
        found += problems(path.name, path.read_text())
    assert not found, "PHI-shaped content in output:\n" + "\n".join(found[:20])


def test_startup_logging_silences_http_client_url_logs():
    # guards: httpx's INFO request lines (with ?patient=<uuid>) coming back after a logging refactor
    from app.logging_setup import setup_logging

    root = logging.getLogger()
    saved, level = root.handlers[:], root.level
    try:
        setup_logging()
        assert logging.getLogger("httpx").getEffectiveLevel() >= logging.WARNING
        assert logging.getLogger("httpcore").getEffectiveLevel() >= logging.WARNING
    finally:
        root.handlers, root.level = saved, level


def test_client_and_access_loggers_cannot_emit_a_patient_query_string(caplog):
    # guards: `?patient=<uuid>` reaching a log through httpx (outbound FHIR) or uvicorn's access log (inbound GET)
    import logging

    from app.logging_setup import setup_logging

    setup_logging()
    sentinel = "99999999-aaaa-bbbb-cccc-000000000001"
    with caplog.at_level(logging.INFO):
        logging.getLogger("httpx").info('HTTP Request: GET /fhir/Observation?patient=%s "HTTP/1.1 200 OK"', sentinel)
        logging.getLogger("uvicorn.access").info('%s - "GET /v1/w2/documents/5?patient_uuid=%s HTTP/1.1" 200',
                                                 "127.0.0.1", sentinel)
    assert sentinel not in caplog.text


def test_agent_image_starts_uvicorn_without_the_access_log():
    # guards: the container being launched in a way that re-enables the access log before app code runs
    docker = (ROOT / "agent" / "Dockerfile").read_text()
    assert "--no-access-log" in docker
