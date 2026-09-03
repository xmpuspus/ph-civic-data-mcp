"""In-memory, process-local health registry for upstream hosts.

fetch_with_retry calls record_success and record_failure on every call, so
get_data_freshness can report which upstream host fails right now without
a database. Nothing here writes to disk. A restart clears it, so a
fresh process starts with an empty registry.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

MAX_HOSTS = 64

_REGISTRY: dict[str, dict[str, Any]] = {}


def _touch(host: str) -> dict[str, Any]:
    """Return the entry for host. Create it, and evict the oldest if full.

    Re-inserting a key moves it to the end of dict order, so the entry at
    the front is always the one touched longest ago. That is the one an
    eviction removes.
    """
    entry = _REGISTRY.pop(host, None)
    if entry is None:
        if len(_REGISTRY) >= MAX_HOSTS:
            oldest = next(iter(_REGISTRY))
            del _REGISTRY[oldest]
        entry = {
            "last_success_at": None,
            "last_failure_at": None,
            "last_error": None,
            "last_latency_ms": None,
            "success_count": 0,
            "failure_count": 0,
        }
    _REGISTRY[host] = entry
    return entry


def record_success(host: str, latency_ms: float) -> None:
    """Record a successful call to host, with its latency in milliseconds."""
    entry = _touch(host)
    entry["last_success_at"] = datetime.now(timezone.utc).isoformat()
    entry["last_latency_ms"] = latency_ms
    entry["success_count"] += 1


def record_failure(host: str, error: BaseException | str) -> None:
    """Record a failed call to host. error is an exception or a status label."""
    entry = _touch(host)
    if isinstance(error, BaseException):
        message = f"{type(error).__name__}: {error}"
    else:
        message = str(error)
    entry["last_failure_at"] = datetime.now(timezone.utc).isoformat()
    entry["last_error"] = message
    entry["failure_count"] += 1


def snapshot() -> dict[str, dict[str, Any]]:
    """Return a copy of the registry, so a caller cannot mutate live state."""
    return {host: dict(entry) for host, entry in _REGISTRY.items()}


def reset() -> None:
    """Clear the registry. Test-only. A running server never calls this."""
    _REGISTRY.clear()
