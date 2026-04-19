# Records docs/demo_setup.gif — conversational 4-turn demo across all 4 PH data sources.
# Every turn is a real `claude -p` call hitting live APIs through ph-civic-data-mcp on PyPI.
#
# Turns:
#   1. weather      → PAGASA / Open-Meteo
#   2. earthquakes  → PHIVOLCS
#   3. procurement  → PhilGEPS
#   4. population   → PSA OpenSTAT
#
# Usage:  vhs docs/demo_setup.tape

Output docs/demo_setup.gif

Require uv
Require claude

Set Shell "zsh"
Set FontSize 13
Set Width 1400
Set Height 760
Set Padding 18
Set Theme "Catppuccin Mocha"
Set TypingSpeed 22ms

Hide
Type "cd /tmp && clear"
Enter
Sleep 200ms
Type "cat > ph-mcp.json <<'EOF'"
Enter
Type '{"mcpServers":{"ph-civic":{"command":"uvx","args":["ph-civic-data-mcp"]}}}'
Enter
Type "EOF"
Enter
Type "clear"
Enter
Sleep 200ms
Show

# Scene 1 — the entire MCP config (shows setup simplicity)
Type "cat ph-mcp.json | python3 -m json.tool"
Sleep 250ms
Enter
Sleep 1500ms

# Scene 2 — weather (PAGASA / Open-Meteo)
Type `claude -p --mcp-config ph-mcp.json --strict-mcp-config --permission-mode bypassPermissions "How hot will Metro Manila be this weekend? Use ph-civic MCP. Under 35 words."`
Sleep 200ms
Enter
Sleep 18s

# Scene 3 — earthquakes (PHIVOLCS)
Type `claude -p --mcp-config ph-mcp.json --strict-mcp-config --permission-mode bypassPermissions "Any earthquakes in Luzon in the last 12 hours? Use ph-civic MCP. Under 35 words."`
Sleep 200ms
Enter
Sleep 16s

# Scene 4 — procurement (PhilGEPS)
Type `claude -p --mcp-config ph-mcp.json --strict-mcp-config --permission-mode bypassPermissions "Search PhilGEPS for flood control contracts via ph-civic MCP. List 3 with agency. Under 50 words."`
Sleep 200ms
Enter
Sleep 22s

# Scene 5 — population (PSA)
Type `claude -p --mcp-config ph-mcp.json --strict-mcp-config --permission-mode bypassPermissions "What is NCR's population per the latest PSA census? Use ph-civic MCP. Under 25 words."`
Sleep 200ms
Enter
Sleep 14s

# Tail — let the final answer settle before cut
Sleep 1500ms
