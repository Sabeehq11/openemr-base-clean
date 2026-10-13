"""Structured JSON logging. Every line carries the correlation ID; PHI-like values are redacted."""
from __future__ import annotations

import contextvars
import json
import logging
import re
import sys
import time

correlation_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("correlation_id", default="-")

# Values that must never reach a log line: bearer tokens, API keys, SSNs, emails, phone numbers.
_REDACTIONS = [
    (re.compile(r"(?i)bearer\s+[a-z0-9._\-~+/]+=*"), "Bearer [REDACTED]"),
    (re.compile(r"sk-[A-Za-z0-9_\-]{10,}"), "[REDACTED_KEY]"),
    (re.compile(r"\b\d{3}-\d{2}-\d{4}\b"), "[REDACTED_SSN]"),
    (re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+"), "[REDACTED_EMAIL]"),
    (re.compile(r"\b\d{3}[-.\s]\d{3}[-.\s]\d{4}\b"), "[REDACTED_PHONE]"),
]
_SENSITIVE_KEYS = {"authorization", "access_token", "refresh_token", "api_key", "password", "secret", "token"}


def redact(value: object) -> object:
    if isinstance(value, str):
        for pattern, repl in _REDACTIONS:
            value = pattern.sub(repl, value)
        return value
    if isinstance(value, dict):
        return {
            k: ("[REDACTED]" if str(k).lower() in _SENSITIVE_KEYS else redact(v)) for k, v in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [redact(v) for v in value]
    return value


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(record.created)) + f".{int(record.msecs):03d}Z",
            "level": record.levelname,
            "logger": record.name,
            "correlation_id": correlation_id_var.get(),
            "msg": redact(record.getMessage()),
        }
        extra = getattr(record, "fields", None)
        if isinstance(extra, dict):
            payload.update(redact(extra))  # type: ignore[arg-type]
        if record.exc_info:
            payload["exc_type"] = record.exc_info[0].__name__ if record.exc_info[0] else None
        return json.dumps(payload, default=str)


def setup_logging(level: int = logging.INFO) -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level)


def log_event(logger: logging.Logger, msg: str, **fields: object) -> None:
    logger.info(msg, extra={"fields": fields})


CORRELATION_HEADER = "x-correlation-id"


def outbound_headers(extra: dict[str, str] | None = None) -> dict[str, str]:
    """Headers for every call the agent makes to OpenEMR or the model provider.

    Adds the current request's correlation ID (a random UUID, never PHI) so a trace can be followed across services.
    Outside a request the ID is "-" and no header is added.
    """
    headers = dict(extra or {})
    cid = correlation_id_var.get()
    if cid and cid != "-":
        headers[CORRELATION_HEADER] = cid
    return headers
