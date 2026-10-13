"""Gate check for 'shared HTTP client': cost of a fresh TLS connection vs a reused one, run inside the agent container.
   docker exec -i agent-load python - < loadtest/conn_probe.py"""
import asyncio
import os
import statistics
import time

import httpx

URL = os.environ["OPENEMR_BASE_URL"] + "/apis/default/fhir/metadata"


async def timed(c: httpx.AsyncClient) -> float:
    t = time.perf_counter()
    await c.get(URL)
    return (time.perf_counter() - t) * 1000


async def main() -> None:
    fresh = []
    for _ in range(15):
        async with httpx.AsyncClient(verify=False, timeout=10) as c:
            fresh.append(await timed(c))
    async with httpx.AsyncClient(verify=False, timeout=10) as c:
        await timed(c)
        reused = [await timed(c) for _ in range(15)]
    print(f"fresh connection  p50={statistics.median(fresh):.1f} ms")
    print(f"reused connection p50={statistics.median(reused):.1f} ms")


asyncio.run(main())
