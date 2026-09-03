# FastMCP 4 is safe to adopt later, and v0.7.0 stays on 3.x

Checked 2026-09-03 against the v4.0.0 release notes and the official
migration guide. FastMCP 4.0.2 is current. This project runs 3.4.7 and
pins `fastmcp>=3.0.0,<4.0.0`.

Verdict: stay on 3.x for v0.7.0. Adopt 4.x in v0.8.0 or later. The project
uses none of the removed or changed APIs (sampling, elicitation, background
tasks, legacy Client syntax). One hard prerequisite exists: the pydantic
floor. One conditional item exists: the httpx to httpx2 transition.

## Nine of ten breaking changes do not touch this project

| Change | Source | Affected | Where |
|---|---|---|---|
| Server sampling and roots removed | Release notes v4.0.0, "Remove server-initiated sampling and roots" | No | No `ctx.sample()` or `ctx.list_roots()` in `src/` |
| `ctx.elicit()` deprecated | Migration guide, section 5 | No | No `ctx.elicit()` in `src/` |
| Deprecated 3.x shims removed | Migration guide, section 2 | No | Only `from fastmcp import FastMCP` in `src/ph_civic_data_mcp/_mcp.py` |
| snake_case field bridge | Migration guide, section 1 | No | Tools return `.model_dump(mode="json")` dicts, no camelCase access |
| Background tasks moved to `fastmcp-tasks` | Migration guide, section 6 | No | No `task=` on any `@mcp.tool` |
| `Client("string")` deprecated | Migration guide, section 3 | No | Tests use `Client(mcp)` only, in `tests/test_psa_catalog.py` |
| Module relocations (proxy, openapi) | Migration guide, section 2 | No | Only `FastMCP` is imported |
| httpx to httpx2 inside FastMCP | Migration guide, section 7 | Conditional | `src/ph_civic_data_mcp/utils/http.py` and `sources/phivolcs.py` import httpx directly |
| Pydantic floor raised to 2.12 | Migration guide, "Environment Requirements" | Yes | `pyproject.toml` sets `pydantic>=2.0.0`, needs `>=2.12.0` |
| Error handling spec changes | Release notes v4.0.0 | No | Sources raise httpx exceptions, never `McpError` |

## The pydantic floor is the only code change a migration needs

`uv.lock` already resolves pydantic 2.13.2, so raising the floor in
`pyproject.toml` from `>=2.0.0` to `>=2.12.0` is a one-line change with no
resolver risk.

The httpx item is conditional. FastMCP 4 switched its own HTTP client to
httpx2. This project's `httpx` imports are its own dependency, not FastMCP's,
and the exception classes this project catches stay compatible. Move to
httpx2 on this project's own schedule, not as part of the FastMCP bump.

Nothing else changes. Decorator usage (`@mcp.tool`, `@mcp.resource`,
`@mcp.prompt` with `tags=` and `annotations=`), the failure envelope
contract, and import-time registration all work the same in 4.x.

## A migration pull request needs five checks before merge

1. Raise pydantic to `>=2.12.0` in `pyproject.toml` and change the fastmcp
   pin to `>=4.0.0,<5.0.0`.
2. Run `uv lock` to refresh the lockfile.
3. Run `pytest -m "not live"` on Python 3.11 through 3.14.
4. Run `twine check` on the built wheel.
5. Run `tests/test_v060_registration.py` and `fastmcp inspect`. That test
   pins the tool count across four import paths, so it catches a silent
   registration change first.

## Recommendation

Ship v0.7.0 on FastMCP 3.4.7. Open the FastMCP 4 migration as its own small
pull request in v0.8.0. The change is low risk and low effort, and it does
not block any feature work today.

Sources: https://github.com/jlowin/fastmcp/releases/tag/v4.0.0 and
https://gofastmcp.com/getting-started/upgrading/from-fastmcp-3
