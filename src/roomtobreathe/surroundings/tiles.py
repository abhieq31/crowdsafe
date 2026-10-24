"""Fast map data from OpenStreetMap vector tiles (OpenFreeMap), converted to the Overpass format.

The public Overpass servers are often slow (minutes) or refuse requests. OpenFreeMap serves the same
OpenStreetMap data as vector tiles from a CDN: free, no key, no rate limit, and a few hundred kilobytes
for a whole venue's surroundings. A 3 x 3 km area at zoom 14 is about 4-9 tiles, fetched in parallel in
about a second.

Tiles are made for drawing maps, not routing, so this module rebuilds what routing needs:

- **Streets** come from the ``transportation`` layer (class, subclass, one-way). Lines are split at tile
  edges and at junctions are not guaranteed to share points, so endpoints and crossings are **snapped**
  within ``SNAP_M`` to make a connected network.
- **Street names** are in a separate ``transportation_name`` layer; each street piece takes the name of the
  named line running along it (within ``NAME_M``).
- **Places** (hospitals, police, fire stations, stations, stops, car parks) come from the ``poi`` layer.

What tiles lack compared with Overpass: tags like ``width``, ``sidewalk`` and ``emergency`` (rare in OSM
anyway), and minor paths can be simplified. Results say which source was used.
Standard library only (protobuf decoding included), so it runs in the browser too.
"""

from __future__ import annotations

import gzip
import json
import math
import urllib.request
from typing import Dict, Iterable, List, Optional, Tuple

TILEJSON = "https://tiles.openfreemap.org/planet"
ZOOM = 14
SNAP_M = 3.0
NAME_M = 6.0
USER_AGENT = "roomtobreathe (crowd-safety planning; https://github.com/abhipatelz/roomtobreathe)"

# OpenMapTiles transportation class/subclass -> OSM highway tag.
CLASS_TO_HIGHWAY = {
    "motorway": "motorway", "trunk": "trunk", "primary": "primary", "secondary": "secondary",
    "tertiary": "tertiary", "minor": "residential", "service": "service", "track": "track",
    "raceway": None, "busway": None, "bus_guideway": None, "ferry": None, "rail": None, "transit": None,
}
PATH_SUBCLASS = {"footway": "footway", "pedestrian": "pedestrian", "path": "path", "steps": "steps",
                 "cycleway": "cycleway", "bridleway": "path", "corridor": "corridor", "platform": None}

# OpenMapTiles poi class/subclass -> OSM tags our network understands.
def _poi_tags(cls: str, sub: str, name: str) -> Optional[dict]:
    t = {"name": name} if name else {}
    if cls == "hospital" or sub == "hospital":
        return {**t, "amenity": "hospital"}
    if sub == "clinic":
        return {**t, "amenity": "clinic"}
    if cls == "police" or sub == "police":
        return {**t, "amenity": "police"}
    if sub == "fire_station" or cls == "fire_station":
        return {**t, "amenity": "fire_station"}
    if sub == "parking" or cls == "parking":
        return {**t, "amenity": "parking"}
    if sub == "bus_station":
        return {**t, "amenity": "bus_station"}
    if sub == "bus_stop":
        return {**t, "highway": "bus_stop"}
    if sub == "subway" or sub == "subway_entrance":
        return {**t, "railway": "station", "station": "subway"}
    if sub == "tram_stop":
        return {**t, "railway": "tram_stop"}
    if cls == "railway" or sub in ("station", "halt"):
        return {**t, "railway": "station"}
    if cls == "ferry_terminal" or sub == "ferry_terminal":
        return {**t, "amenity": "ferry_terminal"}
    if sub == "taxi":
        return {**t, "amenity": "taxi"}
    return None


# ------------------------------------------------------------------ tile maths
def lonlat_to_tile(lon: float, lat: float, z: int = ZOOM) -> Tuple[float, float]:
    n = 2 ** z
    x = (lon + 180.0) / 360.0 * n
    y = (1.0 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2.0 * n
    return x, y


def tile_to_lonlat(x: float, y: float, z: int = ZOOM) -> Tuple[float, float]:
    n = 2 ** z
    lon = x / n * 360.0 - 180.0
    lat = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * y / n))))
    return lon, lat


def tiles_around(lat: float, lon: float, radius_m: float, z: int = ZOOM) -> List[Tuple[int, int, int]]:
    dlat = radius_m / 111_320.0
    dlon = radius_m / (111_320.0 * math.cos(math.radians(lat)))
    x0, y0 = lonlat_to_tile(lon - dlon, lat + dlat, z)
    x1, y1 = lonlat_to_tile(lon + dlon, lat - dlat, z)
    return [(z, x, y) for x in range(int(x0), int(x1) + 1) for y in range(int(y0), int(y1) + 1)]


