# Records docs/demo_correlation.gif — one real multi-source correlation turn.
#
# Scene correlates three live sources behind a single question:
#   - PAGASA / Open-Meteo 7-day forecast (v0.1.x)
#   - NASA POWER daily solar + climate (v0.2.0)
#   - Open-Meteo Air Quality (v0.2.0)
#
# Pre-requisite (run before vhs):
#   cd /tmp && echo '{"mcpServers":{"ph-civic":{"command":"uvx","args":["ph-civic-data-mcp"]}}}' > ph-mcp.json
#   uvx ph-civic-data-mcp --help >/dev/null 2>&1  # warm uvx cache
#
# Usage:  vhs docs/demo_correlation.tape

Output docs/demo_correlation.gif

Require uv
Require claude

Set Shell "zsh"
Set FontSize 13
Set Width 1400
Set Height 720
Set Padding 18
Set Theme "Catppuccin Mocha"
Set TypingSpeed 18ms
Set PlaybackSpeed 1.0

Hide
Type "cd /tmp && clear"
Enter
Sleep 500ms
Show

# Scene 1 — the real multi-source correlation question
Type `claude -p --mcp-config ph-mcp.json --strict-mcp-config --permission-mode bypassPermissions "Why is Metro Manila so hot right now? Use ph-civic MCP to pull the 7-day forecast for Manila, NASA POWER solar irradiance at lat 14.60 lng 121.00 for the last 14 days, and current air quality. Correlate the three sources in 3 sentences."`
Sleep 300ms
Enter
Sleep 32s

# Tail
Sleep 800ms
