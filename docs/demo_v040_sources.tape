# Records docs/demo_v040.gif — the v0.4.0 per-source tour.
#
# docs/live_demo_v040.py drives the LOCAL v0.4.0 server over the real MCP
# stdio protocol (pre-release: not on PyPI yet) and renders real tool JSON
# with Rich: national + regional inflation, Labor Force Survey rates, national
# health indicators, and the one-call auto-stitch area profile.
#
# Usage:  vhs docs/demo_v040_sources.tape

Output docs/demo_v040.gif

Require uv

Set Shell "zsh"
Set FontSize 14
Set Width 1280
Set Height 800
Set Padding 20
Set Theme "Catppuccin Mocha"
Set LetterSpacing 0
Set TypingSpeed 50ms
Set PlaybackSpeed 1.0

# Warm the local editable build so the recorded run starts without a
# uv-build/resolve detour (the cold first `uv run` compiles the package).
Hide
Type "cd /Users/xavier/Desktop/ph-civic-data-mcp && uv run python -c 'import ph_civic_data_mcp' >/dev/null 2>&1 && clear"
Enter
Sleep 800ms
Show

Type "uv run python docs/live_demo_v040.py"
Sleep 400ms
Enter

# Connect + 5 tool calls with spinners + closing panel (live run ~8s; pad for
# the MCP connect handshake and PSA round-trips).
Sleep 45s
Sleep 3s
