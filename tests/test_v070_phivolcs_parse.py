"""Offline tests for the PHIVOLCS earthquake list table parser (v0.7.0).

Codex cross-model finding: `_fetch_earthquake_list` detected the header row
by name, then read each data row by a fixed cell position. A column reorder
upstream would emit longitude as latitude and vice versa with no error. The
fix reads each cell through a name-to-index map built from the header row.
"""

from __future__ import annotations

import httpx
import pytest

from ph_civic_data_mcp.sources import phivolcs as phivolcs_module
from ph_civic_data_mcp.utils.cache import CACHES

PHIVOLCS_EQ_LIST_URL = phivolcs_module.PHIVOLCS_EQ_LIST_URL


def _row(*cells: str) -> str:
    tds = "".join(f"<td>{c}</td>" for c in cells)
    return f"<tr>{tds}</tr>"


def _table(header: str, rows: list[str]) -> str:
    return f"<html><body><table>{header}{''.join(rows)}</table></body></html>"


def _page(text: str) -> httpx.Response:
    return httpx.Response(200, text=text, request=httpx.Request("GET", PHIVOLCS_EQ_LIST_URL))


@pytest.fixture(autouse=True)
def _clear_cache():
    CACHES["phivolcs_earthquakes"].clear()
    yield
    CACHES["phivolcs_earthquakes"].clear()


@pytest.mark.asyncio
async def test_reordered_columns_read_by_header_name_not_position(monkeypatch):
    """Date, Longitude, Latitude, Depth, Magnitude, Location: a real reorder."""
    header = _row("Date - Time", "Longitude", "Latitude", "Depth", "Magnitude", "Location")
    rows = [
        _row("04 September 2026 - 10:00 AM", "120.9", "14.6", "10", "4.5", "Near Manila"),
        _row("03 September 2026 - 09:00 AM", "121.0", "14.7", "12", "3.1", "Near Quezon City"),
        _row("02 September 2026 - 08:00 AM", "121.1", "14.8", "8", "2.9", "Near Marikina"),
        _row("01 September 2026 - 07:00 AM", "121.2", "14.9", "15", "3.5", "Near Pasig"),
    ]
    html = _table(header, rows)

    async def _fake(client, method, url, **kwargs):
        return _page(html)

    monkeypatch.setattr(phivolcs_module, "fetch_with_retry", _fake)

    results = await phivolcs_module._fetch_earthquake_list()

    assert results[0]["latitude"] == 14.6
    assert results[0]["longitude"] == 120.9


@pytest.mark.asyncio
async def test_header_missing_magnitude_column_raises_not_emits_rows(monkeypatch):
    """A header with no magnitude column must raise, never return rows."""
    header = _row("Date - Time", "Latitude", "Longitude", "Depth", "Location")
    rows = [
        _row("04 September 2026 - 10:00 AM", "14.6", "120.9", "10", "Near Manila"),
        _row("03 September 2026 - 09:00 AM", "14.7", "121.0", "12", "Near Quezon City"),
        _row("02 September 2026 - 08:00 AM", "14.8", "121.1", "8", "Near Marikina"),
        _row("01 September 2026 - 07:00 AM", "14.9", "121.2", "15", "Near Pasig"),
    ]
    html = _table(header, rows)

    async def _fake(client, method, url, **kwargs):
        return _page(html)

    monkeypatch.setattr(phivolcs_module, "fetch_with_retry", _fake)

    with pytest.raises(RuntimeError):
        await phivolcs_module._fetch_earthquake_list()


@pytest.mark.asyncio
async def test_data_rows_that_all_fail_to_parse_raise_not_emit_empty(monkeypatch):
    """A table with every required header, but a magnitude cell that is not
    a number on every data row, must raise the drift error. Caching []
    here would read as "no earthquakes" for a page that always has rows."""
    header = _row("Date - Time", "Latitude", "Longitude", "Depth", "Magnitude", "Location")
    rows = [
        _row("04 September 2026 - 10:00 AM", "14.6", "120.9", "10", "bad", "Near Manila"),
        _row("03 September 2026 - 09:00 AM", "14.7", "121.0", "12", "bad", "Near Quezon City"),
        _row("02 September 2026 - 08:00 AM", "14.8", "121.1", "8", "bad", "Near Marikina"),
        _row("01 September 2026 - 07:00 AM", "14.9", "121.2", "15", "bad", "Near Pasig"),
    ]
    html = _table(header, rows)

    async def _fake(client, method, url, **kwargs):
        return _page(html)

    monkeypatch.setattr(phivolcs_module, "fetch_with_retry", _fake)

    with pytest.raises(RuntimeError, match="parsed 0 of"):
        await phivolcs_module._fetch_earthquake_list()
    assert len(CACHES["phivolcs_earthquakes"]) == 0


@pytest.mark.asyncio
async def test_rows_below_the_required_cell_count_also_raise_not_emit_empty(monkeypatch):
    """The table selects fine (header carries latitude and mag, 5+ <tr>),
    but every row has too few cells to read, for example a layout change
    that collapses each row to a colspan message. This must raise too:
    zero parsed rows is drift, whether the cause is a bad cell value or a
    row shape that never reaches the parse step at all."""
    header = _row("Date - Time", "Latitude", "Longitude", "Depth", "Magnitude", "Location")
    rows = [_row("x", "y") for _ in range(4)]
    html = _table(header, rows)

    async def _fake(client, method, url, **kwargs):
        return _page(html)

    monkeypatch.setattr(phivolcs_module, "fetch_with_retry", _fake)

    with pytest.raises(RuntimeError, match="parsed 0 of"):
        await phivolcs_module._fetch_earthquake_list()
    assert len(CACHES["phivolcs_earthquakes"]) == 0


@pytest.mark.asyncio
async def test_header_missing_depth_column_raises_not_emits_rows(monkeypatch):
    """A header keeping latitude and magnitude, but dropping depth, must
    still raise. Latitude and magnitude alone pass table selection, so this
    pins the column-map completeness check, not just table selection."""
    header = _row("Date - Time", "Latitude", "Longitude", "Magnitude", "Location")
    rows = [
        _row("04 September 2026 - 10:00 AM", "14.6", "120.9", "4.5", "Near Manila"),
        _row("03 September 2026 - 09:00 AM", "14.7", "121.0", "3.1", "Near Quezon City"),
        _row("02 September 2026 - 08:00 AM", "14.8", "121.1", "2.9", "Near Marikina"),
        _row("01 September 2026 - 07:00 AM", "14.9", "121.2", "3.5", "Near Pasig"),
    ]
    html = _table(header, rows)

    async def _fake(client, method, url, **kwargs):
        return _page(html)

    monkeypatch.setattr(phivolcs_module, "fetch_with_retry", _fake)

    with pytest.raises(RuntimeError, match="depth"):
        await phivolcs_module._fetch_earthquake_list()
