# CLAUDE.md - ph-civic-data-mcp

Current project guide. Read this before changing anything here.

The original v0.1.0 build specification lives in
[docs/archive/v0.1-build-spec.md](docs/archive/v0.1-build-spec.md). Most of it
is now wrong; treat it as provenance, never as instructions. Release history
lives in [CHANGELOG.md](CHANGELOG.md).

## What this is

| Field | Value |
|---|---|
| Package | `ph-civic-data-mcp` (PyPI) |
| Registry name | `io.github.xmpuspus/ph-civic-data-mcp` |
| Repository | `xmpuspus/ph-civic-data-mcp` |
| Current version | 0.6.0 |
| Surface | 32 tools, 3 prompts, 2 resources, 12 sources |
| Transport | stdio only, zero hosting cost |
| Python | 3.11+, tested on 3.11 through 3.14 |
| Framework | `fastmcp>=3.0.0,<4.0.0` (3.4.6 as of 2026-08-06) |
| Auth | None required. `PAGASA_API_TOKEN` optional. |

Deeper background sits in project memory: `project_ph_civic_data_mcp`,
`project_psa_expansion_handoff`, `feedback_release_pipeline`,
`feedback_demo_gifs`, `feedback_research_verification`.

## Contracts every change must hold

**The failure envelope.** A list tool returns a real list on success. On
upstream failure it returns `{results: [], upstream_error: true, caveats: [...]}`
through `utils/envelope.py::failure_envelope`. Never return a bare `[]` from an
error path; an agent reads that as "no earthquakes". Composites unwrap with
`cross_source._unwrap_list`.

**Never cache an error.** Only a success or a genuine negative enters `CACHES`.
A fetch helper raises or returns an un-cached error dict. A parse that yields
zero rows on a page that always has rows means drift: raise, never cache-empty.
See `_fetch_earthquake_list`, `_fetch_notices`, and `psa._browse`.

**A `caveats` entry carries the real error** (`ConnectError: ...`), never just
the exception class name.

**A caller mistake is not an outage.** The three OpenSTAT catalog tools return
`validation_error: true` for a rejected argument and `upstream_error: true` only
for an upstream failure.

**Allowlist an agent-supplied URL or path before any fetch.** See
`phivolcs._is_phivolcs_url`, which is mandatory for anything routed through the
`verify=False` client, and `psa_catalog._normalize_path`.

**A heuristic rule name says what it actually checked.**
`high_cost_no_published_progress`, not `high_cost_no_progress`: `progress_pct`
is None for every PhilGEPS record, so the old name implied a per-project check
that never happened.

**Every analytics response carries the disclaimer** and defensible language.
Never "fraud", "guilty", or a direct accusation.

## Registration and module layout

`src/ph_civic_data_mcp/_mcp.py` holds the single shared `FastMCP` instance.
`server.py` imports it, defines `get_data_freshness`, the resources and the
prompts, and calls `_register_tools()` at the bottom of the module.

Two rules follow, and both are load-bearing:

1. **A source module imports `mcp` from `_mcp`, never from `server`.** Importing
   from `server` re-enters import-time registration mid-import, and
   `autostitch` importing `psa` then fails with a circular-import error.
2. **Keep the `_register_tools()` call as the last statement before the
   `__main__` guard**, after every name a source module might read.

`_register_tools()` stays public and idempotent. release-smoke calls it by name
on already published wheels.

Before v0.6.0 only `main()` called it, so `fastmcp inspect` and any library
import saw 1 tool of 29. Four paths must now show the whole surface: the
console script, `python -m ph_civic_data_mcp.server`, a bare import, and
`fastmcp inspect`. `tests/test_v060_registration.py` pins the invariant; CI and
release-smoke pin the literal count.

## PSA OpenSTAT landmines

Live-checked 2026-08-06. Do not re-learn these.

- **Subjects move.** Poverty went from `DB/1E/FY` to `DB/1F/FY`; the old path
  404s and `1E` is now "Income and Consumption". Discover a subject by title
  from the root listing, never by id. The root carries both "Poverty" and
  "Living Conditions, Poverty and Cross-cutting Social Issues", so an exact
  title match must win over a substring match.
- **Folder depth varies.** `1F/FY` reaches tables in two levels; `2A/PPA/2025`
  needs three. Never assume a fixed depth.
- **POST with `response: {format: "json"}`**, and parse `data[].key` and
  `data[].values`. **Not `json-stat2`**: on this PXWeb it returns a sparse
  shape that mis-parses to a constant-looking `1.0` for every cell. That is a
  silent fabrication trap.
- **PSA splits long series into backcasted era tables** whose titles barely
  differ. Among predicate matches, pick the table whose Year dimension reaches
  the most recent year (`_pick_latest_table` and `_year_max`).
- **`".."` is the missing-value sentinel** (also `"..."`, `"-"`). Guard every
  float cast with `_to_float`. A missing cell is null, never zero.
- **Full-cube `filter: "all"` or `"*"` requests get a WAF HTTP 403.** Always
  select explicit item codes and keep the cell count small. An unselected
  dimension expands to all of its values, which is the same trap by accident.
- **The response `updated` field is server wall clock, not data recency.** Read
  the vintage from the table's Year or time dimension. A 1D health table's
  `Year` is not `time`-typed.
