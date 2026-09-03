"""Shared helpers for the live-marked suites.

A live test has one job: catch drift in a real upstream. So it must skip only
when the upstream is positively down, and fail on everything else. The old
guards skipped on any `caveats` entry, which is how a broken population
discovery stayed green in the weekly live-drift run for weeks.
"""

from __future__ import annotations

import re

import pytest

# Text that only a transport or availability failure produces. A discovery
# miss ("the catalog moved"), a schema change, or a wrong geography never
# matches, so those fail the test the way drift should.
_OUTAGE_MARKERS = re.compile(
    r"ConnectError|ConnectTimeout|ReadTimeout|ReadError|RemoteProtocolError|PoolTimeout|"
    r"WriteTimeout|HTTPStatusError|\b(?:429|500|502|503|504)\b|Too Many Requests|"
    r"Service Unavailable|Gateway|timed out|unreachable|unavailable \(",
    re.IGNORECASE,
)


def is_outage(result: object) -> bool:
    """True when a tool result reports an upstream that was positively down."""
    if not isinstance(result, dict) or not result.get("upstream_error"):
        return False
    text = " ".join(str(c) for c in result.get("caveats") or [])
    return bool(_OUTAGE_MARKERS.search(text))


def skip_if_outage(result: object, label: str) -> None:
    """Skip on a positively identified outage. Anything else runs the assertions."""
    if is_outage(result):
        pytest.skip(f"{label} outage: {result.get('caveats')}")  # type: ignore[union-attr]
