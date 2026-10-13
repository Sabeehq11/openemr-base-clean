"""Local mock of OpenRouter chat completions, for the `mock` data source. It is a SIMULATION, not a measurement.

Behaviour copies what the real model did in calibration (loadtest/mock_profile.json): turn 1 asks for the four core
tools, turn 2 returns claims. Latency and token counts come from the profile; cost is always 0 so mock runs can never
feed the cost analysis.   agent/.venv/bin/python -m loadtest.mock_openrouter [--error-rate 0.05]
"""
from __future__ import annotations

import argparse
import asyncio
import json
import random
import sys
from pathlib import Path

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "agent"))
from app.llm import DemoStubLLM  # noqa: E402

PROFILE = Path(__file__).with_name("mock_profile.json")
DEFAULT = {"turn1": {"latency_s": 3.0, "prompt_tokens": 2200, "completion_tokens": 120},
           "turn2": {"latency_s": 6.0, "prompt_tokens": 6500, "completion_tokens": 450}, "note": "placeholder"}


def build(error_rate: float, seed: int = 11) -> FastAPI:
    app, rng = FastAPI(), random.Random(seed)
    profile = json.loads(PROFILE.read_text()) if PROFILE.exists() else DEFAULT
    brain = DemoStubLLM()

    @app.get("/v1/models")
    async def models() -> dict:
        return {"data": []}

    @app.post("/v1/chat/completions")
    async def completions(request: Request):
        body = await request.json()
        if error_rate and rng.random() < error_rate:
            return JSONResponse({"error": "mock overload"}, status_code=429)
        msgs = body["messages"]
        resp = await brain.complete(msgs, body.get("tools") or [])
        turn = profile["turn1" if resp.tool_calls else "turn2"]
        await asyncio.sleep(max(0.05, turn["latency_s"] * rng.uniform(0.8, 1.25)))
        msg: dict = {"role": "assistant", "content": resp.text}
        if resp.tool_calls:
            msg["tool_calls"] = [{"id": c.id, "type": "function", "function": {"name": c.name, "arguments": "{}"}}
                                 for c in resp.tool_calls]
        return {"choices": [{"message": msg}],
                "usage": {"prompt_tokens": turn["prompt_tokens"], "completion_tokens": turn["completion_tokens"],
                          "cost": 0.0}}

    return app


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8499)
    ap.add_argument("--error-rate", type=float, default=0.0, help="fraction of calls answered 429")
    a = ap.parse_args()
    uvicorn.run(build(a.error_rate), host="0.0.0.0", port=a.port, log_level="warning")
