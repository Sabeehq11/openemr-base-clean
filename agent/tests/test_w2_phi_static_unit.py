"""Static PHI checks, a second line behind the `no_phi_in_logs` rubric.

The rubric only sees the log lines a golden case happens to produce. These scans read the code, so a logging call on a
path no case reaches is still caught. Two scans:
  1. every logging call in agent/app/w2 uses a constant message and fields that are codes, counts or enums, never a
     field that holds document text, a quote, a value, a name, an identifier or an exception message;
  2. the golden set, fixtures and results contain only synthetic data: no SSN, email or phone outside the fiction ranges.
"""
import ast
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
W2 = ROOT / "agent" / "app" / "w2"
LOG_METHODS = {"debug", "info", "warning", "error", "critical", "exception", "log"}

# Field names that hold or imply patient or document content. Listed, not guessed: a new one is a conscious choice.
FORBIDDEN_FIELDS = {
    "text", "quote", "value", "values", "name", "first_name", "last_name", "patient", "patient_id", "patient_uuid",
    "uuid", "filename", "file", "path", "content", "body", "raw", "question", "claim", "claims", "snippet",
    "snippets", "message", "detail", "details", "exc", "exception", "error_message", "traceback", "stack", "data",
    "payload", "sample", "dob", "phone", "email", "address", "mrn", "token", "authorization", "exc_info",
}
# A value built by one of these calls can carry content. `error_code(e)` and `type(e).__name__` are the safe forms.
FORBIDDEN_CALLS = {"str", "repr", "format", "dumps", "model_dump", "model_dump_json", "join"}
# Reviewed and allowed: (file, event message, field) -> reason. Starts empty; every entry needs a reason.
ALLOW: dict[tuple[str, str, str], str] = {}


def _is_log_call(node: ast.Call) -> bool:
    f = node.func
    if isinstance(f, ast.Name) and f.id == "log_event":
        return True
    return (isinstance(f, ast.Attribute) and f.attr in LOG_METHODS and isinstance(f.value, ast.Name)
            and f.value.id in {"logger", "log", "logging"})


def _value_problem(v: ast.expr) -> str | None:
    """Why a field's value could carry content, or None when it is a plain code, count or enum."""
    if isinstance(v, ast.JoinedStr):
        return "an f-string"
    if isinstance(v, ast.BinOp) and isinstance(v.op, ast.Mod):
        return "a %-formatted string"
    for n in ast.walk(v):
        if isinstance(n, ast.Call):
            name = n.func.id if isinstance(n.func, ast.Name) else n.func.attr if isinstance(n.func, ast.Attribute) else ""
            if name in FORBIDDEN_CALLS:
                return f"a call to {name}()"
        if isinstance(n, ast.Attribute) and n.attr in {"args", "message", "text", "quote", "detail"}:
            return f"the attribute .{n.attr}"
    return None


def scan_source(src: str, filename: str) -> list[str]:
    """Problems with the logging calls in one source file. Pure, so a test can feed it a bad snippet."""
    problems: list[str] = []
    for node in ast.walk(ast.parse(src)):
        if not (isinstance(node, ast.Call) and _is_log_call(node)):
            continue
        msg_index = 1 if isinstance(node.func, ast.Name) else 0
        where = f"{filename}:{node.lineno}"
        msg = node.args[msg_index] if len(node.args) > msg_index else None
        if not (isinstance(msg, ast.Constant) and isinstance(msg.value, str)):
            problems.append(f"{where}: the log message must be a constant string, not built from data")
            continue
        if len(node.args) > msg_index + 1:
            problems.append(f"{where}: positional %-arguments in a log call; use named code or count fields")
        if isinstance(node.func, ast.Attribute) and node.func.attr == "exception":
            problems.append(f"{where}: logger.exception writes the traceback and message; log error_code(e) instead")
        for kw in node.keywords:
            if (filename, msg.value, kw.arg or "**") in ALLOW:
                continue
            if kw.arg is None:
                problems.append(f"{where}: **kwargs in a log call hides which fields are written")
            elif kw.arg in FORBIDDEN_FIELDS:
                problems.append(f"{where}: field '{kw.arg}' may hold patient or document content")
            elif (why := _value_problem(kw.value)) is not None:
                problems.append(f"{where}: field '{kw.arg}' is built from {why}")
    return problems


