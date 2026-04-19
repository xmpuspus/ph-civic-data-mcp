# Records docs/demo_setup.gif — "one JSON file, one command, Claude correlates live PH data".
# Everything below runs live:
#   - `uvx ph-civic-data-mcp` boots from PyPI
#   - `claude` is Claude Code CLI
#   - Claude fans out to Open-Meteo (weather) and PSA OpenSTAT (census) in one turn
#
# Usage:  vhs docs/demo_setup.tape

Output docs/demo_setup.gif

Require uv
Require claude

Set Shell "zsh"
Set FontSize 14
Set Width 1280
Set Height 620
Set Padding 20
Set Theme "Catppuccin Mocha"
Set TypingSpeed 40ms

Hide
Type "cd /tmp && clear"
Enter
Sleep 300ms
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

# Scene 1 — the entire MCP config
Type "cat ph-mcp.json | python3 -m json.tool"
Sleep 300ms
Enter
Sleep 1600ms

# Scene 2 — one natural-language question, Claude fans out to weather + census
Type `claude -p --mcp-config ph-mcp.json --strict-mcp-config --permission-mode bypassPermissions "How hot is Metro Manila this week and how many people are affected? Use only the ph-civic MCP tools, under 80 words."`
Sleep 400ms
Enter

# Multi-tool fan-out: Open-Meteo + PSA PXWeb in parallel, ~12-18s total
Sleep 26s
