# Directory Submissions — ph-civic-data-mcp

Reference for manual submissions to MCP directories. Copy/paste the content into each form.

## 1. MCP Servers (replaces wong2/awesome-mcp-servers)

> Submission form: https://mcpservers.org/submit

**Name:** ph-civic-data-mcp

**Short description (one line):**
> The first MCP server for Philippine government data — earthquakes, weather, typhoons, procurement, population, air quality.

**Long description:**
> ph-civic-data-mcp is a zero-cost stdio MCP server that exposes live data from PHIVOLCS (earthquakes, volcano alert levels), PAGASA (weather forecast with Open-Meteo fallback, typhoons, alerts), PhilGEPS (government procurement notices), PSA OpenSTAT (2020 Census population, 2023 Full-Year poverty), and AQICN/EMB (real-time air quality for PH cities) as agent-callable tools. It includes a cross-source multi-hazard risk profiler that makes parallel calls across sources.
>
> Install: `uvx ph-civic-data-mcp`

**Category:** Open Data / Government / Civic Tech

**Repository:** https://github.com/xmpuspus/ph-civic-data-mcp

**PyPI:** https://pypi.org/project/ph-civic-data-mcp/

**License:** MIT

**Tags:** philippines, phivolcs, pagasa, philgeps, psa, civic-tech, open-data, earthquake, weather, typhoon, procurement, census, air-quality

## 2. Smithery

> https://smithery.ai/new

The `smithery.yaml` at the repo root declares the stdio startCommand and config schema (aqicnToken, pagasaApiToken).

After importing the repo on Smithery, set:
- Display name: `ph-civic-data-mcp`
- Description (see above)
- Homepage: https://github.com/xmpuspus/ph-civic-data-mcp
- Categories: Government, Open Data, Research

## 3. PulseMCP

> https://www.pulsemcp.com/submit

Same short + long description. PulseMCP categorizes under "Government / Civic / Open Data".

## 4. MCP.so

> https://mcp.so/submit

Same content. Include:
- GitHub URL
- PyPI URL
- Screenshot: `docs/demo.gif`

## 5. Glama

Glama auto-indexes from PyPI within 48h — no manual submission required. After that, it appears at:
- https://glama.ai/mcp/servers/<slug>

## 6. Community posts

**Data Engineering Pilipinas** (FB group, ~38k members)

> Just shipped `ph-civic-data-mcp` — the first MCP server that exposes Philippine government data (PHIVOLCS earthquakes, PAGASA weather + typhoons, PhilGEPS procurement, PSA population + poverty, AQICN air quality) as tools any AI agent can call directly.
>
> Install: `uvx ph-civic-data-mcp`
> Source: https://github.com/xmpuspus/ph-civic-data-mcp
>
> 12 tools including a cross-source multi-hazard risk profiler. Zero hosting cost, MIT licensed. Open to contributions — NDRRMC situational reports and HazardHunterPH coordinate risk assessment are on the v0.2.0 roadmap.

**DEVCON Philippines** — same copy, adapted to Slack/FB norms.

**LinkedIn**

> Spent the weekend building `ph-civic-data-mcp` — the first MCP server for Philippine government data.
>
> It gives Claude, Cursor, and any MCP client direct access to PHIVOLCS earthquake feeds, PAGASA weather and typhoon tracking, PhilGEPS procurement, PSA census and poverty stats, and AQICN air quality — as agent-callable tools. Zero prior art on GitHub or PyPI.
>
> 12 tools. Async httpx. Open-Meteo fallback when PAGASA's token-gated API is unavailable. Parallel cross-source risk profiler via asyncio.gather. MIT licensed.
>
> `uvx ph-civic-data-mcp`
>
> Repo: https://github.com/xmpuspus/ph-civic-data-mcp
> PyPI: https://pypi.org/project/ph-civic-data-mcp/
