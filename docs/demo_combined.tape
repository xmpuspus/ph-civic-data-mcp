# VHS tape — records the cross-source suite into docs/demo_combined.gif
#
# live_demo_single.py spawns `uvx ph-civic-data-mcp` from PyPI and makes a
# real assess_area_risk MCP call; the panel shown is live tool output
# (including the v0.5.0 volcano_alerts block).
#
# Usage:  vhs docs/demo_combined.tape

Output docs/demo_combined.gif

Require uv

Set Shell "zsh"
Set FontSize 14
Set Width 1280
Set Height 780
Set Padding 20
Set Theme "Catppuccin Mocha"
Set LetterSpacing 0
Set TypingSpeed 50ms
Set PlaybackSpeed 1.0

# Warm the uvx cache BEFORE running this tape (`uvx ph-civic-data-mcp@latest`)
# or the recording stalls on a package-install detour.
Hide
Type "cd /Users/xavier/Desktop/ph-civic-data-mcp && clear"
Enter
Sleep 1s
Show

Type "uv run python docs/live_demo_single.py combined"
Sleep 400ms
Enter

# Connect + assess_area_risk (4 parallel upstreams) + closing panel.
Sleep 30s
