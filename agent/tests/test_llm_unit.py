import json

import httpx
import pytest
from pydantic import SecretStr

from app.config import Settings
from app.llm import LLMError, OpenRouterClient, StubLLM, make_llm, DemoStubLLM, UnavailableLLM

KEY = "sk-or-test-key-0123456789"


def client(handler, key=KEY):
    s = Settings(openrouter_api_key=SecretStr(key), openrouter_base_url="http://or", openrouter_model="m/x")
    return OpenRouterClient(s, transport=httpx.MockTransport(handler))


def ok(content=None, tool_calls=None, usage=None):
    msg = {"role": "assistant", "content": content}
    if tool_calls:
        msg["tool_calls"] = tool_calls
    return httpx.Response(200, json={"choices": [{"message": msg}], "usage": usage or {}})


async def test_parses_text_and_usage_and_sends_key_server_side_only():
    seen = {}

    def h(req):
        seen["auth"], seen["body"] = req.headers["authorization"], json.loads(req.content)
        return ok("hello", usage={"prompt_tokens": 7, "completion_tokens": 3, "cost": 0.002})

    r = await client(h).complete([{"role": "user", "content": "hi"}], [])
    assert r.text == "hello" and (r.usage.prompt_tokens, r.usage.completion_tokens, r.usage.cost_usd) == (7, 3, 0.002)
    assert seen["auth"] == f"Bearer {KEY}" and seen["body"]["model"] == "m/x" and "tools" not in seen["body"]


async def test_parses_tool_calls():
    tc = [{"id": "c1", "type": "function", "function": {"name": "get_labs", "arguments": '{"since":"2026-01-01"}'}}]
    r = await client(lambda req: ok(tool_calls=tc)).complete([], [{"x": 1}])
    assert r.tool_calls[0].name == "get_labs" and r.tool_calls[0].arguments == {"since": "2026-01-01"}


@pytest.mark.parametrize("resp", [httpx.Response(500, text="secret upstream detail"), httpx.Response(429),
                                  httpx.Response(200, text="<html>"), httpx.Response(200, json={"choices": []})])
async def test_failures_become_safe_llm_error(resp):
    with pytest.raises(LLMError) as e:
        await client(lambda req: resp).complete([], [])
    assert "secret" not in str(e.value) and KEY not in str(e.value)


async def test_network_error_is_llm_error():
    def h(req):
        raise httpx.ConnectError("boom")

    with pytest.raises(LLMError):
        await client(h).complete([], [])


async def test_missing_key_fails_without_network():
    with pytest.raises(LLMError):
        await client(lambda r: pytest.fail("no request expected"), key="").complete([], [])


async def test_stub_runs_dry_with_error():
    with pytest.raises(LLMError):
        await StubLLM([]).complete([], [])


def test_provider_selection():
    assert isinstance(make_llm(Settings(llm_provider="stub")), DemoStubLLM)
    assert isinstance(make_llm(Settings(llm_provider="unavailable")), UnavailableLLM)
    assert isinstance(make_llm(Settings(llm_provider="openrouter")), OpenRouterClient)


# --- retry: measured need, see docs/PERFORMANCE.md (5% provider 429s became 5% degraded answers) ---

@pytest.fixture
def fast_retry(monkeypatch):
    monkeypatch.setattr("app.llm.RETRY_BASE_S", 0.0)


@pytest.mark.parametrize("bad", [429, 500, 503])
async def test_one_retry_on_429_and_5xx_then_succeeds(fast_retry, bad):
    from app import tracing
    calls = []

    def h(req):
        calls.append(1)
        return httpx.Response(bad) if len(calls) == 1 else ok("fine")

    trace, tok = tracing.start("cid-retry-0001", "m/x")
    try:
        r = await client(h).complete([], [])
    finally:
        tracing.end(tok)
    assert r.text == "fine" and len(calls) == 2 and trace.retries == 1


async def test_retry_is_only_once(fast_retry):
    calls = []

    def h(req):
        calls.append(1)
        return httpx.Response(429)

    with pytest.raises(LLMError):
        await client(h).complete([], [])
    assert len(calls) == 2


@pytest.mark.parametrize("status", [400, 401, 404])
async def test_no_retry_on_client_errors(fast_retry, status):
    calls = []

    def h(req):
        calls.append(1)
        return httpx.Response(status)

    with pytest.raises(LLMError):
        await client(h).complete([], [])
    assert len(calls) == 1


async def test_read_timeout_is_not_retried(fast_retry):
    calls = []

    def h(req):
        calls.append(1)
        raise httpx.ReadTimeout("slow", request=req)

    with pytest.raises(LLMError):
        await client(h).complete([], [])
    assert len(calls) == 1


async def test_connect_error_is_retried_once(fast_retry):
    calls = []

    def h(req):
        calls.append(1)
        if len(calls) == 1:
            raise httpx.ConnectError("refused", request=req)
        return ok("back")

    assert (await client(h).complete([], [])).text == "back" and len(calls) == 2
