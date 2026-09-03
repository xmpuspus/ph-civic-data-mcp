# syntax=docker/dockerfile:1

# Pin by digest, not a mutable tag, so a retagged upstream image cannot
# change what ships. Resolved from python:3.12-slim on 2026-09-03.
FROM python:3.12-slim@sha256:78387bc3881b8273120a12ebe6c1ab22b018ccc2c9adf565ae1ac9b536e184ea AS builder

# This copies the uv binary from the pinned official uv image, instead of
# installing it with an unpinned script. Resolved from ghcr.io/astral-sh/uv:0.9.8.
COPY --from=ghcr.io/astral-sh/uv:0.9.8@sha256:08f409e1d53e77dfb5b65c788491f8ca70fe1d2d459f41c89afa2fcbef998abe /uv /uvx /bin/

WORKDIR /app

# uv.lock pins every transitive dependency. --frozen fails the build instead
# of silently re-resolving if the lockfile ever drifts from pyproject.toml.
COPY pyproject.toml uv.lock README.md LICENSE ./
COPY src ./src

# --no-dev skips pytest/ruff, the runtime never needs them.
# --no-editable bakes the project into site-packages, so the runtime stage
# needs only the venv, never the source tree.
RUN uv sync --frozen --no-dev --no-editable

# Runtime stage: no compiler, no uv, no lockfile, no source tree.
FROM python:3.12-slim@sha256:78387bc3881b8273120a12ebe6c1ab22b018ccc2c9adf565ae1ac9b536e184ea AS runtime

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
 && rm -rf /var/lib/apt/lists/*

# Read-only data server — no reason to run as root.
RUN useradd --create-home --shell /usr/sbin/nologin mcp

WORKDIR /app
COPY --from=builder --chown=mcp:mcp /app/.venv /app/.venv
ENV PATH="/app/.venv/bin:$PATH"

USER mcp

# This server is stdio-only with no HTTP port to poll, so a curl-style
# healthcheck does not apply. server.py runs _register_tools() at import
# time, so importing the module here proves the dependency set and the tool
# registry both still work. This check never opens a real stdio session.
HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
    CMD ["python", "-c", "import ph_civic_data_mcp.server"]

# stdio MCP server: the client that spawns this container speaks MCP over
# stdin/stdout.
CMD ["ph-civic-data-mcp"]