# ------------------------------------------------------------------ protobuf / MVT decoding
def _varint(b: bytes, i: int) -> Tuple[int, int]:
    shift = result = 0
    while True:
        c = b[i]
        i += 1
        result |= (c & 0x7F) << shift
        if not c & 0x80:
            return result, i
        shift += 7


def _fields(b: bytes):
    """Yield (field number, wire type, value) of a protobuf message; length-delimited values as bytes."""
    i, n = 0, len(b)
    while i < n:
        key, i = _varint(b, i)
        f, w = key >> 3, key & 7
        if w == 0:
            v, i = _varint(b, i)
        elif w == 1:
            v, i = b[i:i + 8], i + 8
        elif w == 2:
            ln, i = _varint(b, i)
            v, i = b[i:i + ln], i + ln
        elif w == 5:
            v, i = b[i:i + 4], i + 4
        else:
            raise ValueError(f"unsupported protobuf wire type {w}")
        yield f, w, v


def _packed(b: bytes) -> List[int]:
    out, i = [], 0
    while i < len(b):
        v, i = _varint(b, i)
        out.append(v)
    return out


def _value(b: bytes):
    import struct

    for f, _, v in _fields(b):
        if f == 1:
            return v.decode("utf-8", "replace")
        if f == 2:
            return struct.unpack("<f", v)[0]
        if f == 3:
            return struct.unpack("<d", v)[0]
        if f in (4, 5):
            return v
        if f == 6:
            return (v >> 1) ^ -(v & 1)
        if f == 7:
            return bool(v)
    return None


def _zz(n: int) -> int:
    return (n >> 1) ^ -(n & 1)


def _geometry(cmds: List[int]) -> List[List[Tuple[int, int]]]:
    """Decode MVT geometry commands into parts (lists of tile-pixel points)."""
    parts: List[List[Tuple[int, int]]] = []
    x = y = 0
    i = 0
    cur: List[Tuple[int, int]] = []
    while i < len(cmds):
        cmd, count = cmds[i] & 7, cmds[i] >> 3
        i += 1
        if cmd == 1:  # MoveTo
            for _ in range(count):
                x += _zz(cmds[i])
                y += _zz(cmds[i + 1])
                i += 2
                if cur:
                    parts.append(cur)
                cur = [(x, y)]
        elif cmd == 2:  # LineTo
            for _ in range(count):
                x += _zz(cmds[i])
                y += _zz(cmds[i + 1])
                i += 2
                cur.append((x, y))
        elif cmd == 7:  # ClosePath
            if cur:
                cur.append(cur[0])
    if cur:
        parts.append(cur)
    return parts


def decode_tile(
    data: bytes, layers: Iterable[str] = ("transportation", "transportation_name", "poi")
) -> Dict[str, list]:
    """{layer: [(properties, geometry type, parts in tile pixels, extent)]} for the wanted layers."""
    if data[:2] == b"\x1f\x8b":
        data = gzip.decompress(data)
    wanted = set(layers)
    out: Dict[str, list] = {}
    for f, _, layer in _fields(data):
        if f != 3:
            continue
        name, keys, values, feats, extent = "", [], [], [], 4096
        for lf, _, lv in _fields(layer):
            if lf == 1:
                name = lv.decode()
            elif lf == 2:
                feats.append(lv)
            elif lf == 3:
                keys.append(lv.decode())
            elif lf == 4:
                values.append(_value(lv))
            elif lf == 5:
                extent = lv
        if name not in wanted:
            continue
        rows = []
        for fb in feats:
            tags, gtype, geom = [], 0, []
            for ff, _, fv in _fields(fb):
                if ff == 2:
                    tags = _packed(fv)
                elif ff == 3:
                    gtype = fv
                elif ff == 4:
                    geom = _packed(fv)
            props = {keys[tags[k]]: values[tags[k + 1]] for k in range(0, len(tags) - 1, 2)}
            rows.append((props, gtype, _geometry(geom), extent))
        out[name] = rows
    return out


