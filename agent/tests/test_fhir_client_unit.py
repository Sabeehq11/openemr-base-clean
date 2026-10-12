import httpx
import pytest

from app.config import Settings
from app.fhir_client import FhirClient
from contracts import ToolError

BUNDLE = {"resourceType": "Bundle", "entry": [
    {"resource": {"resourceType": "Condition", "id": "1"}},
    {"resource": {"resourceType": "OperationOutcome", "id": "x"}},
]}


def client(handler):
    s = Settings(openemr_base_url="http://oe", openemr_verify_tls=False, tool_timeout_s=1)
    return FhirClient(s, transport=httpx.MockTransport(handler))


async def test_search_returns_only_requested_resource_type():
    c = client(lambda req: httpx.Response(200, json=BUNDLE))
    rows = await c.search("Condition", "tok", {"patient": "p"})
    assert [r["id"] for r in rows] == ["1"]


async def test_search_sends_bearer_token_and_only_get():
    seen = []

    def h(req):
        seen.append((req.method, req.headers["authorization"]))
        return httpx.Response(200, json=BUNDLE)

    await client(h).search("Condition", "tok", {"patient": "p"})
    assert seen == [("GET", "Bearer tok")]
    assert not hasattr(FhirClient, "post") and not hasattr(FhirClient, "put") and not hasattr(FhirClient, "delete")


async def test_one_retry_on_5xx_then_success():
    calls = []

    def h(req):
        calls.append(1)
        return httpx.Response(503) if len(calls) == 1 else httpx.Response(200, json=BUNDLE)

    assert len(await client(h).search("Condition", "t", {})) == 1
    assert len(calls) == 2


async def test_gives_up_after_one_retry_with_typed_error():
    calls = []

    def h(req):
        calls.append(1)
        return httpx.Response(500)

    with pytest.raises(ToolError) as e:
        await client(h).search("Condition", "t", {})
    assert e.value.code == "upstream_error" and e.value.retryable and len(calls) == 2


async def test_timeout_is_typed_and_retried_once():
    calls = []

    def h(req):
        calls.append(1)
        raise httpx.ReadTimeout("slow")

    with pytest.raises(ToolError) as e:
        await client(h).search("Condition", "t", {})
    assert e.value.code == "timeout" and len(calls) == 2


@pytest.mark.parametrize("status", [401, 403])
async def test_auth_failure_is_not_retried(status):
    calls = []

    def h(req):
        calls.append(1)
        return httpx.Response(status)

    with pytest.raises(ToolError) as e:
        await client(h).search("Condition", "t", {})
    assert e.value.code == "unauthorized" and len(calls) == 1


async def test_malformed_body_is_bad_response():
    with pytest.raises(ToolError) as e:
        await client(lambda r: httpx.Response(200, content=b"<html>")).search("Condition", "t", {})
    assert e.value.code == "bad_response"


async def test_wrong_shape_is_bad_response():
    with pytest.raises(ToolError) as e:
        await client(lambda r: httpx.Response(200, json={"resourceType": "Patient"})).search("Condition", "t", {})
    assert e.value.code == "bad_response"


async def test_error_message_never_contains_upstream_text():
    with pytest.raises(ToolError) as e:
        await client(lambda r: httpx.Response(500, text="SQLSTATE secret at /var/www")).search("Condition", "t", {})
    assert "SQLSTATE" not in e.value.message


async def test_read_wrong_resource_type_is_bad_response():
    with pytest.raises(ToolError) as e:
        await client(lambda r: httpx.Response(200, json={"resourceType": "Condition"})).read("Patient", "p", "t")
    assert e.value.code == "bad_response"
