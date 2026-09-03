"""Offline tests for the v0.7.0 source-health registry (utils/health.py).

fetch_with_retry now records a success or a failure per upstream host, and
get_data_freshness reports that registry plus a per-cache size and TTL. All
of this stays in memory, so a cold process starts with an empty registry.
"""

from __future__ import annotations

import asyncio

import httpx
import pytest

from ph_civic_data_mcp.server import get_data_freshness
from ph_civic_data_mcp.utils import health
from ph_civic_data_mcp.utils import http as http_module
from ph_civic_data_mcp.utils.cache import CACHES


@pytest.fixture(autouse=True)
def _clean_registry():
    health.reset()
    yield
    health.reset()


def _fake_sleep(monkeypatch) -> None:
    async def _sleep(seconds: float) -> None:
        return None

    monkeypatch.setattr(asyncio, "sleep", _sleep)


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


class _AlwaysStatus:
    """A fake httpx.AsyncClient.request that always returns one status code."""

    def __init__(self, status_code: int) -> None:
        self.status_code = status_code
        self.calls = 0

    async def request(self, method: str, url: str, **kwargs: object) -> httpx.Response:
        self.calls += 1
        return httpx.Response(self.status_code, request=httpx.Request(method, url))


def test_record_success_then_snapshot_round_trip():
    health.record_success("example.test", latency_ms=42.5)
    snap = health.snapshot()
    assert snap["example.test"]["success_count"] == 1
    assert snap["example.test"]["last_latency_ms"] == pytest.approx(42.5)
    assert snap["example.test"]["last_success_at"] is not None
    assert snap["example.test"]["failure_count"] == 0


def test_record_failure_then_snapshot_round_trip():
    health.record_failure("example.test", ValueError("bad response"))
    snap = health.snapshot()
    assert snap["example.test"]["failure_count"] == 1
    assert "ValueError" in snap["example.test"]["last_error"]
    assert "bad response" in snap["example.test"]["last_error"]
    assert snap["example.test"]["last_failure_at"] is not None


def test_registry_evicts_the_oldest_host_at_65():
    for i in range(65):
        health.record_success(f"host{i}.test", latency_ms=1.0)
    snap = health.snapshot()
    assert len(snap) == health.MAX_HOSTS
    assert "host0.test" not in snap, "the least recently touched host must be evicted"
    assert "host64.test" in snap


@pytest.mark.asyncio
async def test_fetch_with_retry_success_records_latency_and_count(monkeypatch):
    _fake_sleep(monkeypatch)
    client = _FailNTimes(httpx.ConnectError("boom"), fail_count=1)
    response = await http_module.fetch_with_retry(client, "GET", "https://example.test/path")
    assert response.status_code == 200
    snap = health.snapshot()
    entry = snap["example.test"]
    assert entry["success_count"] == 1
    assert entry["last_latency_ms"] >= 0


@pytest.mark.asyncio
async def test_fetch_with_retry_exhausted_exception_records_failure(monkeypatch):
    _fake_sleep(monkeypatch)
    client = _FailNTimes(httpx.PoolTimeout("pool exhausted"), fail_count=http_module.MAX_RETRIES)
    with pytest.raises(httpx.PoolTimeout):
        await http_module.fetch_with_retry(client, "GET", "https://example.test/path")
    entry = health.snapshot()["example.test"]
    assert entry["failure_count"] == 1
    assert "PoolTimeout" in entry["last_error"]
    assert "pool exhausted" in entry["last_error"]


@pytest.mark.asyncio
async def test_fetch_with_retry_exhausted_503_records_failure(monkeypatch):
    _fake_sleep(monkeypatch)
    client = _AlwaysStatus(503)
    response = await http_module.fetch_with_retry(client, "GET", "https://example.test/path")
    assert response.status_code == 503
    assert client.calls == http_module.MAX_RETRIES
    entry = health.snapshot()["example.test"]
    assert entry["failure_count"] == 1
    assert "503" in entry["last_error"]


@pytest.mark.asyncio
async def test_fetch_with_retry_terminal_403_records_failure(monkeypatch):
    """A blocked or rejected request is a host-health signal, not just a
    caller mistake, so it must show up in get_data_freshness."""
    _fake_sleep(monkeypatch)
    client = _AlwaysStatus(403)
    response = await http_module.fetch_with_retry(client, "GET", "https://example.test/path")
    assert response.status_code == 403
    entry = health.snapshot()["example.test"]
    assert entry["failure_count"] == 1
    assert entry["last_error"] == "HTTP 403"


@pytest.mark.asyncio
async def test_fetch_with_retry_terminal_404_leaves_registry_empty(monkeypatch):
    """A 404 is the caller's own bad request, not the host failing, so it
    must never be recorded as a health event."""
    _fake_sleep(monkeypatch)
    client = _AlwaysStatus(404)
    response = await http_module.fetch_with_retry(client, "GET", "https://example.test/path")
    assert response.status_code == 404
    assert health.snapshot() == {}


@pytest.mark.asyncio
async def test_get_data_freshness_carries_source_health_and_cache_age():
    health.record_success("example.test", latency_ms=10.0)
    result = await get_data_freshness()
    assert result["source_health"]["example.test"]["success_count"] == 1
    assert set(CACHES) <= set(result["cache_age"])
    one_cache = next(iter(CACHES))
    assert result["cache_age"][one_cache]["ttl_seconds"] == CACHES[one_cache].ttl


@pytest.mark.asyncio
async def test_get_data_freshness_source_health_empty_on_cold_registry():
    result = await get_data_freshness()
    assert result["source_health"] == {}
