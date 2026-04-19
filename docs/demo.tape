# VHS tape — records live_demo.py into docs/demo.gif
#
# live_demo.py spawns `uvx ph-civic-data-mcp` from PyPI and makes real MCP calls;
# every panel shown is live tool output.
#
# Usage:  vhs docs/demo.tape

Output docs/demo.gif

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

# Warm uvx cache so the recording starts without a package-install detour.
Hide
Type "cd /Users/xavier/Desktop/ph-civic-data-mcp && clear"
Enter
Sleep 500ms
Show

Type "uv run python docs/live_demo.py"
Sleep 400ms
Enter

# Allow the full run: connect + 8 tool calls with spinners + closing panel.
Sleep 30s
