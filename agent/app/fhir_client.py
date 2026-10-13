"""Thin FHIR reader: per-call timeout, one retry on transient failure, typed ToolError. Read-only by construction."""
from __future__ import annotations

import asyncio
import logging

import httpx

from contracts import ToolError

from . import tracing
from .config import Settings
from .logging_setup import outbound_headers

logger = logging.getLogger("agent.fhir")


class FhirClient:
    def __init__(self, settings: Settings, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._s = settings
        self._transport = transport

    async def search(self, resource: str, token: str, params: dict[str, str]) -> list[dict]:
        """GET a FHIR search and return the resources. Only GET exists on this class."""
        url = f"{self._s.openemr_base_url}/apis/default/fhir/{resource}"
        last: ToolError | None = None
        for attempt in (1, 2):
            try:
                async with httpx.AsyncClient(
                    verify=self._s.openemr_verify_tls, timeout=self._s.tool_timeout_s, transport=self._transport
                ) as c:
                    r = await c.get(url, params=params, headers=outbound_headers({"Authorization": f"Bearer {token}"}))
            except httpx.TimeoutException:
                last = ToolError("timeout", f"{resource} lookup timed out", retryable=True)
            except httpx.HTTPError:
                last = ToolError("upstream_error", f"{resource} lookup failed", retryable=True)
            else:
                if r.status_code in (401, 403):
                    raise ToolError("unauthorized", f"not permitted to read {resource}")
                if r.status_code >= 500:
                    last = ToolError("upstream_error", f"{resource} lookup failed", retryable=True)
                elif r.status_code != 200:
                    raise ToolError("upstream_error", f"{resource} lookup failed")
                else:
                    return self._entries(resource, r)
            if attempt == 1:
                tracing.count_retry()
                await asyncio.sleep(0.2)
        assert last is not None
        raise last

    @staticmethod
    def _entries(resource: str, r: httpx.Response) -> list[dict]:
        try:
            body = r.json()
        except ValueError as e:
            raise ToolError("bad_response", f"{resource} returned malformed data") from e
        if not isinstance(body, dict) or body.get("resourceType") != "Bundle":
            raise ToolError("bad_response", f"{resource} returned an unexpected shape")
        out: list[dict] = []
        for e in body.get("entry", []) or []:
            res = e.get("resource") if isinstance(e, dict) else None
            if isinstance(res, dict) and res.get("resourceType") == resource:
                out.append(res)  # OperationOutcome and other resource types are dropped
        return out

    async def read(self, resource: str, rid: str, token: str) -> dict:
        url = f"{self._s.openemr_base_url}/apis/default/fhir/{resource}/{rid}"
        try:
            async with httpx.AsyncClient(
                verify=self._s.openemr_verify_tls, timeout=self._s.tool_timeout_s, transport=self._transport
            ) as c:
                r = await c.get(url, headers=outbound_headers({"Authorization": f"Bearer {token}"}))
        except httpx.TimeoutException as e:
            raise ToolError("timeout", f"{resource} lookup timed out", retryable=True) from e
        except httpx.HTTPError as e:
            raise ToolError("upstream_error", f"{resource} lookup failed", retryable=True) from e
        if r.status_code in (401, 403):
            raise ToolError("unauthorized", f"not permitted to read {resource}")
        if r.status_code != 200:
            raise ToolError("upstream_error", f"{resource} lookup failed")
        try:
            body = r.json()
        except ValueError as e:
            raise ToolError("bad_response", f"{resource} returned malformed data") from e
        if not isinstance(body, dict) or body.get("resourceType") != resource:
            raise ToolError("bad_response", f"{resource} returned an unexpected shape")
        return body
