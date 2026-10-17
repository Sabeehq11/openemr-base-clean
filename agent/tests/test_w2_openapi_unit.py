"""Contract test: docs/week2/openapi.yaml against the running app.

Scope is the Week 2 endpoints (/v1/w2/*) plus /health and /ready. Week 1 routes (/v1/chat, /v1/tools/*, /dashboard*)
are not in the spec and are ignored here on purpose. Guards the spec and the code drifting apart.
"""
import re
from pathlib import Path

import pytest
import yaml
from pydantic import SecretStr

from app.config import Settings
from app.main import create_app
from app.w2.api import LinksBody, PageBody, PreflightBody, RetrieveBody, UploadBody
from contracts.w2 import DocumentRecord
from contracts.w2.documents import DocStatus, ErrorCode

SPEC = yaml.safe_load((Path(__file__).resolve().parents[2] / "docs" / "week2" / "openapi.yaml").read_text())
EXTRA = {"/health", "/ready"}


def in_scope(path: str) -> bool:
    return path.startswith("/v1/w2/") or path in EXTRA


@pytest.fixture(scope="module")
def app(tmp_path_factory):
    s = Settings(data_dir=str(tmp_path_factory.mktemp("d")), llm_provider="stub", w2_enabled=True,
                 w2_use_stub_vlm=True, audit_hmac_key=SecretStr("k" * 32), module_signing_secret=SecretStr("s"))
    return create_app(s)


def app_operations(app) -> set[tuple[str, str]]:
    return {(m.lower(), r.path) for r in app.routes if in_scope(getattr(r, "path", ""))
            for m in getattr(r, "methods", set()) if m not in ("HEAD", "OPTIONS")}


def spec_operations() -> set[tuple[str, str]]:
    return {(m, p) for p, item in SPEC["paths"].items() for m in item if m in ("get", "post", "put", "delete")}


def resolve(node):
    """Follows a local $ref."""
    while isinstance(node, dict) and "$ref" in node:
        cur = SPEC
        for part in node["$ref"].removeprefix("#/").split("/"):
            cur = cur[part]
        node = cur
    return node


def request_schema(path: str, method: str = "post") -> dict:
    return resolve(SPEC["paths"][path][method]["requestBody"]["content"]["application/json"]["schema"])


def test_every_w2_route_is_documented_and_every_documented_path_exists(app):
    # guards: a new or renamed W2 route with no spec entry, or a spec entry for a route that is gone
    assert app_operations(app) == spec_operations()


def test_week1_routes_are_out_of_scope(app):
    # the spec must not grow Week 1 paths, and the test must not fail on them
    assert all(in_scope(p) for p in SPEC["paths"])
    assert any(r.path.startswith("/v1/chat") for r in app.routes if hasattr(r, "path"))


def test_every_ref_resolves_and_every_operation_has_an_id():
    ids = []
    for item in SPEC["paths"].values():
        for op in item.values():
            ids.append(op["operationId"])
            for p in op.get("parameters", []):
                resolve(p)
            for r in op["responses"].values():
                resolve(r)
    assert len(ids) == len(set(ids))
    assert SPEC["openapi"].startswith("3.0")


@pytest.mark.parametrize("path,model", [
    ("/v1/w2/preflight", PreflightBody), ("/v1/w2/documents", UploadBody),
    ("/v1/w2/documents/{document_id}/links", LinksBody), ("/v1/w2/retrieve", RetrieveBody),
    ("/v1/w2/documents/{document_id}/page", PageBody)])
def test_request_schema_matches_the_pydantic_model(path, model):
    # guards: a required field added to the model but not the spec (or the reverse), or a loosened extra=forbid
    spec = request_schema(path)
    schema = model.model_json_schema()
    assert set(spec["required"]) == set(schema["required"])
    assert set(spec["properties"]) == set(schema["properties"])
    assert spec.get("additionalProperties") is False
    for name, prop in spec["properties"].items():
        py = schema["properties"][name]
        for key in ("minimum", "minLength", "maxLength", "maxItems"):
            if key in py:
                assert resolve(prop).get(key) == py[key], f"{model.__name__}.{name}.{key}"


def test_chat_request_requires_patient_and_question():
    spec = request_schema("/v1/w2/chat")
    assert set(spec["required"]) == {"patient_uuid", "question"}


def test_document_record_schema_matches_the_contract():
    spec = resolve(SPEC["components"]["schemas"]["DocumentRecord"])
    schema = DocumentRecord.model_json_schema()
    assert set(spec["required"]) == set(schema["required"])
    assert set(spec["properties"]) == set(schema["properties"])
    assert set(spec["properties"]["status"]["enum"]) == {s.value for s in DocStatus}
    assert {e for e in spec["properties"]["error_code"]["enum"] if e} == {e.value for e in ErrorCode}


def test_documented_error_statuses_are_ones_the_handlers_return():
    # guards: a status code documented but never produced, or produced but undocumented
    src = (Path(__file__).resolve().parents[1] / "app" / "w2" / "api.py").read_text()
    produced = {int(c) for c in re.findall(r"status_code=(\d{3})", src)} | set(
        int(v) for v in re.findall(r"\b(4\d\d)\b", re.search(r"_REJECT_STATUS = \{[^}]*\}", src).group(0)))
    documented = {int(c) for item in SPEC["paths"].values() for op in item.values() for c in op["responses"]
                  if c.startswith("4") and c not in ("401", "429")}  # 401 and 429 come from the gateway
    assert documented == produced - {503}
    assert {"401", "429"} <= {c for c in SPEC["paths"]["/v1/w2/documents"]["post"]["responses"]}


def test_live_responses_use_the_documented_status_codes(app):
    from fastapi.testclient import TestClient

    c = TestClient(app)
    ready = c.get("/ready")
    assert str(ready.status_code) in SPEC["paths"]["/ready"]["get"]["responses"]
    assert ready.json()["status"] in SPEC["components"]["schemas"]["Readiness"]["properties"]["status"]["enum"]
    assert c.get("/health").json() == {"status": "ok"}
    unsigned = c.post("/v1/w2/retrieve", json={})
    assert str(unsigned.status_code) in SPEC["paths"]["/v1/w2/retrieve"]["post"]["responses"]
