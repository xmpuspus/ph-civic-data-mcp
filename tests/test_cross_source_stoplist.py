"""Offline regression tests for the hazard-overlap stoplist (cross_source.py).

The gap this fix closes: a comment above _HAZARD_STOPWORDS claimed "surigao"
was in the set. It was not. The real fix keeps "surigao" out on purpose,
because it names a province, not a whole region, and a province-scale token
is specific enough to be real hazard_overlap signal. The comment and the
`flag_infra_anomalies` docstring now say so directly.

The real gap was elsewhere: only 6 of the 17 official PSGC region names had
a token in the stoplist (via luzon, manila, mindanao, visayas, metro, and
philippines). This file pins the 11 that were missing, closed on 2026-09-03
from a live pull of the PSGC region list, and pins the province-name
boundary that tests/test_v031_fixes.py and tests/test_v030_cross_source.py
already protect.
"""

from __future__ import annotations

from ph_civic_data_mcp.sources.cross_source import _HAZARD_STOPWORDS, _proper_noun_tokens

# The 17 official PSGC regions, read live on 2026-09-03 via
# list_admin_units(level="region"). A region rename or split needs a
# re-pull of this list and of _HAZARD_STOPWORDS together.
_PSGC_REGION_NAMES = [
    "Ilocos Region",
    "Cagayan Valley",
    "Central Luzon",
    "CALABARZON",
    "MIMAROPA Region",
    "Bicol Region",
    "Western Visayas",
    "Central Visayas",
    "Eastern Visayas",
    "Zamboanga Peninsula",
    "Northern Mindanao",
    "Davao Region",
    "SOCCSKSARGEN",
    "NCR",
    "CAR",
    "Caraga",
    "BARMM",
]

# A sample of real PSGC province names the stoplist must not contain.
# tests/test_v031_fixes.py already pins "samar" and "surigao" surviving the
# filter; tests/test_v030_cross_source.py already pins "batanes" firing a
# real match. This list checks a wider sample of the same boundary.
_PSGC_PROVINCE_NAMES = [
    "Surigao Del Norte",
    "Surigao Del Sur",
    "Samar",
    "Leyte",
    "Batanes",
    "Pampanga",
    "Bulacan",
    "Iloilo",
]


def test_stoplist_does_not_add_surigao() -> None:
    """The doc/code mismatch this fix closes.

    A comment used to claim "surigao" was stoplisted. It never was, and it
    must stay out, because it names a province, not chrome.
    """
    assert "surigao" not in _HAZARD_STOPWORDS


def test_stoplist_covers_every_significant_psgc_region_token() -> None:
    """The real gap: 11 of 17 official PSGC region names had no token here."""
    missing = [
        (name, token)
        for name in _PSGC_REGION_NAMES
        for token in _proper_noun_tokens(name)
        if token not in _HAZARD_STOPWORDS
    ]
    assert missing == []


def test_stoplist_does_not_swallow_real_province_names() -> None:
    """A province is specific enough that a shared token is real signal."""
    caught = [
        (name, token)
        for name in _PSGC_PROVINCE_NAMES
        for token in _proper_noun_tokens(name)
        if token in _HAZARD_STOPWORDS
    ]
    assert caught == []


def test_proper_noun_tokens_drops_region_name_but_keeps_province_name() -> None:
    """A quake location naming a region and a province keeps only the
    province as a hazard keyword."""
    tokens = _proper_noun_tokens("15 km NE of Surigao City (Caraga)")
    assert "surigao" in tokens
    assert "caraga" not in tokens
