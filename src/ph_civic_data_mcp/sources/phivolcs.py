"""PHIVOLCS — earthquakes, bulletins, volcano alert levels.

Landmines (from validation log):
- 1: never construct bulletin URLs; always parse hrefs
- 2: PHIVOLCS has broken SSL → use PHIVOLCS_CLIENT with verify=False
- bulletin 404s exist (~2016 era); catch and skip gracefully
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from urllib.parse import urljoin

from bs4 import BeautifulSoup
from dateutil import parser as date_parser

from ph_civic_data_mcp.models.earthquake import Earthquake
from ph_civic_data_mcp.server import mcp
from ph_civic_data_mcp.utils.cache import CACHES, cache_key
from ph_civic_data_mcp.utils.http import PHIVOLCS_CLIENT, fetch_with_retry, log_stderr

PHIVOLCS_EQ_LIST_URL = "https://earthquake.phivolcs.dost.gov.ph/"
WOVODAT_BULLETIN_LIST_URL = "https://wovodat.phivolcs.dost.gov.ph/bulletin/list-of-bulletin"
WOVODAT_BASE = "https://wovodat.phivolcs.dost.gov.ph"

VOLCANO_NAMES = {
    "mayon": "Mayon",
    "taal": "Taal",
    "kanlaon": "Kanlaon",
    "bulusan": "Bulusan",
    "pinatubo": "Pinatubo",
    "hibok-hibok": "Hibok-Hibok",
    "parker": "Parker",
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def _fetch_earthquake_list() -> list[dict]:
    """Scrape the PHIVOLCS earthquake list table. Returns raw rows."""
    key = cache_key({"endpoint": "eq_list"})
    cache = CACHES["phivolcs_earthquakes"]
    if key in cache:
        return cache[key]

    response = await fetch_with_retry(PHIVOLCS_CLIENT, "GET", PHIVOLCS_EQ_LIST_URL)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "lxml")

    target_table = None
    for table in soup.find_all("table"):
        rows = table.find_all("tr")
        if len(rows) < 5:
            continue
        header_cells = [c.get_text(" ", strip=True).lower() for c in rows[0].find_all(["th", "td"])]
        if any("latitude" in h for h in header_cells) and any("mag" in h for h in header_cells):
            target_table = table
            break

    if target_table is None:
        log_stderr("PHIVOLCS earthquake table not found on list page")
        cache[key] = []
        return []

    results: list[dict] = []
    rows = target_table.find_all("tr")
    for row in rows[1:]:
        cells = row.find_all(["td", "th"])
        if len(cells) < 6:
            continue
        datetime_text = cells[0].get_text(" ", strip=True)
        if not datetime_text or "date" in datetime_text.lower():
            continue

        bulletin_href = None
        link = row.find("a", href=True)
        if link:
            href = link["href"].replace("\\", "/").strip()
            bulletin_href = urljoin(PHIVOLCS_EQ_LIST_URL, href)

        try:
            lat = float(cells[1].get_text(strip=True))
            lng = float(cells[2].get_text(strip=True))
            depth = float(cells[3].get_text(strip=True))
            mag = float(cells[4].get_text(strip=True))
        except (ValueError, IndexError):
            continue

        location = cells[5].get_text(" ", strip=True)
        try:
            dt = date_parser.parse(datetime_text, fuzzy=True)
        except (ValueError, OverflowError):
            continue

        results.append(
            {
                "datetime_pst": dt,
                "latitude": lat,
                "longitude": lng,
                "depth_km": depth,
                "magnitude": mag,
                "location": location,
                "bulletin_url": bulletin_href,
            }
        )

    cache[key] = results
    return results


@mcp.tool()
async def get_latest_earthquakes(
    min_magnitude: float = 1.0,
    limit: int = 20,
    region: str | None = None,
) -> list[dict]:
    """Get the latest earthquake events from PHIVOLCS.

    Args:
        min_magnitude: Minimum magnitude to include (default 1.0).
        limit: Max events to return (default 20, max 100).
        region: Filter by PH region/province/city name (partial match).
    """
    limit = max(1, min(int(limit), 100))
    try:
        rows = await _fetch_earthquake_list()
    except Exception as exc:
        log_stderr(f"get_latest_earthquakes error: {exc}")
        return []

    retrieved_at = _now()
    results: list[dict] = []
    region_lc = region.lower().strip() if region else None

    for row in rows:
        if row["magnitude"] < min_magnitude:
            continue
        if region_lc and region_lc not in row["location"].lower():
            continue
        quake = Earthquake(
            datetime_pst=row["datetime_pst"],
            latitude=row["latitude"],
            longitude=row["longitude"],
            depth_km=row["depth_km"],
            magnitude=row["magnitude"],
            location=row["location"],
            bulletin_url=row["bulletin_url"],
            data_retrieved_at=retrieved_at,
        )
        results.append(quake.model_dump(mode="json"))
        if len(results) >= limit:
            break
    return results


@mcp.tool()
async def get_earthquake_bulletin(bulletin_url: str) -> dict:
    """Get the full bulletin for a PHIVOLCS earthquake event.

    Args:
        bulletin_url: Full URL returned by get_latest_earthquakes.bulletin_url.
    """
    if not bulletin_url or not bulletin_url.startswith("http"):
        return {
            "url": bulletin_url,
            "source": "PHIVOLCS",
            "caveats": ["bulletin_url is empty or malformed"],
            "data_retrieved_at": _now().isoformat(),
        }

    key = cache_key({"bulletin_url": bulletin_url})
    cache = CACHES["phivolcs_bulletins"]
    if key in cache:
        return cache[key]

    try:
        response = await fetch_with_retry(PHIVOLCS_CLIENT, "GET", bulletin_url)
        if response.status_code == 404:
            result = {
                "url": bulletin_url,
                "source": "PHIVOLCS",
                "caveats": ["bulletin page returned 404 — older bulletins may be archived"],
                "data_retrieved_at": _now().isoformat(),
            }
            cache[key] = result
            return result
        response.raise_for_status()
    except Exception as exc:
        log_stderr(f"get_earthquake_bulletin fetch error: {exc}")
        return {
            "url": bulletin_url,
            "source": "PHIVOLCS",
            "caveats": [f"fetch failed: {type(exc).__name__}"],
            "data_retrieved_at": _now().isoformat(),
        }

    soup = BeautifulSoup(response.text, "lxml")
    text = soup.get_text("\n", strip=True)

    magnitude: float | None = None
    depth_km: float | None = None
    location: str | None = None
    datetime_pst: str | None = None

    match = re.search(r"Magnitude\s*:?\s*(?:M[sbLwcW]?\s*)?([-\d.]+)", text, re.IGNORECASE)
    if match:
        try:
            magnitude = float(match.group(1))
        except ValueError:
            pass

    match = re.search(r"Depth[^:\n]*:?\s*0*([0-9]+(?:\.[0-9]+)?)", text, re.IGNORECASE)
    if match:
        try:
            depth_km = float(match.group(1))
        except ValueError:
            pass

    match = re.search(r"Location[^:\n]*:?\s*([^\n]+)", text, re.IGNORECASE)
    if match:
        location = match.group(1).strip()

    match = re.search(
        r"Date\s*/\s*Time[^:\n]*:?\s*([0-9]{1,2}\s+\w+\s+\d{4}\s*-\s*[0-9:]+(?:\s*[AP]M)?)",
        text,
        re.IGNORECASE,
    )
    if match:
        datetime_pst = match.group(1).strip()

    intensity_reports: list[dict] = []
    for tbl in soup.find_all("table"):
        header_text = tbl.get_text(" ", strip=True).lower()
        if "intensity" in header_text and "municipality" not in header_text[:50]:
            for row in tbl.find_all("tr"):
                cells = [c.get_text(" ", strip=True) for c in row.find_all(["td", "th"])]
                if len(cells) == 2 and cells[0] and cells[1]:
                    if "intensity" in cells[0].lower() or "municipality" in cells[0].lower():
                        continue
                    intensity_reports.append({"municipality": cells[0], "intensity": cells[1]})

    result = {
        "url": bulletin_url,
        "magnitude": magnitude,
        "depth_km": depth_km,
        "location": location,
        "datetime_pst": datetime_pst,
        "intensity_reports": intensity_reports,
        "full_text": text[:5000],
        "source": "PHIVOLCS",
        "data_retrieved_at": _now().isoformat(),
    }
    cache[key] = result
    return result


async def _fetch_volcano_bulletin_list() -> dict[str, dict]:
    """Parse WOVODAT bulletin list. Returns {volcano_name: {bulletin_url, bid, date}}."""
    key = cache_key({"endpoint": "volcano_list"})
    cache = CACHES["phivolcs_volcanoes"]
    if key in cache:
        return cache[key]

    result: dict[str, dict] = {}
    try:
        response = await fetch_with_retry(PHIVOLCS_CLIENT, "GET", WOVODAT_BULLETIN_LIST_URL)
        response.raise_for_status()
    except Exception as exc:
        log_stderr(f"volcano list fetch error: {exc}")
        cache[key] = result
        return result

    soup = BeautifulSoup(response.text, "lxml")
    vo_map = {
        "mvo": "Mayon",
        "bvo": "Bulusan",
        "kvo": "Kanlaon",
        "tvo": "Taal",
        "pvo": "Pinatubo",
    }

    for a in soup.find_all("a", href=True):
        href = a["href"]
        match = re.search(r"/bulletin/activity-(\w+)\?bid=(\d+)(?:&lang=en)?", href)
        if not match:
            continue
        code = match.group(1)
        if code not in vo_map:
            continue
        text = a.get_text(" ", strip=True)
        is_english = "lang=en" in href or "Summary" in text
        if not is_english:
            continue
        volcano = vo_map[code]
        full_url = urljoin(WOVODAT_BASE, href)
        if volcano not in result:
            result[volcano] = {"bulletin_url": full_url, "bid": match.group(2), "title": text}

    cache[key] = result
    return result


async def _fetch_volcano_alert(bulletin_url: str) -> tuple[int | None, str | None]:
    """Fetch a single volcano bulletin and extract (alert_level, status_description)."""
    try:
        response = await fetch_with_retry(PHIVOLCS_CLIENT, "GET", bulletin_url)
        response.raise_for_status()
    except Exception as exc:
        log_stderr(f"volcano bulletin fetch error: {exc}")
        return None, None

    soup = BeautifulSoup(response.text, "lxml")
    text = soup.get_text(" ", strip=True)

    match = re.search(r"ALERT\s*LEVEL\s*(?:\([^)]+\))?\s*(\d)", text, re.IGNORECASE)
    alert_level = int(match.group(1)) if match else None

    status = None
    status_match = re.search(
        r"ALERT\s*LEVEL[^(]*\(([^)]+)\)", text, re.IGNORECASE
    )
    if status_match:
        status = status_match.group(1).strip()

    return alert_level, status


@mcp.tool()
async def get_volcano_status(volcano_name: str | None = None) -> list[dict]:
    """Get current alert level for Philippine volcanoes.

    Args:
        volcano_name: e.g. "Mayon", "Taal", "Kanlaon", "Bulusan".
                      None returns all monitored volcanoes with recent bulletins.
    """
    try:
        bulletins = await _fetch_volcano_bulletin_list()
    except Exception as exc:
        log_stderr(f"get_volcano_status list error: {exc}")
        return []

    if not bulletins:
        return []

    target_volcanoes: list[str] = []
    if volcano_name:
        key = volcano_name.strip().lower()
        canonical = VOLCANO_NAMES.get(key, volcano_name.strip().title())
        if canonical in bulletins:
            target_volcanoes = [canonical]
        else:
            return [
                {
                    "name": canonical,
                    "alert_level": None,
                    "status_description": "No recent WOVODAT bulletin found for this volcano",
                    "last_updated": None,
                    "bulletin_url": None,
                    "source": "PHIVOLCS",
                    "data_retrieved_at": _now().isoformat(),
                }
            ]
    else:
        target_volcanoes = list(bulletins.keys())

    results: list[dict] = []
    for name in target_volcanoes:
        info = bulletins[name]
        alert_level, status = await _fetch_volcano_alert(info["bulletin_url"])
        results.append(
            {
                "name": name,
                "alert_level": alert_level,
                "status_description": status,
                "last_updated": None,
                "bulletin_url": info["bulletin_url"],
                "bulletin_title": info.get("title"),
                "source": "PHIVOLCS",
                "data_retrieved_at": _now().isoformat(),
            }
        )
    return results
