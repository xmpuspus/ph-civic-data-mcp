"""PHIVOLCS — earthquakes, bulletins, volcano alert levels.

Landmines (from validation log):
- 1: never construct bulletin URLs; always parse hrefs
- 2: PHIVOLCS has broken SSL → use PHIVOLCS_CLIENT with verify=False
- bulletin 404s exist (~2016 era); catch and skip gracefully
"""

from __future__ import annotations

import asyncio
import re
from datetime import datetime, timezone
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup
from dateutil import parser as date_parser

from ph_civic_data_mcp.models.earthquake import Earthquake
from ph_civic_data_mcp._mcp import mcp
from ph_civic_data_mcp.utils.cache import CACHES, cache_key
from ph_civic_data_mcp.utils.envelope import failure_envelope, failure_result
from ph_civic_data_mcp.utils.geo import haversine_km
from ph_civic_data_mcp.utils.http import PHIVOLCS_CLIENT, fetch_with_retry, log_stderr

PHIVOLCS_LICENSE = "Public — PHIVOLCS public bulletin pages"

# Every URL that reaches PHIVOLCS_CLIENT passes this allowlist first, whether
# an agent supplied it (get_earthquake_bulletin) or an upstream page did
# (the WOVODAT bulletin links). The client skips certificate checks, so a URL
# that escapes to another host would be fetched with no TLS check at all.
ALLOWED_BULLETIN_HOST_SUFFIX = ".phivolcs.dost.gov.ph"
MAX_REDIRECT_HOPS = 3


class PhivolcsHostError(ValueError):
    """A URL, or a redirect target, is not an https PHIVOLCS host."""


def _is_phivolcs_url(url: str) -> bool:
    """True only for an https URL on a PHIVOLCS host, with no userinfo or odd port.

    Parses the authority instead of matching a string, so
    `https://wovodat.phivolcs.dost.gov.ph@evil.example/` (userinfo),
    `//evil.example/` (scheme-relative), `http://` (downgrade) and
    `https://wovodat.phivolcs.dost.gov.ph.evil.example/` (suffix) all fail.
    """
    try:
        parsed = urlparse(str(url))
        port = parsed.port
    except ValueError:
        return False
    if parsed.scheme != "https":
        return False
    if parsed.username is not None or parsed.password is not None:
        return False
    host = (parsed.hostname or "").lower()
    if not (host.endswith(ALLOWED_BULLETIN_HOST_SUFFIX) or host == "phivolcs.dost.gov.ph"):
        return False
    return port in (None, 443)


async def _fetch_phivolcs(url: str) -> httpx.Response:
    """GET through the TLS-relaxed client, checking every hop against the allowlist.

    The client never follows a redirect on its own. Each Location is resolved
    against the URL that sent it and re-checked, so a redirect off
    *.phivolcs.dost.gov.ph, to http://, or to a private address raises
    PhivolcsHostError before any connection is made.
    """
    current = str(url)
    for _ in range(MAX_REDIRECT_HOPS + 1):
        if not _is_phivolcs_url(current):
            raise PhivolcsHostError(f"refusing to fetch non-PHIVOLCS URL {current!r}")
        response = await fetch_with_retry(PHIVOLCS_CLIENT, "GET", current, follow_redirects=False)
        if not response.has_redirect_location:
            return response
        current = urljoin(current, response.headers["location"])
    raise PhivolcsHostError(f"too many redirects fetching {url!r}")


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

    response = await _fetch_phivolcs(PHIVOLCS_EQ_LIST_URL)
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
        # Parse drift, not "no earthquakes" — raise so callers report the
        # failure instead of caching a false all-clear.
        raise RuntimeError("PHIVOLCS earthquake table not found on list page (HTML drift?)")

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


