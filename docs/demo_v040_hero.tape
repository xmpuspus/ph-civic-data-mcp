# Records docs/demo_v040_hero.gif — the LinkedIn showcase, in the exact
# compact style of docs/demo_setup.tape. Real `claude -p` natural-language
# turns hitting live APIs through ph-civic-data-mcp v0.4.0.
#
# Story:
#   1. the one-line MCP config (setup simplicity)
#   2. one plain question -> get_area_profile("Cebu") silently stitches
#      PSGC + PSA + PhilGEPS + PHIVOLCS + PAGASA into one coherent answer
#   3. a comparative question -> two get_area_profile calls correlated into
#      a defensible, flagged-for-review read no single source can give
#
# Pre-release: v0.4.0 is not on PyPI, so the config runs the LOCAL branch
# over the real MCP stdio protocol. At release time, re-tape against
# `uvx ph-civic-data-mcp@0.4.0`. The local editable build is warmed in the
# Hide block so the cold `uv run` build does not eat into a recorded turn.
#
# Pre-requisite (run before vhs):
#   cd /tmp && echo '{"mcpServers":{"ph-civic":{"command":"uv","args":["run","--directory","/Users/xavier/Desktop/ph-civic-data-mcp","ph-civic-data-mcp"]}}}' > ph-mcp-v040.json
#
# Long on purpose; trim to the key frames in post.
#
# Usage:  vhs docs/demo_v040_hero.tape

Output docs/demo_v040_hero.gif

Require uv
Require claude

Set Shell "zsh"
Set FontSize 13
Set Width 1400
Set Height 700
Set Padding 18
Set Theme "Catppuccin Mocha"
Set TypingSpeed 18ms
Set PlaybackSpeed 1.0

Hide
Type "cd /tmp && uv run --directory /Users/xavier/Desktop/ph-civic-data-mcp python -c 'import ph_civic_data_mcp' >/dev/null 2>&1 && clear"
Enter
Sleep 800ms
Show

# Scene 1 — the entire MCP config (setup simplicity, same beat as demo_setup)
Type "cat ph-mcp-v040.json | python3 -m json.tool"
Sleep 150ms
Enter
Sleep 1200ms

# Scene 2 — one plain question; the agent silently stitches five sources
Type `claude -p --mcp-config ph-mcp-v040.json --strict-mcp-config --permission-mode bypassPermissions "Civic picture of Cebu — population, poverty, inflation, jobs, earthquake risk — in 3 sentences. Use ph-civic MCP. Cite sources. No em-dashes."`
Sleep 150ms
Enter
Sleep 38s

# Scene 3 — the correlation only the v0.4.0 economic denominator makes possible
Type `claude -p --mcp-config ph-mcp-v040.json --strict-mcp-config --permission-mode bypassPermissions "Compare Eastern Visayas and Central Visayas: which is more underserved relative to its poverty and population given recent infra-notice activity and hazard exposure? Use ph-civic MCP. Flagged-for-review language, 3 sentences, cite source_url, include the disclaimer. No em-dashes, no accusations."`
Sleep 150ms
Enter
# Two get_area_profile calls (~8 upstreams each) + comparative reasoning.
# Generous budget; dead air is trimmed in post.
Sleep 105s

# Tail
Sleep 1500ms
