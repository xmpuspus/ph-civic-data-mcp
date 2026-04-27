from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

PSGCLevel = Literal[
    "region", "province", "city", "municipality", "district", "barangay", "sub-municipality"
]


class PSGCRecord(BaseModel):
    psgc_code: str
    name: str
    level: PSGCLevel
    parent_code: str | None = None
    region_code: str | None = None
    region_name: str | None = None
    island_group: str | None = None
    lat: float | None = None
    lng: float | None = None
    source: Literal["PSGC"] = "PSGC"
    source_url: str
    license: str = "Public domain (PSA Philippine Standard Geographic Code)"


class PSGCHierarchyLevel(BaseModel):
    psgc_code: str
    name: str
    level: PSGCLevel
    source_url: str


class PSGCHierarchy(BaseModel):
    psgc_code: str
    chain: list[PSGCHierarchyLevel]
    source: Literal["PSGC"] = "PSGC"
    license: str = "Public domain (PSA Philippine Standard Geographic Code)"
