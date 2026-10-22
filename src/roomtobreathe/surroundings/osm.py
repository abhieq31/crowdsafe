"""OpenStreetMap data for the area around a venue: streets, footpaths, transit, parking and services.

OpenStreetMap needs no account, key or billing, and covers the whole world, so any council can use it.
The query is built here, in one place, and used both by Python (``fetch_osm``) and by the browser, which
fetches it itself and hands the JSON to the engine.

What OSM usually lacks is just as important: in a test around Eden Gardens, Kolkata, none of 466 street
segments had a ``width`` or ``sidewalk`` tag. Widths are therefore estimated from the street type
(see ``network.ROAD_TYPES``) and every result says so; organisers can override them per street.
"""

from __future__ import annotations

import json
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import List, Optional

# Public Overpass servers, tried in order. The main one sometimes refuses or times out under load.
MIRRORS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
]
USER_AGENT = "roomtobreathe (crowd-safety planning; https://github.com/abhieq31/roomtobreathe)"

POI_FILTERS = [
    'nwr[amenity~"^(hospital|clinic|police|fire_station|parking|bus_station|taxi|ferry_terminal)$"]',
    'nwr[healthcare~"^(hospital|clinic)$"]',
    'nwr[railway~"^(station|halt|subway_entrance|tram_stop)$"]',
    'nwr[public_transport~"^(station|stop_position|platform)$"]',
    'node[highway="bus_stop"]',
]


def overpass_query(lat: float, lon: float, radius_m: float = 1500, poi_radius_m: Optional[float] = None) -> str:
    """Overpass QL: all streets within ``radius_m``; main roads, hospitals and other services within
    ``poi_radius_m`` (default 2x, at least 3 km), so ambulance routes reach hospitals further away."""
    r = int(radius_m)
    pr = int(poi_radius_m or max(3000, 2 * radius_m))  # hospitals can be further away than the streets we model
    pois = "".join(f"{f}(around:{pr},{lat:.6f},{lon:.6f});" for f in POI_FILTERS)
    return (
        f"[out:json][timeout:90];"
        f"(way(around:{r},{lat:.6f},{lon:.6f})[highway];"
        f'way(around:{pr},{lat:.6f},{lon:.6f})[highway~"^(motorway|trunk|primary|secondary)(_link)?$"];)->.streets;'
        f".streets out body;.streets >;out skel qt;"
        f"({pois});out tags center;"
    )


def cache_path(lat: float, lon: float, radius_m: float, cache_dir: Optional[str] = None) -> Path:
    base = Path(cache_dir) if cache_dir else Path.home() / ".cache" / "roomtobreathe" / "osm"
    return base / f"osm_{lat:.4f}_{lon:.4f}_{int(radius_m)}.json"


def load_or_fetch(
    lat: float,
    lon: float,
    radius_m: float = 1500,
    cache_dir: Optional[str] = None,
    max_age_days: float = 30,
    refresh: bool = False,
) -> dict:
    """Map data from the local cache if it is recent, otherwise from Overpass (and cache it).
    Streets change slowly; a month-old copy is fine for planning."""
    p = cache_path(lat, lon, radius_m, cache_dir)
    if not refresh and p.exists() and time.time() - p.stat().st_mtime < max_age_days * 86400:
        return json.loads(p.read_text(encoding="utf-8"))
    data = fetch_osm(lat, lon, radius_m)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data), encoding="utf-8")
    return data


def fetch_osm(
    lat: float, lon: float, radius_m: float = 1500, timeout: float = 120, mirrors: Optional[List[str]] = None
) -> dict:
    """Download the data for ``overpass_query`` from the first mirror that answers."""
    body = urllib.parse.urlencode({"data": overpass_query(lat, lon, radius_m)}).encode()
    errors = []
    for url in mirrors or MIRRORS:
        req = urllib.request.Request(url, data=body, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                data = json.loads(r.read().decode("utf-8"))
            if data.get("elements"):
                return data
            errors.append(f"{url}: empty answer")
        except Exception as e:  # noqa: BLE001 - try the next mirror whatever went wrong
            errors.append(f"{url}: {e}")
    raise RuntimeError("could not download OpenStreetMap data: " + "; ".join(errors))
