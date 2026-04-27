# Records docs/demo_accountability.gif — one real PH-accountability turn.
#
# Scene: Claude reaches into ph-civic-data-mcp v0.3.0 and calls
#   - resolve_ph_location  ("Pampanga" -> PSGC code)
#   - search_infra_projects (keyword=flood control, low-progress, >P50M)
#   - flag_infra_anomalies  (cross-references PHIVOLCS quakes + PAGASA typhoons)
# inside a single turn.
#
# Pre-requisite (run before vhs):
#   cd /tmp && echo '{"mcpServers":{"ph-civic":{"command":"uvx","args":["ph-civic-data-mcp"]}}}' > ph-mcp-v030.json
#   uvx --refresh ph-civic-data-mcp@0.3.0 --help >/dev/null 2>&1  # warm uvx cache to 0.3.0
#
# Usage:  vhs docs/demo_accountability.tape

Output docs/demo_accountability.gif

Require uv
Require claude

Set Shell "zsh"
Set FontSize 18
Set Width 1600
Set Height 900
Set Padding 22
Set Theme "Catppuccin Mocha"
Set TypingSpeed 18ms
Set PlaybackSpeed 1.0

Hide
Type "cd /tmp && clear"
Enter
Sleep 500ms
Show

# Scene — one civic-accountability question across three new tools
Type `claude -p --mcp-config ph-mcp-v030.json --strict-mcp-config --permission-mode bypassPermissions "Find DPWH flood-control or construction projects in Pampanga over P50M with no progress data. Use ph-civic MCP: resolve_ph_location for Pampanga, then search_infra_projects, then flag_infra_anomalies. Report the top 3 flagged projects in 4 sentences. No em-dashes."`
Sleep 300ms
Enter
Sleep 38s

# Tail
Sleep 800ms