@mcp.tool(
    title="Latest PHIVOLCS earthquakes",
    tags={"earthquake", "hazard", "philippines", "phivolcs"},
    annotations={
        "title": "Latest PHIVOLCS earthquakes",
        "readOnlyHint": True,
        "idempotentHint": True,
        "openWorldHint": True,
        "destructiveHint": False,
    },
)
async def get_latest_earthquakes(
    min_magnitude: float = 1.0,
    limit: int = 20,
    region: str | None = None,
    center_lat: float | None = None,
    center_lon: float | None = None,
    radius_km: float | None = None,
) -> list[dict] | dict:
    """Get the latest earthquake events from PHIVOLCS.

    Args:
        min_magnitude: Minimum magnitude to include (default 1.0).
        limit: Max events to return (default 20, max 100).
        region: Filter by PH region/province/city name (partial match).
        center_lat: Latitude of a search point. Give with center_lon and
                    radius_km to filter to events near one place instead of
                    the full recent-events list.
        center_lon: Longitude of a search point. Give with center_lat and
                    radius_km.
        radius_km: Keep only events within this distance of
                   (center_lat, center_lon). Give all three of center_lat,
                   center_lon, and radius_km together, or none of them. Each
                   returned event then carries a distance_km field.

    Returns a list of events on success. If the PHIVOLCS upstream is
    unreachable, returns a dict {results: [], upstream_error: true, caveats}
    instead of an empty list, so "outage" is never mistaken for "no quakes".
    """
    have = (center_lat is not None, center_lon is not None, radius_km is not None)
    if any(have) and not all(have):
        return failure_result(
            "PHIVOLCS",
            PHIVOLCS_EQ_LIST_URL,
            "center_lat, center_lon, and radius_km must all be given together, or all left out.",
            license=PHIVOLCS_LICENSE,
            validation_error=True,
            results=[],
        )
    if radius_km is not None and radius_km <= 0:
        return failure_result(
            "PHIVOLCS",
            PHIVOLCS_EQ_LIST_URL,
            "radius_km must be a positive number.",
            license=PHIVOLCS_LICENSE,
            validation_error=True,
            results=[],
        )

    limit = max(1, min(int(limit), 100))
    try:
        rows = await _fetch_earthquake_list()
    except Exception as exc:
        log_stderr(f"get_latest_earthquakes error: {exc}")
        return failure_envelope(
            "PHIVOLCS",
            PHIVOLCS_EQ_LIST_URL,
            f"PHIVOLCS earthquake list unavailable ({type(exc).__name__}: {exc}). "
            "This is an upstream/parse failure, not an absence of earthquakes.",
            license=PHIVOLCS_LICENSE,
        )

    retrieved_at = _now()
    results: list[dict] = []
    region_lc = region.lower().strip() if region else None
    use_radius = radius_km is not None

    for row in rows:
        if row["magnitude"] < min_magnitude:
            continue
        if region_lc and region_lc not in row["location"].lower():
            continue
        distance_km = None
        if use_radius:
            distance_km = haversine_km(center_lat, center_lon, row["latitude"], row["longitude"])
            if distance_km > radius_km:
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
        data = quake.model_dump(mode="json")
        if use_radius:
            data["distance_km"] = round(distance_km, 2)
        results.append(data)
        if len(results) >= limit:
            break
    return results


@mcp.tool(
    title="PHIVOLCS earthquake bulletin",
    tags={"bulletin", "earthquake", "philippines", "phivolcs"},
    annotations={
        "title": "PHIVOLCS earthquake bulletin",
        "readOnlyHint": True,
        "idempotentHint": True,
        "openWorldHint": True,
        "destructiveHint": False,
    },
)
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
    if not _is_phivolcs_url(bulletin_url):
        return {
            "url": bulletin_url,
            "source": "PHIVOLCS",
            "caveats": [
                "bulletin_url must be a *.phivolcs.dost.gov.ph URL returned by "
                "get_latest_earthquakes; refusing to fetch other hosts."
            ],
            "data_retrieved_at": _now().isoformat(),
        }

    key = cache_key({"bulletin_url": bulletin_url})
    cache = CACHES["phivolcs_bulletins"]
    if key in cache:
        return cache[key]

    try:
        response = await _fetch_phivolcs(bulletin_url)
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
            "upstream_error": True,
            "caveats": [f"bulletin fetch failed ({type(exc).__name__}: {exc})"],
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
    # Fetch failures propagate to the caller; never cache an empty list born
    # from an outage for the full success TTL.
    response = await _fetch_phivolcs(WOVODAT_BULLETIN_LIST_URL)
    response.raise_for_status()

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
        if not _is_phivolcs_url(full_url):
            # An upstream page that links off-host never steers the
            # certificate-blind client anywhere. Skip the entry.
            log_stderr(f"WOVODAT list linked off-host, skipped: {full_url}")
            continue
        if volcano not in result:
            result[volcano] = {"bulletin_url": full_url, "bid": match.group(2), "title": text}

    cache[key] = result
    return result


