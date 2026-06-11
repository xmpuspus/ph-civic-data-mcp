"""Failure envelope for list-returning tools.

Audit 2026-06-11 finding: list tools returned a bare `[]` on upstream failure,
which agents read as a true "no hazards / no data" all-clear. On failure,
list tools now return this dict envelope instead so the agent can tell an
outage apart from genuinely empty data. Success responses keep their original
list shape — only the failure path changes.

The companion rule: never write a failure to a TTL cache. A transient upstream
blip must not pin "no data" for the full success TTL.
"""

from __future__ import annotations

from datetime import datetime, timezone


def failure_envelope(
    source: str,
    source_url: str,
    caveat: str,
    *,
    license: str | None = None,
) -> dict:
    """Dict a list-returning tool sends back when its upstream call failed.

    `results` is always [] and `upstream_error` is always True so agents can
    branch on either. The caveat names the source and the failure mode.
    """
    out: dict = {
        "results": [],
        "upstream_error": True,
        "caveats": [caveat],
        "source": source,
        "source_url": source_url,
        "data_retrieved_at": datetime.now(timezone.utc).isoformat(),
    }
    if license:
        out["license"] = license
    return out
