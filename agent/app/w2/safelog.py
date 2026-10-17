"""One way to log a caught exception without its text.

Exception messages can carry document values, so a message is logged only when it is known to be a code. W2's own
error types (VlmError, RenderError, RerankError, ExtractionError, ...) raise short codes by construction, such as
"schema_invalid", "http_503" or the name of an underlying exception class. Their message passes through if it is a
single identifier. Every other exception is reduced to its class name.
"""
from __future__ import annotations

import re

_CODE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,39}$")
_OWN_PACKAGE = "app.w2"


def error_code(exc: BaseException) -> str:
    """A loggable code for the exception: its message for a W2 error type with a code-shaped message, else its class."""
    cls = type(exc)
    text = str(exc)
    if cls.__module__.startswith(_OWN_PACKAGE) and _CODE.fullmatch(text):
        return text
    return cls.__name__
