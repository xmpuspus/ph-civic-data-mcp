"""A plain import of the server must expose the whole MCP surface.

Before v0.6.0 only main() called _register_tools(), so `fastmcp inspect` and
any library import saw 1 tool of 29. inspect imports the module and never calls
main(), which is why the discrepancy hid for five releases.

These assert the invariant (a bare import equals an explicitly-registered
import), not a literal count. The literal 32 is pinned in CI and in
release-smoke, where a drifting number is the point.
"""

from __future__ import annotations

import subprocess
import sys

import pytest

BARE_IMPORT = (
    "import asyncio;"
    "from ph_civic_data_mcp.server import mcp;"
    "print(len(asyncio.run(mcp.list_tools())))"
)

EXPLICIT_REGISTER = (
    "import asyncio;"
    "from ph_civic_data_mcp.server import mcp, _register_tools;"
    "_register_tools();"
    "print(len(asyncio.run(mcp.list_tools())))"
)


def _count(code: str) -> int:
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=180)
    assert out.returncode == 0, out.stderr
    return int(out.stdout.strip())


def test_bare_import_registers_the_same_tools_as_an_explicit_call():
    bare = _count(BARE_IMPORT)
    explicit = _count(EXPLICIT_REGISTER)
    assert bare == explicit, f"bare import saw {bare} tools, explicit call saw {explicit}"
    assert bare > 1, "a bare import must expose more than the one tool in server.py"


def test_importing_a_source_module_first_does_not_break_the_import():
    """psa.py imports the shared instance; that must not re-enter registration."""
    code = (
        "import asyncio;"
        "import ph_civic_data_mcp.sources.psa;"
        "from ph_civic_data_mcp.server import mcp;"
        "print(len(asyncio.run(mcp.list_tools())))"
    )
    assert _count(code) == _count(BARE_IMPORT)


def test_module_entrypoint_exposes_the_same_surface():
    """`python -m ph_civic_data_mcp.server` must not run a second empty instance."""
    code = (
        "import asyncio, runpy, sys;"
        "sys.modules.pop('ph_civic_data_mcp.server', None);"
        "m = __import__('ph_civic_data_mcp.server', fromlist=['mcp']);"
        "print(len(asyncio.run(m.mcp.list_tools())))"
    )
    assert _count(code) == _count(BARE_IMPORT)


@pytest.mark.asyncio
async def test_register_tools_stays_callable_and_idempotent():
    """release-smoke on already published wheels calls _register_tools() by name."""
    from ph_civic_data_mcp.server import _register_tools, mcp

    _register_tools()
    first = len(await mcp.list_tools())
    _register_tools()
    second = len(await mcp.list_tools())
    assert first > 1
    assert second == first, "a second call must not duplicate or drop tools"


@pytest.mark.asyncio
async def test_every_tool_carries_title_and_read_only_annotations():
    from ph_civic_data_mcp.server import mcp

    for tool in await mcp.list_tools():
        assert tool.title, f"{tool.name} has no title"
        assert tool.tags, f"{tool.name} has no tags"
        assert tool.annotations is not None, f"{tool.name} has no annotations"
        assert tool.annotations.readOnlyHint is True, f"{tool.name} is not read-only"
        assert tool.annotations.idempotentHint is True, f"{tool.name} is not idempotent"
        assert tool.annotations.destructiveHint is False


@pytest.mark.asyncio
async def test_only_the_in_process_tool_is_closed_world():
    """A tool that calls an upstream service must declare openWorldHint."""
    from ph_civic_data_mcp.server import mcp

    closed = {t.name for t in await mcp.list_tools() if t.annotations.openWorldHint is False}
    assert closed == {"get_data_freshness"}, closed


@pytest.mark.asyncio
async def test_server_reports_its_application_version():
    from ph_civic_data_mcp import __version__
    from ph_civic_data_mcp.server import get_data_freshness, mcp

    assert mcp.version == __version__
    fresh = await get_data_freshness()
    assert fresh["server_version"] == __version__
    assert fresh["tool_count"] == len(await mcp.list_tools())
