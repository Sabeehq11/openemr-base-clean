"""HTTP surface for tools. Every route is behind the signed-request gateway AND the per-call patient authorization."""
from __future__ import annotations

import json

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from contracts import ToolError, ToolName

from .gateway import CallerContext, Gateway
from .tools import DeniedError, ToolRunner

GENERIC_DENIAL = {"error": "forbidden", "message": "You do not have access to this patient."}


def build_router(gateway: Gateway, runner: ToolRunner) -> APIRouter:
    router = APIRouter(prefix="/v1")

    @router.post("/tools/{tool_name}")
    async def call_tool(tool_name: str, request: Request) -> JSONResponse:
        ctx: CallerContext = await gateway.verify(request)
        try:
            tool = ToolName(tool_name)
            payload = json.loads(ctx.body or b"{}")
            patient = payload["patient_uuid"]
            args = {k: v for k, v in payload.items() if k != "patient_uuid"}  # validated by the tool's contract
            if not isinstance(patient, str):
                raise TypeError
        except (ValueError, KeyError, TypeError):
            return JSONResponse({"error": "invalid_request", "message": "Malformed request."}, status_code=400)
        try:
            env = await runner.run(tool, ctx.token, patient, claimed_user=ctx.user_uuid, **args)
        except DeniedError:
            return JSONResponse(GENERIC_DENIAL, status_code=403)  # same body whatever the reason
        except ToolError as e:
            status = 400 if e.code == "invalid_input" else 502
            return JSONResponse({"error": e.code, "message": e.message}, status_code=status)
        return JSONResponse(json.loads(env.model_dump_json()))

    return router
