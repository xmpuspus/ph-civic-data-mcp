"""Failure contract shared by every tool.

Audit 2026-06-11 finding: list tools returned a bare `[]` on upstream failure,
which agents read as a true "no hazards / no data" all-clear. On failure,
list tools return the dict envelope from `failure_envelope` instead, so the
agent can tell an outage apart from genuinely empty data. Success responses
keep their original list shape; only the failure path changes.

Audit 2026-09-03 finding: single-value tools each hand-rolled their own
failure dict, and the population one omitted `upstream_error`, so a broken
tool read as a sparse answer for weeks. `failure_result` is the one builder
every dict-returning tool uses now, so a sibling cannot drift silently.

The companion rule: never write a failure to a TTL cache. A transient upstream
blip must not pin "no data" for the full success TTL.
"""

from __future__ import annotations

from datetime import datetime, timezone

# `data_status` values. Additive: a tool that never set the field before now
# sets one of these on every response.
DATA_STATUS_SUCCESS = "success"
DATA_STATUS_EMPTY = "empty"
DATA_STATUS_UNAVAILABLE = "unavailable"
DATA_STATUS_INDETERMINATE = "indeterminate"
# A caller mistake. Not an outage: `validation_error` is True and
# `upstream_error` is False, and retrying the same call cannot help.
DATA_STATUS_INVALID_REQUEST = "invalid_request"

# v0.7.0: the closed set every `data_status` value must belong to. A source
# module adding its own status string (a typo, a new outage shape) would
# silently escape the upstream_error/validation_error derivation above.
DATA_STATUS_VALUES = frozenset(
    {
        DATA_STATUS_SUCCESS,
        DATA_STATUS_EMPTY,
        DATA_STATUS_UNAVAILABLE,
        DATA_STATUS_INDETERMINATE,
        DATA_STATUS_INVALID_REQUEST,
    }
)


def failure_result(
    source: str,
    source_url: str,
    caveat: str | list[str],
    *,
    license: str | None = None,
    validation_error: bool = False,
    data_status: str | None = None,
    **fields: object,
) -> dict:
    """Dict a single-value tool sends back when it cannot publish a figure.

    `data_status` is the single source of truth. `upstream_error` and
    `validation_error` are both derived from it, so they can never disagree
    with the status a caller actually branches on. Codex cross-model finding
    on the v0.6.1 diff: an earlier version let a caller pass
    `data_status="empty"` alongside `validation_error=True`, which made a
    legitimate empty answer read as a caller mistake.

    `data_status` defaults from `validation_error` when not given explicitly:
    a rejected argument is `"invalid_request"`, anything else is
    `"unavailable"`. Pass `data_status="empty"` or `"indeterminate"`
    directly for those cases; `validation_error` is then ignored.

    Extra keyword fields (a `population: None`, a `region`) land in the dict
    first so the contract keys always win.
    """
    caveats = [caveat] if isinstance(caveat, str) else list(caveat)
    if data_status is None:
        data_status = DATA_STATUS_INVALID_REQUEST if validation_error else DATA_STATUS_UNAVAILABLE
    out: dict = dict(fields)
    out.update(
        {
            "data_status": data_status,
            "upstream_error": data_status in (DATA_STATUS_UNAVAILABLE, DATA_STATUS_INDETERMINATE),
            "validation_error": data_status == DATA_STATUS_INVALID_REQUEST,
            "caveats": caveats,
            "source": source,
            "source_url": source_url,
            "data_retrieved_at": datetime.now(timezone.utc).isoformat(),
        }
    )
    if license:
        out["license"] = license
    return out


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
    return failure_result(source, source_url, caveat, license=license, results=[])


def is_failure(result: object) -> bool:
    """True when a tool result is a failure envelope rather than data."""
    return isinstance(result, dict) and bool(
        result.get("upstream_error") or result.get("validation_error")
    )
