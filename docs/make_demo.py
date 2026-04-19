"""Generate terminal GIFs for ph-civic-data-mcp README.

Produces:
  docs/demo.gif              — grand tour of all sources
  docs/demo_phivolcs.gif     — earthquakes + volcano
  docs/demo_pagasa.gif       — weather + typhoons
  docs/demo_philgeps.gif     — procurement search
  docs/demo_psa.gif          — population + poverty
  docs/demo_aqicn.gif        — air quality
  docs/demo_combined.gif     — cross-source risk + narrative chaining

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

OUT_DIR = "/Users/xavier/Desktop/ph-civic-data-mcp/docs"


def you(prompt: str):
    return [O("  you  "), F(prompt)]


def claude_says(text: str):
    return [P("  claude  "), F(text)]


# ────────────────────────────────────────────────────────────────────
# 1. Grand tour — showcases every source in one demo
# ────────────────────────────────────────────────────────────────────
def build_grand_tour() -> None:
    gif = TerminalGIF(preset="full", title="claude — ph-civic-data-mcp")
    gif.pause(400)

    screen = gif.command_scene(
        "uvx ph-civic-data-mcp",
        [
            "",
            [D("  Starting "), C("ph-civic-data-mcp"), D(" via stdio...")],
            check("12 tools across 5 PH government sources"),
            check("PHIVOLCS · PAGASA · PhilGEPS · PSA · AQICN"),
            check("connected to Claude"),
            "",
        ],
    )
    gif.pause(1600)

    # Turn 1 — Earthquakes
    gif = TerminalGIF(preset="full", title="claude — ph-civic-data-mcp")
    gif.pause(300)
    screen = gif.type_text([], you(""), "what earthquakes hit PH in the last 24h?", speed=24)
    gif.pause(300)
    screen = gif.show_running(screen, label="  ⏎ get_latest_earthquakes", steps=4, delay=140)
    screen = gif.reveal_lines(
        screen,
        [
            "",
            claude_says("3 quakes from PHIVOLCS (retrieved just now):"),
            "",
            [D("   "), Y("M2.3"), F(" · 15km N 88° W of Dinalungan (Aurora)")],
            [D("   "), Y("M1.8"), F(" · offshore Sarangani")],
            [D("   "), Y("M1.5"), F(" · offshore Samar")],
            "",
        ],
        delay=70,
    )
    gif.pause(1800)

    # Turn 2 — Weather
    gif = TerminalGIF(preset="full", title="claude — ph-civic-data-mcp")
    gif.pause(300)
    screen = gif.type_text([], you(""), "3-day forecast for Cebu City", speed=24)
    gif.pause(300)
    screen = gif.show_running(screen, label="  ⏎ get_weather_forecast", steps=4, delay=140)
    screen = gif.reveal_lines(
        screen,
        [
            "",
            claude_says("Cebu City via Open-Meteo:"),
            "",
            [D("   Apr 19 · "), Y("30.2°C"), D(" high  "), Y("2.4 mm"), D(" rain")],
            [D("   Apr 20 · "), Y("30.5°C"), D(" high  "), Y("1.5 mm"), D(" rain")],
            [D("   Apr 21 · "), Y("31.6°C"), D(" high  "), Y("0.0 mm"), D(" rain")],
            "",
        ],
        delay=70,
    )
    gif.pause(1800)

    # Turn 3 — Procurement
    gif = TerminalGIF(preset="full", title="claude — ph-civic-data-mcp")
    gif.pause(300)
    screen = gif.type_text([], you(""), "search PhilGEPS for flood control", speed=24)
    gif.pause(300)
    screen = gif.show_running(screen, label="  ⏎ search_procurement", steps=4, delay=140)
    screen = gif.reveal_lines(
        screen,
        [
            "",
            claude_says("Matches in the latest PhilGEPS notices:"),
            "",
            [D("   • "), F("DPWH - Bicol  "), D("· Flood mitigation structures")],
            [D("   • "), F("LGU Pampanga "), D("· River dredging works")],
            [D("   • "), F("DPWH - NCR   "), D("· Drainage improvement")],
            "",
        ],
        delay=70,
    )
    gif.pause(1800)

    # Turn 4 — Population + Poverty
    gif = TerminalGIF(preset="full", title="claude — ph-civic-data-mcp")
    gif.pause(300)
    screen = gif.type_text([], you(""), "NCR population and poverty incidence", speed=24)
    gif.pause(300)
    screen = gif.show_running(screen, label="  ⏎ get_population_stats + get_poverty_stats", steps=5, delay=140)
    screen = gif.reveal_lines(
        screen,
        [
            "",
            claude_says("PSA OpenSTAT:"),
            "",
            [D("   population   "), Y("13,484,462"), D(" · 2020 Census")],
            [D("   poverty rate "), Y("2.0%"), D(" · 2023 Full-Year")],
            "",
        ],
        delay=70,
    )
    gif.pause(1800)

    # Turn 5 — Air quality + Cross-source
    gif = TerminalGIF(preset="full", title="claude — ph-civic-data-mcp")
    gif.pause(300)
    screen = gif.type_text([], you(""), "give me a full risk profile for Manila", speed=24)
    gif.pause(300)
    screen = gif.show_running(screen, label="  ⏎ assess_area_risk (parallel)", steps=6, delay=160)
    screen = gif.reveal_lines(
        screen,
        [
            "",
            claude_says("3 parallel calls: PHIVOLCS + PAGASA + AQICN"),
            "",
            [D("   earthquake   "), G("Low  "), D("(0 quakes ≥M4 in 30d)")],
            [D("   typhoon      "), G("No active signal")],
            [D("   air quality  "), Y("AQI 82"), D(" · Fair · PM2.5 dominant")],
            "",
            [D("  For emergencies: ndrrmc.gov.ph / PHIVOLCS / PAGASA")],
        ],
        delay=80,
    )
    gif.pause(3000)
    gif.save(f"{OUT_DIR}/demo.gif")
    print("wrote docs/demo.gif")


# ────────────────────────────────────────────────────────────────────
# 2. PHIVOLCS — earthquakes + volcano
# ────────────────────────────────────────────────────────────────────
def build_phivolcs() -> None:
    gif = TerminalGIF(preset="full", title="claude — PHIVOLCS")
    gif.pause(300)

    screen = gif.type_text([], you(""), "show me the biggest PH earthquake this month", speed=26)
    gif.pause(300)
    screen = gif.show_running(screen, label="  ⏎ get_latest_earthquakes min_magnitude=4", steps=5, delay=140)
    screen = gif.reveal_lines(
        screen,
        [
            "",
            claude_says("PHIVOLCS (cached 5 min):"),
            "",
            [D("   "), Y("M5.4"), F(" · 082 km SW of Polillo (Quezon)")],
            [D("         "), D("2026-04-12 03:14 PST · depth 031 km")],
            [D("         "), C("intensity V reported in 3 towns")],
            "",
            [D("  bulletin → "), C("2026_0412_0314_B2F.html")],
        ],
        delay=80,
    )
    gif.pause(2500)

    # Follow-up: volcano status
    screen = gif.type_text(screen, you(""), "is Mayon still on alert level 3?", speed=26)
    gif.pause(300)
    screen = gif.show_running(screen, label="  ⏎ get_volcano_status Mayon", steps=4, delay=140)
    screen = gif.reveal_lines(
        screen,
        [
            "",
            claude_says("Yes — from WOVODAT (24h observation):"),
            "",
            [D("   volcano     "), F("Mayon")],
            [D("   alert level "), R("3"), D(" (Intensified Unrest / Magmatic Unrest)")],
            [D("   seismicity  "), Y("41"), D(" volcanic earthquakes · 440 rockfalls")],
            "",
        ],
        delay=80,
    )
    gif.pause(3000)
    gif.save(f"{OUT_DIR}/demo_phivolcs.gif")
    print("wrote docs/demo_phivolcs.gif")


# ────────────────────────────────────────────────────────────────────
# 3. PAGASA — weather + typhoons
# ────────────────────────────────────────────────────────────────────
def build_pagasa() -> None:
    gif = TerminalGIF(preset="full", title="claude — PAGASA")
    gif.pause(300)

    screen = gif.type_text([], you(""), "weekend forecast for Baguio", speed=26)
    gif.pause(300)
    screen = gif.show_running(screen, label="  ⏎ get_weather_forecast Baguio days=3", steps=4, delay=140)
    screen = gif.reveal_lines(
        screen,
        [
            "",
            claude_says("Open-Meteo (PAGASA fallback, no token):"),
            "",
            [D("   Sat · "), Y("20-24°C"), D("  "), C("Partly cloudy"), D(" · 5 mm")],
            [D("   Sun · "), Y("19-22°C"), D("  "), C("Light rain    "), D(" · 12 mm")],
            [D("   Mon · "), Y("18-21°C"), D("  "), C("Moderate rain "), D(" · 18 mm")],
            "",
        ],
        delay=80,
    )
    gif.pause(2400)

    screen = gif.type_text(screen, you(""), "any active typhoons I should worry about?", speed=26)
    gif.pause(300)
    screen = gif.show_running(screen, label="  ⏎ get_active_typhoons", steps=4, delay=140)
    screen = gif.reveal_lines(
        screen,
        [
            "",
            claude_says("Checked the PAGASA severe weather bulletin —"),
            [F("  "), G("No Active Tropical Cyclone"), F(" within the PAR right now.")],
            "",
        ],
        delay=80,
    )
    gif.pause(3000)
    gif.save(f"{OUT_DIR}/demo_pagasa.gif")
    print("wrote docs/demo_pagasa.gif")


# ────────────────────────────────────────────────────────────────────
# 4. PhilGEPS — procurement search
# ────────────────────────────────────────────────────────────────────
def build_philgeps() -> None:
    gif = TerminalGIF(preset="full", title="claude — PhilGEPS")
    gif.pause(300)

    screen = gif.type_text([], you(""), "what is DBM buying this week?", speed=26)
    gif.pause(300)
    screen = gif.show_running(screen, label="  ⏎ search_procurement agency=DBM", steps=5, delay=140)
    screen = gif.reveal_lines(
        screen,
        [
            "",
            claude_says("PhilGEPS open data (6 h cache):"),
            "",
            [D("   • "), F("Pest & termite control  "), D("· Small Value Procurement")],
            [D("   • "), F("Admin building repair    "), D("· Small Value Procurement")],
            [D("   • "), F("72 foam mattresses       "), D("· Small Value Procurement")],
            "",
            [D("   published 18-Apr-2026 · closing this week")],
            "",
        ],
        delay=80,
    )
    gif.pause(2500)

    screen = gif.type_text(screen, you(""), "summarize by mode of procurement", speed=26)
    gif.pause(300)
    screen = gif.show_running(screen, label="  ⏎ get_procurement_summary", steps=4, delay=140)
    screen = gif.reveal_lines(
        screen,
        [
            "",
            claude_says("100 latest notices:"),
            "",
            [D("   "), Y("72"), F(" · Small Value Procurement")],
            [D("   "), Y("18"), F(" · Public Bidding")],
            [D("   "), Y("7"),  F("  · Negotiated Procurement")],
            [D("   "), Y("3"),  F("  · Shopping")],
            "",
        ],
        delay=80,
    )
    gif.pause(3000)
    gif.save(f"{OUT_DIR}/demo_philgeps.gif")
    print("wrote docs/demo_philgeps.gif")


# ────────────────────────────────────────────────────────────────────
# 5. PSA — population + poverty
# ────────────────────────────────────────────────────────────────────
def build_psa() -> None:
    gif = TerminalGIF(preset="full", title="claude — PSA OpenSTAT")
    gif.pause(300)

    screen = gif.type_text([], you(""), "how many Filipinos live in Region VII?", speed=26)
    gif.pause(300)
    screen = gif.show_running(screen, label="  ⏎ get_population_stats Region VII", steps=4, delay=140)
    screen = gif.reveal_lines(
        screen,
        [
            "",
            claude_says("PSA PXWeb (discovered via browse API):"),
            "",
            [D("   region      "), F("Central Visayas (Region VII)")],
            [D("   population  "), Y("8,081,988"), D(" · 2020 Census")],
            [D("   note        "), D("Latest available census data")],
            "",
        ],
        delay=80,
    )
    gif.pause(2500)

    screen = gif.type_text(screen, you(""), "and what is the Bicol poverty rate?", speed=26)
    gif.pause(300)
    screen = gif.show_running(screen, label="  ⏎ get_poverty_stats Bicol", steps=4, delay=140)
    screen = gif.reveal_lines(
        screen,
        [
            "",
            claude_says("PSA Full-Year Poverty Statistics:"),
            "",
            [D("   region                "), F("Region V (Bicol)")],
            [D("   poverty incidence     "), Y("23.5%"), D(" of families")],
            [D("   subsistence incidence "), Y("6.7%"), D(" · 2023")],
            "",
        ],
        delay=80,
    )
    gif.pause(3000)
    gif.save(f"{OUT_DIR}/demo_psa.gif")
    print("wrote docs/demo_psa.gif")


# ────────────────────────────────────────────────────────────────────
# 6. AQICN — air quality
# ────────────────────────────────────────────────────────────────────
def build_aqicn() -> None:
    gif = TerminalGIF(preset="full", title="claude — AQICN")
    gif.pause(300)

    screen = gif.type_text([], you(""), "is it safe to run outside in Manila today?", speed=26)
    gif.pause(300)
    screen = gif.show_running(screen, label="  ⏎ get_air_quality Manila", steps=4, delay=140)
    screen = gif.reveal_lines(
        screen,
        [
            "",
            claude_says("Real-time AQICN (15 min cache):"),
            "",
            [D("   AQI             "), Y("82"), D(" · Fair")],
            [D("   dominant        "), C("PM2.5")],
            [D("   PM2.5 / PM10    "), Y("82.0"), D(" / "), Y("33.0"), D(" µg/m³")],
            [D("   O3 / NO2        "), Y("29.3"), D(" / "), Y("20.6"), D(" µg/m³")],
            "",
            [F("  "), G("Acceptable for most"), F(" — sensitive groups should ease prolonged outdoor effort.")],
            "",
        ],
        delay=80,
    )
    gif.pause(3500)
    gif.save(f"{OUT_DIR}/demo_aqicn.gif")
    print("wrote docs/demo_aqicn.gif")


# ────────────────────────────────────────────────────────────────────
# 7. Mix & match — chain sources into one narrative
# ────────────────────────────────────────────────────────────────────
def build_combined() -> None:
    gif = TerminalGIF(preset="full", title="claude — cross-source")
    gif.pause(300)

    screen = gif.type_text(
        [],
        you(""),
        "brief me on Leyte: population, recent quakes, weather, and air",
        speed=26,
    )
    gif.pause(400)
    screen = gif.show_running(
        screen, label="  ⏎ 4 tools in parallel", steps=7, delay=160
    )
    screen = gif.reveal_lines(
        screen,
        [
            "",
            claude_says("Pulled from PSA + PHIVOLCS + Open-Meteo + AQICN:"),
            "",
            [D("   population     "), Y("1,789,158"), D(" · 2020 Census")],
            [D("   recent quakes  "), Y("2"),  D(" events ≥M2 in last 30 d · max "), Y("M3.1")],
            [D("   weather (3 d)  "), Y("26-31°C"), D(" · 8-22 mm rain")],
            [D("   air quality    "), D("no AQICN station in Leyte")],
            [D("   typhoon        "), G("No active signal")],
            "",
            claude_says("Summary: quiet seismic week, mild rain, no active TC."),
            "",
        ],
        delay=90,
    )
    gif.pause(3500)
    gif.save(f"{OUT_DIR}/demo_combined.gif")
    print("wrote docs/demo_combined.gif")


if __name__ == "__main__":
    build_grand_tour()
    build_phivolcs()
    build_pagasa()
    build_philgeps()
    build_psa()
    build_aqicn()
    build_combined()
