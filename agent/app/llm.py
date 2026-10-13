"""LLM interface. The loop only knows `LLMClient`; tests use `StubLLM`, production uses `OpenRouterClient`."""
from __future__ import annotations

import asyncio
import json
import random
from dataclasses import dataclass, field
from typing import Protocol

import httpx

from . import tracing
from .config import Settings
from .logging_setup import outbound_headers

RETRY_BASE_S = 0.5  # backoff is RETRY_BASE_S plus up to the same again of jitter


class LLMError(Exception):
    """Provider unavailable or returned something unusable. Message is safe to show; never raw upstream text."""


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: dict


@dataclass(frozen=True)
class Usage:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost_usd: float = 0.0


@dataclass(frozen=True)
class LLMResponse:
    text: str | None = None
    tool_calls: list[ToolCall] = field(default_factory=list)
    usage: Usage = Usage()


Message = dict  # OpenAI-style chat message: {"role": ..., "content": ..., ...}


class LLMClient(Protocol):
    async def complete(self, messages: list[Message], tools: list[dict]) -> LLMResponse: ...


class StubLLM:
    """Deterministic scripted model. Each call pops the next scripted response; running dry is an error."""

    def __init__(self, script: list[LLMResponse | Exception]) -> None:
        self._script = list(script)
        self.calls: list[list[Message]] = []

    async def complete(self, messages: list[Message], tools: list[dict]) -> LLMResponse:
        self.calls.append([dict(m) for m in messages])
        if not self._script:
            raise LLMError("stub script exhausted")
        nxt = self._script.pop(0)
        if isinstance(nxt, Exception):
            raise nxt
        return nxt


class OpenRouterClient:
    """OpenAI-compatible chat completions via OpenRouter. The key stays server-side and is never logged."""

    def __init__(self, settings: Settings, transport: httpx.AsyncBaseTransport | None = None,
                 timeout_s: float = 30.0) -> None:
        self._s, self._transport, self._timeout = settings, transport, timeout_s

    async def complete(self, messages: list[Message], tools: list[dict]) -> LLMResponse:
        key = self._s.openrouter_api_key.get_secret_value()
        if not key:
            raise LLMError("language model is not configured")
        payload: dict = {"model": self._s.openrouter_model, "messages": messages, "temperature": 0,
                         "usage": {"include": True}}
        if tools:
            payload["tools"] = tools
        r = await self._post_with_retry(payload, key)
        if r.status_code != 200:
            raise LLMError("language model is unavailable")
        return self._parse(r)

    async def _post_with_retry(self, payload: dict, key: str) -> httpx.Response:
        """One retry, with jittered backoff, only when the provider said 'try again' (429, 5xx) or was never reached.
        A read timeout is not retried: the request may have been processed, and a second wait would blow the deadline."""
        for attempt in (1, 2):
            try:
                async with httpx.AsyncClient(timeout=self._timeout, transport=self._transport) as c:
                    r = await c.post(f"{self._s.openrouter_base_url}/chat/completions", json=payload,
                                     headers=outbound_headers({"Authorization": f"Bearer {key}"}))
            except (httpx.ConnectError, httpx.ConnectTimeout) as e:
                if attempt == 2:
                    raise LLMError("language model is unavailable") from e
            except httpx.HTTPError as e:
                raise LLMError("language model is unavailable") from e
            else:
                if r.status_code != 429 and r.status_code < 500:
                    return r
                if attempt == 2:
                    return r
            tracing.count_retry()
            await asyncio.sleep(RETRY_BASE_S + random.uniform(0, RETRY_BASE_S))
        raise LLMError("language model is unavailable")  # unreachable; keeps the type checker honest

    @staticmethod
    def _parse(r: httpx.Response) -> LLMResponse:
        try:
            body = r.json()
            msg = body["choices"][0]["message"]
            calls = [ToolCall(c["id"], c["function"]["name"], json.loads(c["function"].get("arguments") or "{}"))
                     for c in (msg.get("tool_calls") or [])]
            u = body.get("usage") or {}
            usage = Usage(int(u.get("prompt_tokens", 0)), int(u.get("completion_tokens", 0)),
                          float(u.get("cost", 0.0) or 0.0))
            return LLMResponse(text=msg.get("content"), tool_calls=calls, usage=usage)
        except (ValueError, KeyError, IndexError, TypeError) as e:
            raise LLMError("language model returned an unusable response") from e


class DemoStubLLM:
    """Stateless deterministic model for the no-network provider (`stub`). Reads the four core tools, then states
    each record verbatim with its source id. Used by e2e tests and local demos; `unavailable` simulates an outage."""

    CORE = ("get_problems", "get_medications", "get_allergies", "get_labs")

    async def complete(self, messages: list[Message], tools: list[dict]) -> LLMResponse:
        last_user = max((i for i, m in enumerate(messages) if m["role"] == "user"), default=-1)
        results = [m for m in messages[last_user + 1:] if m["role"] == "tool"]
        if not results and tools:
            return LLMResponse(tool_calls=[ToolCall(f"demo-{n}", n, {}) for n in self.CORE])
        claims: list[dict] = []
        for m in results:
            env = json.loads(m["content"])
            tool = env.get("tool", "")
            if "error" in env:
                claims.append({"text": f"Source unavailable: {env.get('unavailable', 'a tool')}.", "kind": "fact",
                               "source_ids": []})
            elif env.get("empty"):
                what = tool.removeprefix("get_")
                claims.append({"text": f"No {what} documented in the record.", "kind": "absence", "source_ids": []})
            else:
                for r in env["records"]:
                    label = r.get("title") or r.get("name") or r.get("substance") or r.get("test") or "record"
                    claims.append({"text": str(label), "kind": "fact", "source_ids": [r["source_id"]]})
        return LLMResponse(text=json.dumps({"claims": claims[:40]}), usage=Usage(50, 20, 0.0))


class UnavailableLLM:
    async def complete(self, messages: list[Message], tools: list[dict]) -> LLMResponse:
        raise LLMError("language model is unavailable")


def make_llm(settings: Settings) -> LLMClient:
    if settings.llm_provider == "openrouter":
        return OpenRouterClient(settings)
    if settings.llm_provider == "unavailable":
        return UnavailableLLM()
    return DemoStubLLM()
