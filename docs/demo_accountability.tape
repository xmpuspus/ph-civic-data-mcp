# Records docs/demo_accountability.gif — one real PH-accountability turn
# exercising the v0.3.1 fixes (PSGC inversion + province expansion + clean
# anomaly indicators).
#
# Scene: Claude reaches into ph-civic-data-mcp v0.3.1 and calls
#   - resolve_ph_location  ("Sta. Mesa, Manila" -> PSGC City of Manila;
#     v0.3.1 city_to_coords now handles the City-of-X inversion)
#   - search_infra_projects (keyword=flood control, province=Pampanga;
#     v0.3.1 _PROVINCE_AGENCY_HINTS expands to "Region III" so DPWH agency
#     names match)
#   - flag_infra_anomalies   (v0.3.1 _proper_noun_tokens + _HAZARD_STOPWORDS
#     no longer fire on tokens like "city")
# inside a single turn.
#
# Pre-requisite (run before vhs):
#   cd /tmp && echo '{"mcpServers":{"ph-civic":{"command":"uvx","args":["ph-civic-data-mcp"]}}}' > ph-mcp-v031.json
#   uvx --refresh ph-civic-data-mcp@0.3.1 --help >/dev/null 2>&1  # warm uvx cache to 0.3.1
#
# Post-process: ffmpeg mpdecimate compresses idle frames.
#   ffmpeg -i docs/demo_accountability.gif -vf "mpdecimate,setpts=N/FRAME_RATE/TB" \
#     -loop 0 docs/demo_accountability.gif.tmp && \
#     mv docs/demo_accountability.gif.tmp docs/demo_accountability.gif
#
# Usage:  vhs docs/demo_accountability.tape

Output docs/demo_accountability.gif

Require uv
Require claude

Set Shell "zsh"
Set FontSize 22
Set Width 1920
Set Height 1080
Set Padding 28
Set Theme "Catppuccin Mocha"
Set TypingSpeed 18ms
Set PlaybackSpeed 1.0

Hide
Type "cd /tmp && clear"
Enter
Sleep 500ms
Show

# Scene — one civic-accountability question that exercises the v0.3.1 fixes
Type `claude -p --mcp-config ph-mcp-v031.json --strict-mcp-config --permission-mode bypassPermissions "Use ph-civic MCP. First resolve_ph_location for 'Sta. Mesa, Manila' to confirm PSGC. Then search_infra_projects with keyword='flood control', province='Pampanga', limit=5. Then flag_infra_anomalies with province='Pampanga'. Report what each tool returned in 4 plain sentences with citations and the disclaimer. No em-dashes."`
Sleep 300ms
Enter
# Sleep covers the four-tool turn end-to-end. mpdecimate post-process trims
# idle frames so the playback feels closer to 25s without truncating Claude
# mid-answer.
Sleep 35s

# Hold final frame so the answer stays on screen before the loop restarts
Sleep 4s