# ------------------------------------------------------------------ conversion to Overpass elements
def tiles_to_osm(tiles: List[Tuple[Tuple[int, int, int], bytes]], center: Tuple[float, float],
                 radius_m: float) -> dict:
    """Convert decoded tiles to Overpass-style JSON (nodes and ways with OSM tags)."""
    lat0, lon0 = center
    kx = 111_320.0 * math.cos(math.radians(lat0))
    ky = 111_320.0

    def to_m(lon, lat):
        return ((lon - lon0) * kx, (lat - lat0) * ky)

    lines: List[Tuple[dict, List[Tuple[float, float]]]] = []  # (tags, points as lon/lat)
    named: List[Tuple[str, str, List[Tuple[float, float]]]] = []  # (name, class, points in metres)
    pois: List[Tuple[dict, float, float]] = []
    for (z, tx, ty), data in tiles:
        layers = decode_tile(data)
        for layer, rows in layers.items():
            for props, gtype, parts, extent in rows:
                for part in parts:
                    ll = [tile_to_lonlat(tx + px / extent, ty + py / extent, z) for px, py in part]
                    if layer == "transportation" and gtype == 2:
                        tags = _road_tags(props)
                        if tags:
                            lines.append((tags, ll))
                    elif layer == "transportation_name" and gtype == 2 and props.get("name"):
                        named.append((str(props["name"]), str(props.get("class", "")), [to_m(*p) for p in ll]))
                    elif layer == "poi" and gtype == 1:
                        tags = _poi_tags(str(props.get("class", "")), str(props.get("subclass", "")),
                                         str(props.get("name", "") or ""))
                        if tags:
                            lon, lat = ll[0]
                            pois.append((tags, lat, lon))
    return _build(lines, named, pois, to_m, (lat0, lon0), kx, ky, radius_m)


def _road_tags(p: dict) -> Optional[dict]:
    cls, sub = str(p.get("class", "")), str(p.get("subclass", ""))
    if cls == "path":
        hw = PATH_SUBCLASS.get(sub, "path")
    elif cls.endswith("_construction"):
        return None
    else:
        hw = CLASS_TO_HIGHWAY.get(cls)
    if not hw:
        return None
    if p.get("ramp") == 1 and hw in ("motorway", "trunk", "primary", "secondary", "tertiary"):
        hw += "_link"
    tags = {"highway": hw}
    if p.get("oneway") == 1:
        tags["oneway"] = "yes"
    elif p.get("oneway") == -1:
        tags["oneway"] = "-1"
    if p.get("access") == "no":
        tags["access"] = "no"
    return tags


def _build(lines, named, pois, to_m, origin, kx, ky, radius_m) -> dict:
    lat0, lon0 = origin
    # Like the Overpass queries: every street within the radius, main roads and places within
    # 2x the radius (at least 3 km) for ambulance routes and diversions.
    near, far = radius_m * 1.05, max(3000.0, 2 * radius_m)
    main = ("motorway", "trunk", "primary", "secondary")
    # 1. Points in metres, snapped to a grid so pieces of a line cut at tile edges and crossing lines
    #    that share a junction get the same node.
    grid: Dict[Tuple[int, int], int] = {}
    coords: Dict[int, Tuple[float, float]] = {}
    nid = 0

    def node(x: float, y: float) -> int:
        nonlocal nid
        gx, gy = int(round(x / SNAP_M)), int(round(y / SNAP_M))
        for dx in (0, -1, 1):
            for dy in (0, -1, 1):
                k = grid.get((gx + dx, gy + dy))
                if k is not None and math.hypot(coords[k][0] - x, coords[k][1] - y) <= SNAP_M:
                    return k
        nid += 1
        grid[(gx, gy)] = nid
        coords[nid] = (x, y)
        return nid

    # 2. Junctions: two streets that cross without a shared point are joined where a vertex of one lies on
    #    the other (common after tile simplification). Insert those vertices into the segment they touch.
    pieces = []
    for tags, ll in lines:
        pts = [to_m(lon, lat) for lon, lat in ll]
        limit = far if tags["highway"].replace("_link", "") in main else near
        if all(math.hypot(x, y) > limit for x, y in pts):
            continue
        pieces.append((tags, pts))
    _insert_touching_vertices(pieces)

    names = _NameIndex(named)
    ways = []
    wid = 0
    for tags, pts in pieces:
        nodes = []
        for x, y in pts:
            n = node(x, y)
            if not nodes or nodes[-1] != n:
                nodes.append(n)
        if len(nodes) < 2:
            continue
        wid += 1
        name = names.name_for(pts)
        t = dict(tags, **({"name": name} if name else {}))
        ways.append({"type": "way", "id": wid, "nodes": nodes, "tags": t})

    els = []
    for k, (x, y) in coords.items():
        els.append({"type": "node", "id": k, "lat": lat0 + y / ky, "lon": lon0 + x / kx})
    els += ways
    seen = set()
    for i, (tags, lat, lon) in enumerate(pois):
        if math.hypot((lon - lon0) * kx, (lat - lat0) * ky) > far:
            continue
        key = (tags.get("name"), round(lat, 5), round(lon, 5))
        if key in seen:
            continue
        seen.add(key)
        els.append({"type": "node", "id": 10_000_000 + i, "lat": lat, "lon": lon, "tags": tags})
    return {"elements": els, "source": "openfreemap-tiles"}


