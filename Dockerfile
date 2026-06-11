FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY pyproject.toml README.md ./
COPY src ./src

RUN pip install --no-cache-dir .

# Read-only data server — no reason to run as root.
RUN useradd --create-home --shell /usr/sbin/nologin mcp
USER mcp

# stdio MCP server — Glama boots this and speaks MCP over stdin/stdout
CMD ["ph-civic-data-mcp"]
