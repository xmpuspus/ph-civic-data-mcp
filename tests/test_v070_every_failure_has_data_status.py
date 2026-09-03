"""Every failure path converted in v0.7.0 must carry a real data_status.

Six sites used to build a failure dict by hand, with upstream_error set but
no data_status. Each now goes through failure_result. This test drives each
site into its failure path and checks the closed-set contract holds.
"""

from __future__ import annotations

import httpx
import pytest

from ph_civic_data_mcp.sources import infra as infra_module
from ph_civic_data_mcp.sources import phivolcs as phivolcs_module
from ph_civic_data_mcp.sources import psa as psa_module
from ph_civic_data_mcp.utils.cache import CACHES
from ph_civic_data_mcp.utils.envelope import DATA_STATUS_VALUES


async def _boom(client, method, url, **kwargs):
    raise httpx.ConnectError("openstat down")


@pytest.mark.asyncio
async def test_poverty_discovery_failure_sets_data_status(monkeypatch):
    CACHES["psa_poverty"].clear()
    CACHES["psa_browse"].clear()
    psa_module._DISCOVERY_CACHE.clear()
    monkeypatch.setattr(psa_module, "fetch_with_retry", _boom)

    result = await psa_module.get_poverty_stats()

    assert result["data_status"] in DATA_STATUS_VALUES
    assert result["upstream_error"] is True


@pytest.mark.asyncio
async def test_inflation_discovery_failure_sets_data_status(monkeypatch):
    CACHES["psa_prices"].clear()
    CACHES["psa_browse"].clear()
    psa_module._DISCOVERY_CACHE.clear()
    monkeypatch.setattr(psa_module, "fetch_with_retry", _boom)

    result = await psa_module.get_inflation_stats()

    assert result["data_status"] in DATA_STATUS_VALUES
    assert result["upstream_error"] is True


@pytest.mark.asyncio
async def test_labor_discovery_failure_sets_data_status(monkeypatch):
    CACHES["psa_labor"].clear()
    CACHES["psa_browse"].clear()
    psa_module._DISCOVERY_CACHE.clear()
    monkeypatch.setattr(psa_module, "fetch_with_retry", _boom)

    result = await psa_module.get_labor_stats()

    assert result["data_status"] in DATA_STATUS_VALUES
    assert result["upstream_error"] is True


@pytest.mark.asyncio
async def test_health_browse_failure_sets_data_status(monkeypatch):
    CACHES["psa_health"].clear()
    CACHES["psa_browse"].clear()
    psa_module._DISCOVERY_CACHE.clear()
    monkeypatch.setattr(psa_module, "fetch_with_retry", _boom)

    result = await psa_module.get_health_indicators()

    assert result["data_status"] in DATA_STATUS_VALUES
    assert result["upstream_error"] is True


@pytest.mark.asyncio
async def test_infra_get_project_fetch_failure_sets_data_status(monkeypatch):
    CACHES["infra_projects"].clear()

    async def _fail():
        raise httpx.ConnectError("philgeps down")

    monkeypatch.setattr(infra_module, "_load_infra_records", _fail)

    result = await infra_module.get_infra_project("PHILGEPS-INF-001")

    assert result["data_status"] in DATA_STATUS_VALUES
    assert result["upstream_error"] is True


@pytest.mark.asyncio
async def test_phivolcs_bulletin_fetch_failure_sets_data_status(monkeypatch):
    CACHES["phivolcs_bulletins"].clear()
    url = "https://earthquake.phivolcs.dost.gov.ph/2026_Earthquake_Information/x.html"
    monkeypatch.setattr(phivolcs_module, "fetch_with_retry", _boom)

    result = await phivolcs_module.get_earthquake_bulletin(url)

    assert result["data_status"] in DATA_STATUS_VALUES
    assert result["upstream_error"] is True