def _insert_touching_vertices(pieces) -> None:
    """Where a vertex of one street lies within SNAP_M of another street's segment, add it to that
    segment, so the two streets connect there."""
    cell = 25.0
    index: Dict[Tuple[int, int], List[Tuple[int, int]]] = {}
    for pi, (_, pts) in enumerate(pieces):
        for si in range(len(pts) - 1):
            (x1, y1), (x2, y2) = pts[si], pts[si + 1]
            for gx in range(int(min(x1, x2) // cell), int(max(x1, x2) // cell) + 1):
                for gy in range(int(min(y1, y2) // cell), int(max(y1, y2) // cell) + 1):
                    index.setdefault((gx, gy), []).append((pi, si))
    inserts: Dict[Tuple[int, int], List[Tuple[float, Tuple[float, float]]]] = {}
    for pi, (_, pts) in enumerate(pieces):
        for x, y in (pts[0], pts[-1]) if len(pts) > 1 else ():
            for qi, si in index.get((int(x // cell), int(y // cell)), ()):
                if qi == pi:
                    continue
                (x1, y1), (x2, y2) = pieces[qi][1][si], pieces[qi][1][si + 1]
                dx, dy = x2 - x1, y2 - y1
                L2 = dx * dx + dy * dy
                if L2 < 1e-6:
                    continue
                t = ((x - x1) * dx + (y - y1) * dy) / L2
                if not 0.02 < t < 0.98:
                    continue
                px, py = x1 + t * dx, y1 + t * dy
                if math.hypot(px - x, py - y) <= SNAP_M:
                    inserts.setdefault((qi, si), []).append((t, (x, y)))
    for (qi, si), adds in sorted(inserts.items(), key=lambda kv: (kv[0][0], -kv[0][1])):
        pts = pieces[qi][1]
        for _, p in sorted(adds, reverse=True):
            pts.insert(si + 1, p)


class _NameIndex:
    """Named street lines in a grid of NAME_M-sized cells, for fast nearest-name lookups."""

    def __init__(self, named):
        self.cells: Dict[Tuple[int, int], List[Tuple[int, Tuple[float, float], Tuple[float, float]]]] = {}
        self.names = [n for n, _, _ in named]
        c = NAME_M
        for k, (_, _, line) in enumerate(named):
            for a, b in zip(line, line[1:]):
                for gx in range(int(min(a[0], b[0]) // c) - 1, int(max(a[0], b[0]) // c) + 2):
                    for gy in range(int(min(a[1], b[1]) // c) - 1, int(max(a[1], b[1]) // c) + 2):
                        self.cells.setdefault((gx, gy), []).append((k, a, b))

    def name_for(self, pts) -> str:
        """The name of the named line running along most of this street piece (within NAME_M)."""
        sample = pts if len(pts) <= 6 else [pts[i] for i in range(0, len(pts), max(1, len(pts) // 6))]
        score: Dict[int, List[float]] = {}
        for p in sample:
            near: Dict[int, float] = {}
            for k, a, b in self.cells.get((int(p[0] // NAME_M), int(p[1] // NAME_M)), ()):
                d = _dist_to_segment(p, a, b)
                if d <= NAME_M and d < near.get(k, math.inf):
                    near[k] = d
            for k, d in near.items():
                score.setdefault(k, []).append(d)
        best, best_key = "", None
        for k, ds in score.items():
            if len(ds) * 2 < len(sample):  # must run along most of the piece, not just cross it
                continue
            key = (-len(ds), sum(ds) / len(ds))
            if best_key is None or key < best_key:
                best, best_key = self.names[k], key
        return best


def _dist_to_segment(p, a, b) -> float:
    (x1, y1), (x2, y2) = a, b
    dx, dy = x2 - x1, y2 - y1
    L2 = dx * dx + dy * dy
    t = 0.0 if L2 < 1e-9 else max(0.0, min(1.0, ((p[0] - x1) * dx + (p[1] - y1) * dy) / L2))
    return math.hypot(p[0] - (x1 + t * dx), p[1] - (y1 + t * dy))


# ------------------------------------------------------------------ fetching (Python side)
def tile_url_template(timeout: float = 20) -> str:
    req = urllib.request.Request(TILEJSON, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())["tiles"][0]


def fetch_tiles_osm(lat: float, lon: float, radius_m: float = 1500, timeout: float = 30) -> dict:
    """Download the tiles around a point (in parallel) and convert them. Usually 1-3 seconds."""
    import concurrent.futures as cf

    template = tile_url_template(timeout)
    want = tiles_around(lat, lon, max(3000.0, 2 * radius_m))

    def get(t):
        z, x, y = t
        req = urllib.request.Request(template.format(z=z, x=x, y=y), headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return t, r.read()

    with cf.ThreadPoolExecutor(max_workers=8) as pool:
        tiles = list(pool.map(get, want))
    return tiles_to_osm(tiles, (lat, lon), radius_m)
