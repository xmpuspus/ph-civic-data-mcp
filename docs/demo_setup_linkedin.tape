# Records docs/demo_setup_linkedin.gif — LinkedIn-optimized variant of the setup demo.
#
# Differences from docs/demo_setup.tape:
#   - Bigger FontSize (20 vs 13) so text reads at LinkedIn's embed size.
#   - 1200x900 aspect (vs 1400x620) — closer to LinkedIn's feed viewport.
#   - Same 3 turns, same real data.
#
# Pre-requisite (run before vhs):
#   cd /tmp && echo '{"mcpServers":{"ph-civic":{"command":"uvx","args":["ph-civic-data-mcp"]}}}' > ph-mcp.json
#   uvx ph-civic-data-mcp --help >/dev/null 2>&1
#
# Usage:  vhs docs/demo_setup_linkedin.tape

Output docs/demo_setup_linkedin.gif

Require uv
Require claude

Set Shell "zsh"
Set FontSize 20
Set Width 1200
Set Height 900
Set Padding 24
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
Type `claude -p --mcp-config ph-mcp.json --strict-mcp-config --permission-mode bypassPermissions "Metro Manila heat this week? Use ph-civic MCP. Under 20 words."`
Sleep 150ms
Enter
Sleep 10s

# Scene 3 — earthquakes (PHIVOLCS)
Type `claude -p --mcp-config ph-mcp.json --strict-mcp-config --permission-mode bypassPermissions "Any Luzon quakes today? Use ph-civic MCP. Under 20 words."`
Sleep 150ms
Enter
Sleep 9s

# Scene 4 — population (PSA)
Type `claude -p --mcp-config ph-mcp.json --strict-mcp-config --permission-mode bypassPermissions "NCR population per PSA latest census? Use ph-civic MCP. Under 12 words."`
Sleep 150ms
Enter
Sleep 14s

Sleep 800ms
