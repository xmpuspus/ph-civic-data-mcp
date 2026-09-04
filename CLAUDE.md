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
| Current version | 0.8.0 |
| Surface | 41 tools, 3 prompts, 2 resources, 19 public sources |
| Transport | stdio only, zero hosting cost |
| Python | 3.11+, tested on 3.11 through 3.14 |
| Framework | `fastmcp>=4.0.0,<5.0.0` (4.0.2 on MCP SDK 2.1.1 as of 2026-09-04, adopted per `docs/fastmcp-4-evaluation.md`) |
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

**Never fall back to a default when a parse fails.** Return an envelope. Two
real ones: an unreadable population cell became `population = 0` and cached for
24 hours, and an unreadable year label became a hardcoded 2023. Both published
a figure nobody measured. Use `_first_cell` and `_year_from_label`.

**Never index a PXWeb `values` field without a type check.** PXWeb sends a
list. When it sent the string `"10.9"`, `values[0]` handed back `"1"` and that
went out as a statistic. Same for `variables`, `valueTexts`, and a row that is
not a dict. Coerce every published code and label with `str()`; a numeric
`code` raised AttributeError on `.lower()` and killed the whole tool.

**Match a place name on token boundaries, never on a bare substring.** The
region code `"I"` matched `"philippines"`, so `get_poverty_stats(region="I")`
returned the national 10.9 percent labelled as Region I. A later fix that fell
back to a raw substring let `"region i"` match `"region ii"`. Use
`_token_match`, and live-probe the real 108-entry geolocation list after any
change.

**Validate a caller-supplied string before it reaches a URL.**
`get_world_bank_indicator` appended its argument to the Philippines endpoint,
and httpx normalizes a path, so
`"../../../country/USA/indicator/SP.POP.TOTL"` returned United States
population under this server's hardcoded "Philippines" label. Every
caller-supplied path or code needs a shape check first.

**Single-flight any cache-check-then-await-fetch.** Twenty concurrent cold
calls for one table queued twenty identical GETs, and the later ones blew their
own timeout while the first result sat unused. See `psa._browse_lock` and
`psa_catalog._meta_lock`. Both registries are bounded, and eviction spares a
lock somebody holds.

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
- **A test that drives a timer runs on a fake clock.** The first rate-limiter
  test monkeypatched `asyncio.sleep` to a no-op that never advanced anything,
  so the limiter busy-looped for 10 seconds of real CPU while asserting the
  wrong thing. See `_fake_clock` in `tests/test_v060_fixes.py`.
- **A ceiling nobody exercises is not tested.** `MAX_ROWS_CEILING` is 5000 and
  the fixture had 2 rows, so the truncation assertion passed for free.

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
idempotent. Everything that calls an upstream declares `openWorldHint: true`.
`get_data_freshness` is the only one with `false`.

**FastMCP cuts the client-facing description at the first standalone
Google-style header.** `Args:`, `Returns:`, and a bare `Examples:` line all
stop it. Before v0.7.0, 25 of 34 tools reached an agent with no failure shape
and 29 with no example, because both sat after `Args:`. Put the one-line
summary, the example calls, and the `On failure:` sentence before `Args:`.
Fold the `Examples:` label onto the tail of the prior sentence, never on its
own line. Check with `mcp.list_tools()`: every description must contain
`Examples:` and `On failure` and must not contain `Args:`.

## CI and packaging landmines

- **Check that every action ref exists before you push a workflow.**
  `gh api repos/<owner>/<repo>/git/ref/tags/<ref>`. `astral-sh/setup-uv`
  stopped publishing floating major tags at v8, so `@v9` is a 404 and every job
  fails at the install step. Pin `@v9.0.0`. `checkout@v7`, `setup-python@v7`
  and `upload-artifact@v7` do resolve.
- **Never interpolate `${{ }}` into a `run:` body.** GitHub substitutes it into
  the shell source before bash runs, so a validation check below it cannot
  help; a crafted `workflow_dispatch` input executes as a command. Pass the
  value through `env:` and read the variable.
- **`uv sync` reads `.python-version` unless you pass `--python`.** The
  live-drift job installed 3.12 and then built its environment on 3.11, so its
  own name lied about what it exercised.
- **`.mcpbignore` carries the secret patterns and excludes `docs/*.gif`.** It
  claimed to mirror `.gitignore` and carried none of them, so an untracked
  `.env` or demo recording in a working tree would land in a published bundle.
  Excluding the GIFs also took the bundle from 5.0 MB to 148 KB.
- **Run `pip-audit` every release.** A two-month-old lockfile carried 14 known
  vulnerabilities across 9 transitive packages. Three rounds of
  `uv lock --upgrade-package` cleared it.
- **`Client("ph-civic-data-mcp")` does not infer a transport.** FastMCP raises
  `ValueError`. Build `StdioTransport(command=..., args=[])` explicitly.
- **PyPI lags its own upload.** `twine` prints the release URL, and the JSON
  API and the pip index need about a minute more. Poll until the version
  appears rather than calling the upload failed.

## Release pipeline

release-smoke installs from PyPI on a version tag, so the order matters:

1. Merge the pull request, and capture the real merge SHA.
2. Build from that SHA in a clean worktree: `uv build`, then `twine check`.
3. `twine upload` to PyPI, then confirm PyPI serves the version. This step
   stays manual until a PyPI maintainer registers the trusted publisher for
   `publish.yml`.
4. `gh release create vX.Y.Z` on the merge SHA. That creates the tag and
   triggers release-smoke against the now-live wheel. Attach the `.mcpb`.
   The release event also starts `publish.yml`, which skips its PyPI upload
   when the version already exists (`skip-existing: true`) and then attaches
   the provenance attestation and SBOM. So the twine path and the workflow do
   not collide.
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
