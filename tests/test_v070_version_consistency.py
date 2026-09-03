"""Every published version marker must say the same version.

A release bumps four places by hand: __version__, manifest.json's version,
server.json's top-level version, and server.json's packages[].version. See
"Version surfaces" in CLAUDE.md. Missing one ships a package that reports one
version and a registry entry that reports another, and nothing catches it
until a user notices.

This test reads all four values from disk, not from a cache. It fails the
moment a release misses one of them.
"""

from __future__ import annotations

import json
from pathlib import Path

import ph_civic_data_mcp

REPO_ROOT = Path(__file__).resolve().parent.parent


def _manifest_version() -> str:
    data = json.loads((REPO_ROOT / "manifest.json").read_text())
    return data["version"]


def _server_json() -> dict:
    return json.loads((REPO_ROOT / "server.json").read_text())


def test_package_version_matches_manifest_version():
    assert ph_civic_data_mcp.__version__ == _manifest_version()


def test_package_version_matches_server_json_top_level_version():
    assert ph_civic_data_mcp.__version__ == _server_json()["version"]


def test_package_version_matches_every_server_json_package_version():
    packages = _server_json()["packages"]
    assert packages, "server.json has no packages entries to check"
    for package in packages:
        assert package["version"] == ph_civic_data_mcp.__version__, package
