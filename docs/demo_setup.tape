# Records docs/demo_setup.gif — the "15-second setup to first answer" demo.
# Every line below runs real services:
#   - `uvx ph-civic-data-mcp` boots from PyPI
#   - `claude` is Claude Code CLI, which actually calls the MCP over stdio
#   - the earthquake + Mayon answer is what Claude returns after calling the tools
#
# Usage:  vhs docs/demo_setup.tape

Output docs/demo_setup.gif

Require uv
Require claude

Set Shell "zsh"
Set FontSize 15
Set Width 1280
Set Height 560
Set Padding 20
Set Theme "Catppuccin Mocha"
Set TypingSpeed 45ms

Hide
Type "cd /tmp && clear"
Enter
Sleep 300ms
Type "cat > ph-mcp.json <<EOF"
Enter
Type '{"mcpServers":{"ph-civic":{"command":"uvx","args":["ph-civic-data-mcp"]}}}'
Enter
Type "EOF"
Enter
Type "clear"
Enter
Sleep 300ms
Show

# Scene 1 — the whole MCP config (one line of JSON)
Type "cat ph-mcp.json"
Sleep 300ms
Enter
Sleep 1800ms

# Scene 2 — point Claude Code at that config and ask a natural-language question
Type `claude -p --mcp-config ph-mcp.json --strict-mcp-config --permission-mode bypassPermissions "what's the latest PH earthquake and Mayon's current alert level?"`
Sleep 400ms
Enter

# Claude Code: spawns uvx ph-civic-data-mcp, calls tools over MCP, streams the answer.
# End-to-end cold start is ~8-12s. Give it enough runway.
Sleep 25s