async def _fetch_volcano_alert(bulletin_url: str) -> tuple[int | None, str | None, str | None]:
    """Fetch a single volcano bulletin and extract (alert_level, status_description, error).

    `error` is None on a completed fetch, even when the page carries no
    matchable alert text. It names the failure only when the fetch itself
    could not complete, so a caller can tell an outage from a bulletin that
    loaded but published no ALERT LEVEL line.
    """
    try:
        response = await _fetch_phivolcs(bulletin_url)
        response.raise_for_status()
    except Exception as exc:
        log_stderr(f"volcano bulletin fetch error: {exc}")
        return None, None, f"{type(exc).__name__}: {exc}"

    soup = BeautifulSoup(response.text, "lxml")
    text = soup.get_text(" ", strip=True)

    match = re.search(r"ALERT\s*LEVEL\s*(?:\([^)]+\))?\s*(\d)", text, re.IGNORECASE)
    alert_level = int(match.group(1)) if match else None

    status = None
    status_match = re.search(r"ALERT\s*LEVEL[^(]*\(([^)]+)\)", text, re.IGNORECASE)
    if status_match:
        status = status_match.group(1).strip()

    return alert_level, status, None


@mcp.tool(
    title="Philippine volcano alert levels",
    tags={"hazard", "philippines", "phivolcs", "volcano"},
    annotations={
        "title": "Philippine volcano alert levels",
        "readOnlyHint": True,
        "idempotentHint": True,
        "openWorldHint": True,
        "destructiveHint": False,
    },
)
async def get_volcano_status(volcano_name: str | None = None) -> list[dict] | dict:
    """Get current alert level for Philippine volcanoes.

    Args:
        volcano_name: e.g. "Mayon", "Taal", "Kanlaon", "Bulusan".
                      None returns all monitored volcanoes with recent bulletins.

    Returns a list on success. If the WOVODAT bulletin list is unreachable or
    parses to nothing, returns {results: [], upstream_error: true, caveats}.
    A list entry whose own bulletin fetch failed carries upstream_error: true
    and a caveat instead of a null alert_level with no explanation.
    """
    try:
        bulletins = await _fetch_volcano_bulletin_list()
    except Exception as exc:
        log_stderr(f"get_volcano_status list error: {exc}")
        return failure_envelope(
            "PHIVOLCS",
            WOVODAT_BULLETIN_LIST_URL,
            f"WOVODAT volcano bulletin list unavailable ({type(exc).__name__}: {exc}).",
            license=PHIVOLCS_LICENSE,
        )

    if not bulletins:
        return failure_envelope(
            "PHIVOLCS",
            WOVODAT_BULLETIN_LIST_URL,
            "WOVODAT bulletin list parsed to zero entries — likely HTML drift, "
            "not an absence of monitored volcanoes.",
            license=PHIVOLCS_LICENSE,
        )

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
                    "caveats": [
                        "No matching volcano bulletin in WOVODAT. Try 'Mayon', 'Taal', "
                        "'Kanlaon', 'Bulusan', or omit volcano_name to list all monitored."
                    ],
                    "source": "PHIVOLCS",
                    "source_url": "https://wovodat.phivolcs.dost.gov.ph/bulletin/list-of-bulletin",
                    "license": "Public — PHIVOLCS public bulletin pages",
                    "data_retrieved_at": _now().isoformat(),
                }
            ]
    else:
        target_volcanoes = list(bulletins.keys())

    alerts = await asyncio.gather(
        *(_fetch_volcano_alert(bulletins[name]["bulletin_url"]) for name in target_volcanoes)
    )
    results: list[dict] = []
    for name, (alert_level, status, error) in zip(target_volcanoes, alerts):
        info = bulletins[name]
        entry = {
            "name": name,
            "alert_level": alert_level,
            "status_description": status,
            "last_updated": None,
            "bulletin_url": info["bulletin_url"],
            "bulletin_title": info.get("title"),
            "source": "PHIVOLCS",
            "data_retrieved_at": _now().isoformat(),
        }
        if error is not None:
            # A fetch failure on one volcano's bulletin must not read as a
            # confirmed normal reading. Reported twice on the v0.6.1 diff.
            entry["upstream_error"] = True
            entry["caveats"] = [
                f"This volcano's bulletin fetch failed ({error}). alert_level and "
                "status_description are unknown, not confirmed normal."
            ]
        results.append(entry)
    return results
