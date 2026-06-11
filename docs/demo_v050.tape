# VHS tape — records live_demo_v050.py into docs/demo_v050.gif
#
# live_demo_v050.py spawns `uvx ph-civic-data-mcp` from PyPI and makes real
# MCP calls; every panel shown is live tool output. Warm the uvx cache BEFORE
# running this tape (`uvx ph-civic-data-mcp@latest`) or the recording stalls
# on a package-install detour.
#
# Usage:  vhs docs/demo_v050.tape

Output docs/demo_v050.gif

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

Hide
Type "cd /Users/xavier/Desktop/ph-civic-data-mcp && clear"
Enter
Sleep 1s
Show

Type "uv run python docs/live_demo_v050.py"
Sleep 400ms
Enter

# Connect + 3 resolve calls + assess_area_risk (4 parallel upstreams) + tail.
Sleep 45s
