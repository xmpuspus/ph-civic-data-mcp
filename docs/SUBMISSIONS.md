# Where the package is listed, and its update state at each destination

Where `ph-civic-data-mcp` is listed, how each listing updates, and what a
release still needs a human to click. Inventory taken live on 2026-08-06,
then updated the same day after the v0.6.0 release shipped. Updated again
on 2026-09-03 after the v0.6.1 release, and on 2026-09-04 after v0.7.0.

Identifiers, so every form gets the same values:

| Field | Value |
|---|---|
| Package | `ph-civic-data-mcp` on PyPI |
| MCP Registry name | `io.github.xmpuspus/ph-civic-data-mcp` |
| Repository | `https://github.com/xmpuspus/ph-civic-data-mcp` |
| Install | `uvx ph-civic-data-mcp` |
| License | MIT |
| Transport | stdio |
| Auth | None required. `PAGASA_API_TOKEN` is optional. |
| Tools / prompts / resources | 34 / 3 / 2 |
| Current version | 0.7.0 |

## Copy to paste into a form

**One line:**

> Philippine civic data as MCP tools: browse, describe, and query the full PSA
> OpenSTAT statistical catalog, plus hazards, weather, procurement, and
> one-call area profiles. 34 tools, no API keys.

**Long:**

> `ph-civic-data-mcp` is a zero-cost stdio MCP server that exposes Philippine
> civic data as agent-callable tools. Version 0.6.0 opens the whole PSA
> OpenSTAT catalog, about 2,900 statistical tables, through three tools:
> browse the hierarchy, describe a dataset's dimensions and value codes, then
> run one bounded query with explicit selections and a hard cell ceiling.
>
> It also covers PSGC location resolution, PHIVOLCS earthquakes and volcano
> alerts, PAGASA weather and typhoons with an Open-Meteo fallback, PhilGEPS
> procurement and infra-spending accountability, PSA population, poverty,
> inflation, labor and health statistics, NASA POWER solar and climate,
> Open-Meteo air quality, NASA MODIS vegetation indices, USGS earthquakes,
> NOAA IBTrACS historical typhoons, and World Bank macro indicators.
>
> `get_area_profile` composes a whole place profile in one call. On upstream
> failure, tools return an explicit error envelope rather than an empty list,
> so an outage never reads as "no data".
>
> Version 0.7.0 adds `compare_areas`, which lines up 2 to 5 places on the
> same metrics and vintages, and `search_psa_catalog`, a keyword search over
> the OpenSTAT catalog. Every tool now carries a `data_status` field, so an
> agent can tell success, empty, unavailable, indeterminate, and a bad
> request apart.
>
> Install: `uvx ph-civic-data-mcp`

**Categories:** Government, Open Data, Civic Tech, Research, Statistics

**Tags:** philippines, openstat, psa, psgc, phivolcs, pagasa, philgeps,
civic-tech, open-data, statistics, earthquake, weather, typhoon, procurement,
accountability

## Where it is listed today

State after the v0.7.0 release on 2026-09-04.

| Destination | v0.7.0 status | How it updates | What is left |
|---|---|---|---|
| [PyPI](https://pypi.org/project/ph-civic-data-mcp/0.7.0/) | active, checked live | `twine upload` | Nothing |
| [GitHub Release](https://github.com/xmpuspus/ph-civic-data-mcp/releases/tag/v0.7.0) | active, checked live, release-smoke green | `gh release create` on the merge SHA | Nothing |
| Repository description and topics | active, description says 34 tools | `gh repo edit` | Nothing |
| [MCP Registry](https://registry.modelcontextprotocol.io) | 0.7.0 active and isLatest, read back from the versions endpoint on 2026-09-04 after a fresh device login | `mcp-publisher publish server.json` | Nothing. The JWT expires between releases, so run `mcp-publisher login github` on a 401 |
| [Glama](https://glama.ai/mcp/servers/xmpuspus/ph-civic-data-mcp) | showed 0.6.0 and 32 tools on 2026-09-04, two releases behind | Auto-crawls GitHub and PyPI | Check the page again in a week. Claiming the server is a separate one-time step |
| [PulseMCP](https://www.pulsemcp.com/servers/xmpuspus-ph-civic-data) | auto-indexed. The page showed no version string on 2026-09-04 | Tracks the registry record | Nothing to do by hand |
| [punkpeye/awesome-mcp-servers](https://github.com/punkpeye/awesome-mcp-servers/pull/6824) | submitted, still open, last activity 2026-08-07, checked live 2026-09-04 | Pull request | Their merge |
| [TensorBlock/awesome-mcp-servers](https://github.com/TensorBlock/awesome-mcp-servers/pull/1597) | active. The entry moved from the README into `docs/science--research.md`, checked live 2026-09-04 | Pull request | Nothing. The merged entry carries `auth.type: none` |
| [Smithery](https://smithery.ai) | not listed | Connect the repository from a logged-in dashboard | New submission, needs a login |
| [MCP.so](https://mcp.so/submit) | not listed | Web form | New submission, JS form |
| [mcpservers.org](https://mcpservers.org/submit) | not listed | Web form | New submission, JS form |
| [LobeHub](https://lobehub.com/mcp/xmpuspus-ph-civic-data-mcp) | listed, showing 0.1.2 as of 2026-08-06, not re-checked this release | Unknown, the page sits behind bot protection | Needs a real browser to find the update path |
| [Vibehackers](https://vibehackers.io/mcp/ph-civic-data-mcp) | listed, tool count stale as of 2026-08-06, not re-checked this release | "Submit Project" link | Needs a real browser |

Two lists closed a submission without a review comment:
`brandonhimpfen/awesome-civic-tech` (PR 7) and
`brandonhimpfen/awesome-open-governance` (PR 4). Their CONTRIBUTING declines
promotion-framed entries. Reframe any resubmission as filling a gap in their
taxonomy.

`wong2/awesome-mcp-servers` takes no direct pull request. Use the
mcpservers.org form.

## Release order

PyPI first. Every downstream check reads the package version from there.

1. `twine upload dist/*`, then confirm PyPI serves the version.
2. `gh release create vX.Y.Z`, which triggers release-smoke against the live
   wheel.
3. `mcp-publisher publish server.json`. Bump both the top-level `version` and
   `packages[].version` first. Both are manual.
4. Read the full registry version list, not the search summary. Search shows
   the oldest version by default.
5. Glama and PulseMCP follow on their own within about 48 hours.
6. Everything else in the table needs a human.

PyPI ownership check: the registry looks for an `mcp-name:` marker in the
PyPI-rendered README. It sits at the top of `README.md` as an HTML comment.

If `mcp-publisher publish` returns 401, the login token expired. Run
`mcp-publisher login github` and retry.
