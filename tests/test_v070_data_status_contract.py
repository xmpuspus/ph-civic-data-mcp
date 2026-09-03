"""The v0.7.0 data_status contract, checked once so every source module can
build on it without re-deriving the rule.

Any source module extended in this release must set `data_status` to one of
`DATA_STATUS_VALUES` on a failure path, never a bespoke string. This file pins
the invariant `failure_result` already gave v0.6.1: `upstream_error` and
`validation_error` derive from `data_status` alone, so the two flags can never
disagree with the status a caller branches on.
"""

from __future__ import annotations

from ph_civic_data_mcp.utils.envelope import (
    DATA_STATUS_EMPTY,
    DATA_STATUS_INDETERMINATE,
    DATA_STATUS_INVALID_REQUEST,
    DATA_STATUS_SUCCESS,
    DATA_STATUS_UNAVAILABLE,
    DATA_STATUS_VALUES,
    failure_result,
)


def test_data_status_values_cover_the_five_named_constants():
    assert DATA_STATUS_VALUES == {
        DATA_STATUS_SUCCESS,
        DATA_STATUS_EMPTY,
        DATA_STATUS_UNAVAILABLE,
        DATA_STATUS_INDETERMINATE,
        DATA_STATUS_INVALID_REQUEST,
    }


def test_failure_result_status_always_in_the_closed_set():
    for status in DATA_STATUS_VALUES:
        out = failure_result("Test", "https://example.test", "a caveat", data_status=status)
        assert out["data_status"] in DATA_STATUS_VALUES


def test_upstream_and_validation_flags_never_both_true():
    """A response cannot be a caller mistake and an outage at once."""
    for status in DATA_STATUS_VALUES:
        out = failure_result("Test", "https://example.test", "a caveat", data_status=status)
        assert not (out["upstream_error"] and out["validation_error"]), status


def test_only_invalid_request_sets_validation_error():
    for status in DATA_STATUS_VALUES:
        out = failure_result("Test", "https://example.test", "a caveat", data_status=status)
        expected = status == DATA_STATUS_INVALID_REQUEST
        assert out["validation_error"] is expected, status


def test_unavailable_and_indeterminate_set_upstream_error():
    for status in (DATA_STATUS_UNAVAILABLE, DATA_STATUS_INDETERMINATE):
        out = failure_result("Test", "https://example.test", "a caveat", data_status=status)
        assert out["upstream_error"] is True, status


def test_success_and_empty_set_neither_flag():
    for status in (DATA_STATUS_SUCCESS, DATA_STATUS_EMPTY):
        out = failure_result("Test", "https://example.test", "a caveat", data_status=status)
        assert out["upstream_error"] is False, status
        assert out["validation_error"] is False, status
