"""Generate the README terminal GIF showing Claude Code + ph-civic-data-mcp.

Run: uv run --with Pillow python3 docs/make_demo.py
"""

from __future__ import annotations

import sys

sys.path.insert(0, "/Users/xavier/.claude/skills/terminal-gif")

from terminal_gif import (  # noqa: E402
    CB,
    TerminalGIF,
    C,
    D,
    F,
    G,
    K,
    N,
    O,
    P,
    R,
    S,
    Y,
    check,
)


def build() -> None:
    gif = TerminalGIF(preset="full", title="claude — ph-civic-data-mcp")
    gif.pause(400)

    # Scene 1: install
    install_out = [
        "",
        [D("  Installing "), C("ph-civic-data-mcp"), D(" via uvx...")],
        check("12 tools registered"),
        check("connected to Claude Code"),
        "",
    ]
    screen = gif.command_scene("uvx ph-civic-data-mcp", install_out)
    gif.pause(1800)

    # Scene 2: ask about PH earthquakes
    screen = gif.type_text(
        screen,
        [O("  you  "), F("")],
        "any earthquakes in the Philippines in the last 24 hours?",
        speed=28,
    )
    gif.pause(400)

    screen = gif.show_running(screen, label="  ⏎ get_latest_earthquakes", steps=5, delay=140)

    earthquakes = [
        "",
        [P("  claude  "), F("Yes — pulled from PHIVOLCS:")],
        "",
        [D("   •  "), Y("M2.3"), F(" — 15km N 88° W of Dinalungan (Aurora)")],
        [D("   •  "), Y("M1.8"), F(" — Sarangani Province")],
        [D("   •  "), Y("M1.5"), F(" — offshore Samar")],
        "",
        [D("  Source: PHIVOLCS • retrieved just now")],
    ]
    screen = gif.reveal_lines(screen, earthquakes, delay=80)
    gif.pause(2500)

    # Scene 3: multi-hazard profile
    gif = TerminalGIF(preset="full", title="claude — ph-civic-data-mcp")
    gif.pause(300)

    screen = gif.type_text(
        [],
        [O("  you  "), F("")],
        "give me a multi-hazard risk profile for Manila",
        speed=28,
    )
    gif.pause(400)

    screen = gif.show_running(screen, label="  ⏎ assess_area_risk", steps=6, delay=160)

    risk = [
        "",
        [P("  claude  "), F("Parallel call to PHIVOLCS + PAGASA + AQICN:")],
        "",
        [D("   location       "), F("Manila")],
        [D("   earthquake     "), G("Low  "), D("(0 quakes ≥M4 in last 30d)")],
        [D("   typhoon        "), G("No active signal")],
        [D("   air quality    "), Y("AQI 82  "), D("(Fair)")],
        "",
        [D("  For emergencies: ndrrmc.gov.ph / PHIVOLCS / PAGASA")],
    ]
    screen = gif.reveal_lines(screen, risk, delay=80)
    gif.pause(3000)

    gif.save("/Users/xavier/Desktop/ph-civic-data-mcp/docs/demo.gif")
    print("wrote docs/demo.gif")


if __name__ == "__main__":
    build()