def test_no_w2_logging_call_can_carry_document_or_patient_content():
    # guards: a log field or message built from text, quotes, values, names, identifiers or exception messages
    problems: list[str] = []
    files = sorted(W2.rglob("*.py"))
    assert len(files) >= 15, "the scan must be looking at the real source tree"
    for f in files:
        problems += scan_source(f.read_text(), str(f.relative_to(ROOT / "agent")))
    assert problems == [], "\n".join(problems)


def test_the_scan_really_finds_the_leaks_it_is_for():  # guards: a scan that cannot fail
    bad = '''
import logging
from app.logging_setup import log_event
logger = logging.getLogger("agent.w2.x")
def f(row, patient, e):
    log_event(logger, "w2_extract", value=row.value)
    log_event(logger, "w2_extract", status=str(e))
    log_event(logger, f"failed for {patient}")
    log_event(logger, "w2_extract", why=f"bad {row}")
    logger.info("failed %s", patient)
    logger.exception("boom")
    logger.error("x", exc_info=True)
    log_event(logger, "w2_extract", info=e.args)
    log_event(logger, "w2_extract", **row)
'''
    found = scan_source(bad, "bad.py")
    for needle in ("field 'value'", "call to str()", "constant string", "f-string", "positional", "logger.exception",
                   "field 'exc_info'", "attribute .args", "**kwargs"):
        assert any(needle in p for p in found), (needle, found)


def test_the_scan_accepts_the_safe_forms_in_use():  # guards: a scan so strict it gets ignored
    ok = '''
import logging
from app.logging_setup import log_event
logger = logging.getLogger("agent.w2.x")
def f(e, results, n):
    log_event(logger, "w2_extract", status="ok", document_id=3, facts=len(results))
    log_event(logger, "w2_extract", error=error_code(e), step=n)
    log_event(logger, "w2_worker_raised", error_code=type(e).__name__, worker=n.value)
'''
    assert scan_source(ok, "ok.py") == []


# ---- data scan --------------------------------------------------------------------------------------------------

SSN = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")
EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
PHONE = re.compile(r"\b\d{3}[-.\s]\d{3}[-.\s]\d{4}\b")
SYNTHETIC_EMAIL_DOMAINS = {"example.com", "example.org", "example.test", "test.invalid"}


def _real_looking(text: str) -> list[str]:
    hits = SSN.findall(text)
    hits += [m for m in PHONE.findall(text) if not m.startswith("555-01")]  # the 555-01xx range is reserved for fiction
    hits += [m for m in EMAIL.findall(text) if m.rsplit("@", 1)[1].lower() not in SYNTHETIC_EMAIL_DOMAINS]
    return hits


def test_golden_set_and_results_hold_only_synthetic_data():
    # guards: a real SSN, phone number or email entering the committed evaluation data
    targets = list((ROOT / "evals" / "w2").rglob("*.json*")) + list((ROOT / "evals" / "results").glob("w2-golden*"))
    targets += list((ROOT / "agent" / "tests" / "fixtures" / "w2").glob("*.json"))
    assert len(targets) >= 8
    found = {str(p.relative_to(ROOT)): h for p in targets if (h := _real_looking(p.read_text(errors="ignore")))}
    assert found == {}


def test_the_data_scan_really_flags_real_looking_values():  # guards: a data scan that cannot fail
    assert _real_looking("ssn 123-45-6789") == ["123-45-6789"]
    assert _real_looking("call 212-555-0143") == ["212-555-0143"]
    assert _real_looking("mail jane.doe@gmail.com") == ["jane.doe@gmail.com"]
    assert _real_looking("Phone: 555-010-0199 and a@example.com") == []


def test_every_golden_case_is_valid_json_without_a_real_patient_id():
    # guards: a malformed line silently dropped, or a patient id placed in a case
    for f in (ROOT / "evals" / "w2" / "golden").glob("*.jsonl"):
        for i, line in enumerate(f.read_text().splitlines(), 1):
            case = json.loads(line)
            assert case["id"] and "patient_uuid" not in case, f"{f.name}:{i}"
