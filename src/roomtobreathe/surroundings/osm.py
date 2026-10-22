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
    "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
]
USER_AGENT = "roomtobreathe (crowd-safety planning; https://github.com/abhieq31/roomtobreathe)"

POI_FILTERS = [
    'nwr[amenity~"^(hospital|clinic|police|fire_station|parking|bus_station|taxi|ferry_terminal)$"]',
    'nwr[healthcare~"^(hospital|clinic)$"]',
    'nwr[railway~"^(station|halt|subway_entrance|tram_stop)$"]',
    'nwr[public_transport~"^(station|stop_position|platform)$"]',
    'node[highway="bus_stop"]',
]


def overpass_queries(lat: float, lon: float, radius_m: float = 1500, poi_radius_m: Optional[float] = None) -> List[str]:
    """The map data in three smaller Overpass queries, which can be fetched in parallel and fail
    independently: (1) all streets within ``radius_m``; (2) main roads within ``poi_radius_m`` (default 2x,
    at least 3 km), so ambulance routes and diversions reach further; (3) stations, stops, car parks,
    hospitals, police and fire stations within ``poi_radius_m``."""
    r = int(radius_m)
    pr = int(poi_radius_m or max(3000, 2 * radius_m))
    at = f"{lat:.6f},{lon:.6f}"
    head = "[out:json][timeout:120];"
    pois = "".join(f"{f}(around:{pr},{at});" for f in POI_FILTERS)
    return [
        f"{head}way(around:{r},{at})[highway];out body;>;out skel qt;",
        f'{head}way(around:{pr},{at})[highway~"^(motorway|trunk|primary|secondary)(_link)?$"];out body;>;out skel qt;',
        f"{head}({pois});out tags center;",
    ]


def overpass_query(lat: float, lon: float, radius_m: float = 1500, poi_radius_m: Optional[float] = None) -> str:
    """All of ``overpass_queries`` as one query (for tools that want a single request)."""
    r = int(radius_m)
    pr = int(poi_radius_m or max(3000, 2 * radius_m))
    at = f"{lat:.6f},{lon:.6f}"
    pois = "".join(f"{f}(around:{pr},{at});" for f in POI_FILTERS)
    return (
        f"[out:json][timeout:120];"
        f"(way(around:{r},{at})[highway];"
        f'way(around:{pr},{at})[highway~"^(motorway|trunk|primary|secondary)(_link)?$"];)->.streets;'
        f".streets out body;.streets >;out skel qt;"
        f"({pois});out tags center;"
    )


def merge_osm(parts: List[dict]) -> dict:
    """Combine Overpass answers, keeping each element once (a way's nodes come with both street queries)."""
    seen = {}
    for part in parts:
        for e in part.get("elements", []):
            key = (e["type"], e["id"])
            if key not in seen or len(e) > len(seen[key]):  # keep the fuller copy (with tags)
                seen[key] = e
    return {"elements": list(seen.values())}


def parse_osm(text: str) -> dict:
    """Map data from text: Overpass JSON, or OSM XML (the file openstreetmap.org's *Export* button gives,
    or JOSM saves). Lets a control room plan offline, or when the public servers are down."""
    t = text.lstrip()
    if t.startswith("{"):
        data = json.loads(t)
        if "elements" not in data:
            raise ValueError("this JSON is not OpenStreetMap data (no 'elements')")
        return data
    if not t.startswith("<"):
        raise ValueError("not OpenStreetMap data: expected Overpass JSON or an .osm XML file")
    import xml.etree.ElementTree as ET

    root = ET.fromstring(t)
    els = []
    for el in root:
        if el.tag not in ("node", "way", "relation"):
            continue
        tags = {tg.get("k"): tg.get("v") for tg in el.findall("tag")}
        e = {"type": el.tag, "id": int(el.get("id"))}
        if el.tag == "node":
            e["lat"], e["lon"] = float(el.get("lat")), float(el.get("lon"))
        elif el.tag == "way":
            e["nodes"] = [int(nd.get("ref")) for nd in el.findall("nd")]
        if tags:
            e["tags"] = tags
        els.append(e)
    if not els:
        raise ValueError("the .osm file contains no map data")
    return {"elements": els}


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


PART_NAMES = ["streets", "main roads further out", "stations, stops, car parks, hospitals, police and fire stations"]
HEDGE_S = 15.0  # if a server has not answered after this long, also ask the next one


def _post(url: str, query: str, timeout: float) -> dict:
    body = urllib.parse.urlencode({"data": query}).encode()
    req = urllib.request.Request(url, data=body, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = json.loads(r.read().decode("utf-8"))
    if "elements" not in data:
        raise ValueError("not map data")
    return data


def fetch_part(query: str, timeout: float = 150, mirrors: Optional[List[str]] = None, hedge_s: float = HEDGE_S) -> dict:
    """One query, hedged across the mirrors: ask the first; if it has not answered in ``hedge_s`` seconds
    (or fails), ask the next as well; the first good answer wins. Public servers are often slow or down."""
    import concurrent.futures as cf

    mirrors = mirrors or MIRRORS
    errors: List[str] = []
    pool = cf.ThreadPoolExecutor(max_workers=len(mirrors))
    try:
        futures = {}
        pending_mirrors = list(mirrors)
        while pending_mirrors or futures:
            if pending_mirrors and (not futures or len(futures) < len(mirrors)):
                url = pending_mirrors.pop(0)
                futures[pool.submit(_post, url, query, timeout)] = url
            done, _ = cf.wait(
                list(futures), timeout=hedge_s if pending_mirrors else None, return_when=cf.FIRST_COMPLETED
            )
            for f in done:
                url = futures.pop(f)
                try:
                    return f.result()
                except Exception as e:  # noqa: BLE001 - try the others whatever went wrong
                    errors.append(f"{url}: {e}")
    finally:
        pool.shutdown(wait=False, cancel_futures=True)
    raise RuntimeError("could not download OpenStreetMap data: " + "; ".join(errors))


def fetch_osm(
    lat: float, lon: float, radius_m: float = 1500, timeout: float = 150, mirrors: Optional[List[str]] = None
) -> dict:
    """Download the map data: the three parts in parallel, each hedged across the mirrors.

    The streets are essential; if only the main roads or the places (stations, hospitals...) fail, the
    rest is returned with ``missing`` naming what is absent, so a plan can still be made and say so."""
    import concurrent.futures as cf

    queries = overpass_queries(lat, lon, radius_m)
    names = PART_NAMES
    with cf.ThreadPoolExecutor(max_workers=len(queries)) as pool:
        futs = [pool.submit(fetch_part, q, timeout, mirrors) for q in queries]
        results, missing, errors = [], [], []
        for name, f in zip(names, futs):
            try:
                results.append(f.result())
            except RuntimeError as e:
                missing.append(name)
                errors.append(str(e))
    if names[0] in missing:
        raise RuntimeError(errors[0])
    data = merge_osm(results)
    if missing:
        data["missing"] = missing
    return data
