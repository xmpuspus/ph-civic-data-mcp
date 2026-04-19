"""HTTP clients. Two-client pattern: standard + PHIVOLCS (verify=False).

Never use PHIVOLCS_CLIENT for other sources. Never disable SSL globally.
"""

from __future__ import annotations

import asyncio
import sys
from typing import Any

import httpx

USER_AGENT = "ph-civic-data-mcp/0.1.6 (+https://github.com/xmpuspus/ph-civic-data-mcp; civic data research)"

MAX_RETRIES = 3
RETRY_STATUSES = {429, 503, 504}
RETRY_DELAYS = [1, 2, 4]

CLIENT = httpx.AsyncClient(
    timeout=httpx.Timeout(30.0, connect=10.0),
    headers={
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/json,*/*",
    },
    follow_redirects=True,
)

PHIVOLCS_CLIENT = httpx.AsyncClient(
    verify=False,
    timeout=httpx.Timeout(30.0, connect=10.0),
    headers={"User-Agent": USER_AGENT},
    follow_redirects=True,
)


async def fetch_with_retry(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    **kwargs: Any,
) -> httpx.Response:
    """Retry on 429/503/504 with exponential backoff. Pass everything else through."""
    last_exc: Exception | None = None
    for attempt in range(MAX_RETRIES):
        try:
            response = await client.request(method, url, **kwargs)
            if response.status_code in RETRY_STATUSES and attempt < MAX_RETRIES - 1:
                await asyncio.sleep(RETRY_DELAYS[attempt])
                continue
            return response
        except (httpx.ConnectError, httpx.ReadTimeout, httpx.RemoteProtocolError) as exc:
            last_exc = exc
            if attempt < MAX_RETRIES - 1:
                await asyncio.sleep(RETRY_DELAYS[attempt])
                continue
            raise
    if last_exc:
        raise last_exc
    raise RuntimeError("unreachable")


def log_stderr(msg: str) -> None:
    print(f"[ph-civic-data-mcp] {msg}", file=sys.stderr, flush=True)
