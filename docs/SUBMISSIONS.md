# Directory listings

Where `ph-civic-data-mcp` is listed, how each listing updates, and what a
release still needs a human to click. Inventory taken live on 2026-08-06, then
updated the same day after the v0.6.0 release shipped.

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
| Tools / prompts / resources | 32 / 3 / 2 |

## Copy to paste into a form

**One line:**

> Philippine civic data as MCP tools: browse, describe, and query the full PSA
> OpenSTAT statistical catalog, plus hazards, weather, procurement, and
> one-call area profiles. 32 tools, no API keys.

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
> Install: `uvx ph-civic-data-mcp`

**Categories:** Government, Open Data, Civic Tech, Research, Statistics

**Tags:** philippines, openstat, psa, psgc, phivolcs, pagasa, philgeps,
civic-tech, open-data, statistics, earthquake, weather, typhoon, procurement,
accountability

## Where it is listed today

State after the v0.6.0 release on 2026-08-06.

| Destination | v0.6.0 status | How it updates | What is left |
|---|---|---|---|
| [PyPI](https://pypi.org/project/ph-civic-data-mcp/) | active | `twine upload` | Nothing |
| [GitHub Release](https://github.com/xmpuspus/ph-civic-data-mcp/releases/tag/v0.6.0) | active | `gh release create` on the merge SHA | Nothing |
| Repository description and topics | active | `gh repo edit` | Nothing |
| [MCP Registry](https://registry.modelcontextprotocol.io) | pending a login | `mcp-publisher publish server.json` | The token expired. Run `mcp-publisher login github`, then publish from the repository root |
| [Glama](https://glama.ai/mcp/servers/xmpuspus/ph-civic-data-mcp) | auto-indexed, unverified | Auto-crawls GitHub and PyPI | Check the page shows 0.6.0 and 32 tools. Claiming the server is a separate one-time step |
| [PulseMCP](https://www.pulsemcp.com/servers/xmpuspus-ph-civic-data) | auto-indexed, unverified | Tracks the registry record | Waits on the registry |
| [punkpeye/awesome-mcp-servers](https://github.com/punkpeye/awesome-mcp-servers/pull/6824) | submitted, pending review | Pull request | Their merge |
| [TensorBlock/awesome-mcp-servers](https://github.com/TensorBlock/awesome-mcp-servers/pull/1597) | submitted, pending review | Pull request | Their merge. The same PR corrects `auth.type` from `api-key` to `none` |
| [Smithery](https://smithery.ai) | not listed | Connect the repository from a logged-in dashboard | New submission, needs a login |
| [MCP.so](https://mcp.so/submit) | not listed | Web form | New submission, JS form |
| [mcpservers.org](https://mcpservers.org/submit) | not listed | Web form | New submission, JS form |
| [LobeHub](https://lobehub.com/mcp/xmpuspus-ph-civic-data-mcp) | listed, showing 0.1.2 | Unknown, the page sits behind bot protection | Needs a real browser to find the update path |
| [Vibehackers](https://vibehackers.io/mcp/ph-civic-data-mcp) | listed, tool count stale | "Submit Project" link | Needs a real browser |

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
   `packages[].version` first; they are manual.
4. Read the full registry version list, not the search summary. Search shows
   the oldest version by default.
5. Glama and PulseMCP follow on their own within about 48 hours.
6. Everything else in the table needs a human.

PyPI ownership check: the registry looks for an `mcp-name:` marker in the
PyPI-rendered README. It sits at the top of `README.md` as an HTML comment.

If `mcp-publisher publish` returns 401, the login token expired. Run
`mcp-publisher login github` and retry.
