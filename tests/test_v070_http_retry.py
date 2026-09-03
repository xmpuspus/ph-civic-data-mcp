"""Offline tests for the v0.7.0 retry-coverage fix in utils/http.py.

fetch_with_retry caught only ConnectError, ReadTimeout, and
RemoteProtocolError. A ConnectTimeout, ReadError, PoolTimeout, WriteTimeout,
or WriteError fell straight through uncaught on the first attempt, so a
transient transport hiccup on those five exception types got zero retries.
"""

from __future__ import annotations

import asyncio

import httpx
import pytest

from ph_civic_data_mcp.utils import http as http_module


def _fake_sleep(monkeypatch) -> list[float]:
    """No real sleep in a unit test. Records what fetch_with_retry asked for."""
    waits: list[float] = []

    async def _sleep(seconds: float) -> None:
        waits.append(seconds)

    monkeypatch.setattr(asyncio, "sleep", _sleep)
    return waits


class _FailNTimes:
    """A fake httpx.AsyncClient.request that raises, then succeeds."""

    def __init__(self, exc: Exception, fail_count: int) -> None:
        self.exc = exc
        self.fail_count = fail_count
        self.calls = 0

    async def request(self, method: str, url: str, **kwargs: object) -> httpx.Response:
        self.calls += 1
        if self.calls <= self.fail_count:
            raise self.exc
        return httpx.Response(200, request=httpx.Request(method, url))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "exc",
    [
        httpx.ConnectTimeout("timed out connecting"),
        httpx.ReadError("read failed"),
        httpx.PoolTimeout("pool exhausted"),
        httpx.WriteTimeout("write timed out"),
        httpx.WriteError("write failed"),
    ],
)
async def test_fetch_with_retry_retries_every_transport_exception(monkeypatch, exc):
    _fake_sleep(monkeypatch)
    client = _FailNTimes(exc, fail_count=1)
    response = await http_module.fetch_with_retry(client, "GET", "https://example.test")
    assert response.status_code == 200
    assert client.calls == 2, "a transient exception on attempt 1 must get a retry"


@pytest.mark.asyncio
async def test_fetch_with_retry_reraises_after_exhausting_attempts(monkeypatch):
    _fake_sleep(monkeypatch)
    client = _FailNTimes(httpx.PoolTimeout("pool exhausted"), fail_count=http_module.MAX_RETRIES)
    with pytest.raises(httpx.PoolTimeout):
        await http_module.fetch_with_retry(client, "GET", "https://example.test")
    assert client.calls == http_module.MAX_RETRIES


def test_with_jitter_never_shrinks_the_delay():
    for delay in (1.0, 5.0, 11.0):
        jittered = http_module._with_jitter(delay)
        assert delay <= jittered <= delay * 1.1


def test_retry_delay_ladder_unchanged_by_jitter():
    """Jitter lives in fetch_with_retry's sleep call, not in _retry_delay itself."""
    response = httpx.Response(503, request=httpx.Request("GET", "https://example.test"))
    assert [http_module._retry_delay(response, i) for i in range(3)] == [1.0, 2.0, 4.0]
