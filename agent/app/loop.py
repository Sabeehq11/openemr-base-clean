"""Explicit tool-calling loop: at most 6 tool steps and 20 s, one patient, read-only tools only.

It yields events and ends in exactly one terminal event:
  done      -> an unverified AnswerDraft plus every envelope fetched (the verifier runs next)
  degraded  -> the model was unavailable; raw envelopes only, no prose
  denied    -> authorization refused; nothing else is revealed
  failed    -> the model never produced a valid structure
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import AsyncIterator

from pydantic import ValidationError

from contracts import AnswerDraft, ToolError, ToolName

from .conversation import Conversation
from .llm import LLMClient, LLMError, LLMResponse, Usage
from .prompts import RETRY_PROMPT, SYSTEM_PROMPT, TOOL_SPECS, model_view
from .tools import DeniedError, ToolRunner

MAX_STEPS = 6
DEADLINE_S = 20.0
_TOOLS = {t.value: t for t in ToolName}


@dataclass(frozen=True)
class Caller:
    user_uuid: str
    token: str


def parse_draft(text: str | None) -> AnswerDraft | None:
    if not text:
        return None
    s = text.strip()
    if s.startswith("```"):
        s = s.strip("`").removeprefix("json").strip()
    try:
        return AnswerDraft.model_validate(json.loads(s))
    except (ValueError, ValidationError):
        return None


class AgentLoop:
    def __init__(self, runner: ToolRunner, llm: LLMClient, clock=time.monotonic,
                 max_steps: int = MAX_STEPS, deadline_s: float = DEADLINE_S) -> None:
        self._runner, self._llm, self._clock = runner, llm, clock
        self._max_steps, self._deadline = max_steps, deadline_s

    async def run(self, conv: Conversation, caller: Caller, question: str) -> AsyncIterator[dict]:
        start, usage = self._clock(), Usage()
        try:  # every turn starts with a fresh decision, so nothing cached from an earlier turn can be shown without it
            await self._runner.check(ToolName.GET_PATIENT_SUMMARY, caller.token, conv.patient_uuid, caller.user_uuid)
        except DeniedError:
            yield {"type": "denied"}
            return
        except ToolError:
            yield {"type": "denied"}  # an unverifiable decision is a denial
            return
        conv.messages.append({"role": "user", "content": question})
        steps, retried, limit_note = 0, False, None
        while True:
            over = steps >= self._max_steps or self._clock() - start > self._deadline
            msgs = [{"role": "system", "content": SYSTEM_PROMPT}, *conv.messages]
            if over:
                limit_note = "Tool step or time limit reached; some sources may not have been retrieved."
                msgs.append({"role": "system", "content": limit_note + " Answer now with what you have."})
            try:
                resp: LLMResponse = await self._llm.complete(msgs, [] if over else TOOL_SPECS)
            except LLMError:
                yield {"type": "degraded", "envelopes": conv.snapshot.fresh(), "usage": usage}
                return
            usage = Usage(usage.prompt_tokens + resp.usage.prompt_tokens,
                          usage.completion_tokens + resp.usage.completion_tokens, usage.cost_usd + resp.usage.cost_usd)
            if resp.tool_calls and not over:
                conv.messages.append({"role": "assistant", "content": resp.text, "tool_calls": [
                    {"id": c.id, "type": "function",
                     "function": {"name": c.name, "arguments": json.dumps(c.arguments)}} for c in resp.tool_calls]})
                for call in resp.tool_calls:
                    steps += 1
                    result = await self._tool(conv, caller, call.name, call.arguments)
                    if isinstance(result, dict):
                        yield result
                        if result["type"] == "denied":
                            return
                        result = result["content"]
                    yield {"type": "step", "tool": call.name}
                    conv.messages.append({"role": "tool", "tool_call_id": call.id, "content": result})
                continue
            draft = parse_draft(resp.text)
            if draft is None:
                if retried:
                    yield {"type": "failed", "usage": usage}
                    return
                retried = True
                conv.messages.append({"role": "user", "content": RETRY_PROMPT})
                continue
            conv.messages.append({"role": "assistant", "content": resp.text})
            notes = [limit_note] if limit_note else []
            yield {"type": "done", "draft": draft, "envelopes": conv.snapshot.fresh(), "usage": usage,
                   "notes": notes, "steps": steps}
            return

    async def _tool(self, conv: Conversation, caller: Caller, name: str, args: dict):
        tool = _TOOLS.get(name)
        if tool is None or not isinstance(args, dict):
            return json.dumps({"error": "unknown_tool"})
        args = {k: v for k, v in args.items() if k == "since"}
        try:
            cached = conv.snapshot.get(tool, args)
            if cached is not None:  # a cache hit still re-runs the authorization decision
                await self._runner.check(tool, caller.token, conv.patient_uuid, caller.user_uuid)
                env = cached
            else:
                env = await self._runner.run(tool, caller.token, conv.patient_uuid, caller.user_uuid, **args)
                conv.snapshot.put(tool, args, env)
        except DeniedError:
            return {"type": "denied", "content": ""}
        except ToolError as e:
            return json.dumps({"error": e.code, "message": e.message, "unavailable": name})
        return model_view(env)