- **The rate limit is 10 requests per 10 seconds.** `psa._psa_rate_limit` is a
  token bucket, not a sliding window, and that choice is load-bearing. A cold
  `get_area_profile` makes about 11 OpenSTAT calls in one `asyncio.gather`, so a
  strict window stalls the flagship tool for a full 10 seconds. The bucket holds
  the same sustained rate and lets that burst through, and it sleeps outside its
  lock so gathered calls do not serialize. The API guide sits behind a
  Cloudflare challenge that no headless client can read, so the 10-per-10s
  figure comes from the project's own record, not a live quote.
- **Responses carry a UTF-8 BOM.** `httpx.Response.json()` handles it; a
  hand-rolled `json.loads` on raw bytes would not.
- Hardcoding a stable subject path prefix is fine for the curated tools
  (`DB/1A/PO/`, `DB/2M/PI/CPI/2018NEW/`, `DB/1B/LFS/`, `DB/1D/`). Only the
  `.px` leaf must be browse-discovered. Poverty is the exception: discover the
  subject too.

## Other source landmines

- **PHIVOLCS serves a broken certificate chain.** `PHIVOLCS_CLIENT` is the one
  client with `verify=False`. Never widen that, and never route another host
  through it.
- **Never construct a PHIVOLCS bulletin URL.** The filename pattern is
  inconsistent (4-digit and 6-digit times, an unpredictable `F` suffix). Parse
  the href from the list page.
- **PSGC resolver:** nicknames live in `LOCATION_ALIASES`; `_city_variant`
  bridges "X City" to "City of X". On v0.4.0 "Manila City" resolved to Danao
  City, Cebu at 0.61. Ambiguous names surface `alternatives`. After touching
  scoring, live-probe natural inputs ("Manila City", "QC", "San Juan"); offline
  fixtures do not cover the full PSGC name corpus.
- **PAGASA Excel files stopped on 2025-08-31.** Do not reference them.
- **PhilGEPS publishes no progress data** for any notice in the open listing.

## Tests and CI

- **Offline CI is the gate.** `pytest -m "not live"` on Python 3.11, 3.12,
  3.13, and 3.14, plus Ruff lint, Ruff format, `uv lock --check`, `uv build`,
  `twine check`, a wheel-contents check, and a fresh-process discovery check.
- **A test that hard-asserts on a real upstream with no mock is `live`.** Give
  the module `pytestmark = pytest.mark.live`. Detection: no
  `monkeypatch|mock|respx` in the file plus an import of a source module.
  The weekly `live-drift.yml` (Mondays 02:23 UTC) runs `pytest -m live`.
- **A live test degrades to a skip on an outage** and fails on drift. Use the
  `_skip_if_upstream_down` shape in `tests/test_v060_live.py`. Never weaken an
  assertion to turn a red green; fix the drift or classify the outage.
- **Read the tests before editing tested code.**

## Version surfaces

`__init__.py.__version__` is the single source; hatch reads it dynamically. The
User-Agent derives from it. These still need a manual bump every release:

- `manifest.json` `version`
- `server.json` top-level `version`
- `server.json` `packages[].version`
- README and `docs/tool-reference.md` tool counts
- the CHANGELOG entry

The official registry schema caps `server.json` `description` and `title` at
100 characters. Validate the file before you publish it.

## Self-description stays current

The `FastMCP(instructions=...)` block, `get_data_freshness`, the README, the
tool reference, and the MCP prompts and resources get updated in the same pull
request that adds or renames a tool. The v0.5.0 audit found instructions a full
version behind, which hid the flagship tool from agents.

Every tool carries a `title`, `tags`, and `annotations`. All are read-only and
idempotent. Everything that calls an upstream declares `openWorldHint: true`;
`get_data_freshness` is the only one with `false`.

## Release pipeline

release-smoke installs from PyPI on a version tag, so the order matters:

1. Merge the pull request, and capture the real merge SHA.
2. Build from that SHA in a clean worktree: `uv build`, then `twine check`.
3. `twine upload` to PyPI, then confirm PyPI serves the version.
4. `gh release create vX.Y.Z` on the merge SHA. That creates the tag and
   triggers release-smoke against the now-live wheel. Attach the `.mcpb`.
5. `mcp-publisher publish server.json`. The token in
   `~/.config/mcp-publisher/token.json` expires; on a 401 the user runs
   `mcp-publisher login github`.
6. Fresh-venv functional smoke with `pip install --no-cache-dir`.

The Registry search endpoint shows the OLDEST version by default. Confirm a new
version through the full version list, not the search summary.

`.mcpb` packing uses `npx @anthropic-ai/mcpb pack`. The canonical repo is
`modelcontextprotocol/mcpb`. `manifest_version` 0.2 is still valid; 0.4 is the
newest, and everything it added is optional.

Directory listings and their current submission policies live in
[docs/SUBMISSIONS.md](docs/SUBMISSIONS.md).

## Demos

Record a real VHS session, never a mockup or stitched screenshots. Warm the uvx
cache BEFORE invoking vhs; a fresh PyPI version inside the Hide block blows past
its Sleep and records nothing. The Hide block is just `cd && clear`. Commit the
tape. Frame-extract and read the last frame before committing any GIF.

`docs/demo_v040_may22.gif` and `docs/demo_v040_may22.tape` are untracked and
belong to the user. Leave them alone.

## Style

- Python 3.11+ syntax: `str | None`, never `Optional[str]`.
- Tools return `dict`, never a Pydantic model. Use models internally and call
  `.model_dump()`.
- Async `httpx` only, never `requests`. No disk writes; caches sit in memory.
- Never rename a tool. The name is part of the public contract.
- Every response carries `source` and `data_retrieved_at`.
- No emoji anywhere. No em-dashes in prose. No AI attribution in a commit.
