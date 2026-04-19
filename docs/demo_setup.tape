# Records docs/demo_setup.gif — tight 3-turn demo across 3 PH data sources.
# Each turn is a real `claude -p` call hitting live APIs through ph-civic-data-mcp.
#
# Turns:
#   1. weather      → PAGASA / Open-Meteo
#   2. earthquakes  → PHIVOLCS
#   3. population   → PSA OpenSTAT
#
# Pre-requisite (run before vhs):
#   cd /tmp && echo '{"mcpServers":{"ph-civic":{"command":"uvx","args":["ph-civic-data-mcp"]}}}' > ph-mcp.json
#   uvx ph-civic-data-mcp --help >/dev/null 2>&1  # warm uvx cache
#
# Usage:  vhs docs/demo_setup.tape

Output docs/demo_setup.gif

Require uv
Require claude

Set Shell "zsh"
Set FontSize 13
Set Width 1400
Set Height 620
Set Padding 18
Set Theme "Catppuccin Mocha"
Set TypingSpeed 18ms
Set PlaybackSpeed 1.0

Hide
Type "cd /tmp && clear"
Enter
Sleep 500ms
Show

# Scene 1 — the entire MCP config (shows setup simplicity)
Type "cat ph-mcp.json | python3 -m json.tool"
Sleep 150ms
Enter
Sleep 900ms

# Scene 2 — weather (PAGASA / Open-Meteo)
Type `claude -p --mcp-config ph-mcp.json --strict-mcp-config --permission-mode bypassPermissions "How hot is Metro Manila this week? Use ph-civic MCP. Under 25 words."`
Sleep 150ms
Enter
Sleep 10s

# Scene 3 — earthquakes (PHIVOLCS)
Type `claude -p --mcp-config ph-mcp.json --strict-mcp-config --permission-mode bypassPermissions "Any earthquakes in Luzon today? Use ph-civic MCP. Under 25 words."`
Sleep 150ms
Enter
Sleep 9s

# Scene 4 — population (PSA)
Type `claude -p --mcp-config ph-mcp.json --strict-mcp-config --permission-mode bypassPermissions "NCR population per latest PSA census? Use ph-civic MCP. Under 15 words."`
Sleep 150ms
Enter
Sleep 12s

# Tail
Sleep 800ms
