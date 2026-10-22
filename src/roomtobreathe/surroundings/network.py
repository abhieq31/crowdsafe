"""The street network around a venue, for people on foot and for vehicles.

OSM gives the street type, name and one-way rules, but almost never widths. Each street type therefore
carries planning defaults, all overridable per street:

- **walk width** (m): the footpath people actually use. On small streets people walk on the road.
- **road width** (m): the carriageway. If a street is *closed to traffic* for the event, it becomes
  walkable too, which is often the single best fix for a crowded exit route.
- **vehicle capacity** per lane (vehicles/hour, urban roads with junctions) and a typical speed.

Pedestrian capacity uses the same exit-flow figure as the venue (82 people per metre per minute, the
Green Guide's level-walkway design flow), and comfort uses Fruin's walkway Levels of Service.
Standard library only, so it runs in the browser (Pyodide) as well.
"""

from __future__ import annotations

import heapq
import math
import re
from dataclasses import dataclass, field
from typing import Callable, Dict, Iterable, List, Optional, Tuple

from ..physics import FLOW_LEVEL_PPM_PER_MIN, FLOW_STAIRS_PPM_PER_MIN

M_PER_DEG = 111_320.0


def to_local(origin: Tuple[float, float], lat: float, lon: float) -> Tuple[float, float]:
    lat0, lon0 = origin
    return ((lon - lon0) * M_PER_DEG * math.cos(math.radians(lat0)), (lat - lat0) * M_PER_DEG)


def to_latlon(origin: Tuple[float, float], x: float, y: float) -> Tuple[float, float]:
    lat0, lon0 = origin
    return (round(lat0 + y / M_PER_DEG, 7), round(lon0 + x / (M_PER_DEG * math.cos(math.radians(lat0))), 7))


@dataclass(frozen=True)
class RoadType:
    walk: bool
    drive: bool
    walk_width: float  # usable footpath width, both sides together (m)
    road_width: float  # carriageway (m); 0 for paths
    lanes: int  # total lanes, both directions
    lane_vph: float  # vehicles per hour per lane
    speed_kmh: float
    rank: int  # importance: 5 = main road ... 0 = path
    background_vc: float  # usual traffic as a share of capacity when an event is on (evening peak)


ROAD_TYPES: Dict[str, RoadType] = {
    "motorway": RoadType(False, True, 0, 21, 6, 1800, 60, 6, 0.7),
    "trunk": RoadType(False, True, 0, 14, 4, 1200, 45, 6, 0.7),
    "primary": RoadType(True, True, 3.0, 14, 4, 800, 30, 5, 0.65),
    "secondary": RoadType(True, True, 2.5, 9, 2, 750, 25, 4, 0.6),
    "tertiary": RoadType(True, True, 2.0, 7, 2, 650, 25, 3, 0.5),
    "unclassified": RoadType(True, True, 1.5, 6, 2, 500, 20, 2, 0.3),
    "residential": RoadType(True, True, 1.5, 6, 2, 450, 20, 2, 0.3),
    "living_street": RoadType(True, True, 3.0, 4, 1, 200, 10, 1, 0.1),
    "service": RoadType(True, True, 2.5, 4, 1, 250, 15, 1, 0.15),
    "pedestrian": RoadType(True, False, 6.0, 0, 0, 0, 0, 1, 0),
    "footway": RoadType(True, False, 2.0, 0, 0, 0, 0, 0, 0),
    "path": RoadType(True, False, 1.5, 0, 0, 0, 0, 0, 0),
    "cycleway": RoadType(True, False, 2.0, 0, 0, 0, 0, 0, 0),
    "steps": RoadType(True, False, 2.0, 0, 0, 0, 0, 0, 0),
    "corridor": RoadType(True, False, 2.0, 0, 0, 0, 0, 0, 0),
    "track": RoadType(True, True, 2.5, 3, 1, 100, 10, 0, 0.05),
}
for _base in ("motorway", "trunk", "primary", "secondary", "tertiary"):
    t = ROAD_TYPES[_base]
    ROAD_TYPES[_base + "_link"] = RoadType(
        t.walk,
        t.drive,
        t.walk_width,
        t.road_width / 2,
        max(1, t.lanes // 2),
        t.lane_vph,
        t.speed_kmh * 0.8,
        t.rank - 1,
        t.background_vc,
    )

ROAD_LABEL = {
    "motorway": "motorway",
    "trunk": "highway",
    "primary": "main road",
    "secondary": "main road",
    "tertiary": "street",
    "unclassified": "street",
    "residential": "lane",
    "living_street": "lane",
    "service": "service lane",
    "pedestrian": "pedestrian street",
    "footway": "footpath",
    "path": "path",
    "cycleway": "path",
    "steps": "steps",
    "corridor": "passage",
    "track": "track",
}


def _num(v) -> Optional[float]:
    try:
        return float(str(v).split(";")[0].replace("m", "").strip())
    except (TypeError, ValueError):
        return None


@dataclass
class Edge:
    id: int
    u: int
    v: int
    way: int
    length: float
    highway: str
    name: str
    walk: bool
    drive_fwd: bool  # u -> v allowed for vehicles
    drive_back: bool
    walk_width: float
    road_width: float
    lanes: int
    lane_vph: float
    speed_kmh: float
    rank: int
    background_vc: float
    stairs: bool = False
    estimated_width: bool = True
    closed_to_traffic: bool = False

    @property
    def label(self) -> str:
        return self.name or ("unnamed " + ROAD_LABEL.get(self.highway.replace("_link", ""), "street"))

    @property
    def drivable(self) -> bool:
        return (self.drive_fwd or self.drive_back) and not self.closed_to_traffic

    def effective_walk_width(self) -> float:
        """Walking width; a street closed to traffic adds its carriageway."""
        return self.walk_width + (self.road_width if self.closed_to_traffic else 0.0)

    def ped_capacity_ppm(self) -> float:
        """Design flow, people per minute across the whole walking width."""
        rate = FLOW_STAIRS_PPM_PER_MIN if self.stairs else FLOW_LEVEL_PPM_PER_MIN
        return max(0.3, self.effective_walk_width()) * rate

    def vehicle_capacity_vph(self) -> float:
        """Per direction."""
        if not self.drivable:
            return 0.0
        dirs = 2 if (self.drive_fwd and self.drive_back) else 1
        return max(1, self.lanes // dirs if dirs == 2 else self.lanes) * self.lane_vph


@dataclass
class Poi:
    id: str
    kind: str  # metro | rail | tram | ferry | bus | bus_station | parking | taxi | hospital | clinic | police | fire
    name: str
    x: float
    y: float
    tags: dict = field(default_factory=dict)
    access: List[Tuple[float, float]] = field(default_factory=list)  # entrances (stations); else [(x, y)]

    @property
    def label(self) -> str:
        return self.name or {
            "metro": "Metro station",
            "rail": "Railway station",
            "bus": "Bus stop",
            "parking": "Car park",
            "hospital": "Hospital",
        }.get(self.kind, self.kind.title())


class StreetNetwork:
    """Nodes in local metres around ``origin``; edges per OSM segment; separate walk and drive graphs."""

    def __init__(self, origin: Tuple[float, float]):
        self.origin = tuple(origin)
        self.nodes: Dict[int, Tuple[float, float]] = {}
        self.edges: List[Edge] = []
        self.walk_adj: Dict[int, List[Tuple[int, int]]] = {}  # node -> [(neighbour, edge id)]
        self.drive_adj: Dict[int, List[Tuple[int, int]]] = {}
        self.pois: List[Poi] = []
        self.radius = 0.0
        self.notes: List[str] = []

    # ------------------------------------------------------------------ building
    @classmethod
    def from_overpass(
        cls, data: dict, origin: Tuple[float, float], overrides: Optional[Dict[str, dict]] = None
    ) -> "StreetNetwork":
        """Build the network. ``overrides`` maps an OSM way id (as text) to corrections from people who
        know the place: ``walk_width`` / ``road_width`` in metres, ``closed_to_traffic``, ``no_walk``."""
        net = cls(origin)
        overrides = overrides or {}
        els = data.get("elements", [])
        coords = {e["id"]: (e["lat"], e["lon"]) for e in els if e["type"] == "node" and "lat" in e}
        widths_known = 0
        n_ways = 0
        for e in els:
            if e["type"] != "way" or "highway" not in e.get("tags", {}):
                continue
            tags = e["tags"]
            hw = tags["highway"]
            rt = ROAD_TYPES.get(hw)
            if rt is None or tags.get("area") == "yes":
                continue
            n_ways += 1
            ov = overrides.get(str(e["id"]), {})
            walk = rt.walk and tags.get("foot") != "no" and tags.get("access") not in ("no",)
            if tags.get("foot") in ("yes", "designated"):
                walk = True
            drive = rt.drive and tags.get("motor_vehicle") != "no" and tags.get("access") not in ("no", "private")
            oneway = tags.get("oneway") in ("yes", "1", "true") or tags.get("junction") == "roundabout"
            reverse = tags.get("oneway") == "-1"
            walk_w, road_w, est = rt.walk_width, rt.road_width, True
            w = _num(tags.get("width"))
            if w:
                est = False
                widths_known += 1
                if rt.drive:
                    road_w = w
                else:
                    walk_w = w
            sw = tags.get("sidewalk")
            if rt.drive and sw in ("no", "none"):
                walk_w = min(walk_w, 1.0)  # people walk at the road's edge
            elif rt.drive and sw in ("left", "right"):
                walk_w = walk_w / 2
            lanes = int(_num(tags.get("lanes")) or rt.lanes)
            # Corrections from people who know the place (see overview: venue["surroundings"]["overrides"]).
            if "walk_width" in ov:
                walk_w, est = max(0.0, float(ov["walk_width"])), False
            if "road_width" in ov:
                road_w = max(0.0, float(ov["road_width"]))
            if ov.get("no_walk"):  # blocked: construction, a market, a locked gate
                walk = False
            closed = bool(ov.get("closed_to_traffic", False))
            nd = [n for n in e.get("nodes", []) if n in coords]
            for a, b in zip(nd, nd[1:]):
                for nid in (a, b):
                    if nid not in net.nodes:
                        net.nodes[nid] = to_local(origin, *coords[nid])
                (x1, y1), (x2, y2) = net.nodes[a], net.nodes[b]
                length = math.hypot(x2 - x1, y2 - y1)
                if length < 0.05:
                    continue
                edge = Edge(
                    id=len(net.edges),
                    u=a,
                    v=b,
                    way=e["id"],
                    length=length,
                    highway=hw,
                    name=tags.get("name", "") or tags.get("ref", ""),
                    walk=walk,
                    drive_fwd=drive and not reverse,
                    drive_back=drive and (not oneway or reverse),
                    walk_width=walk_w,
                    road_width=road_w,
                    lanes=lanes,
                    lane_vph=rt.lane_vph,
                    speed_kmh=min(_num(tags.get("maxspeed")) or rt.speed_kmh, rt.speed_kmh),  # urban: rarely faster
                    rank=rt.rank,
                    background_vc=rt.background_vc,
                    stairs=hw == "steps",
                    estimated_width=est,
                    closed_to_traffic=closed,
                )
                net.edges.append(edge)
        net._index()
        net._read_pois(els)
        net.radius = max((math.hypot(*p) for p in net.nodes.values()), default=0.0)
        if n_ways and widths_known / n_ways < 0.3:
            net.notes.append(
                f"Only {widths_known} of {n_ways} streets have a recorded width in OpenStreetMap, so "
                "footpath and road widths are estimated from the street type. Check the busiest "
                "routes on the ground and correct them."
            )
        return net

    def _index(self) -> None:
        self.walk_adj = {n: [] for n in self.nodes}
        self.drive_adj = {n: [] for n in self.nodes}
        for e in self.edges:
            if e.walk or e.closed_to_traffic:
                self.walk_adj[e.u].append((e.v, e.id))
                self.walk_adj[e.v].append((e.u, e.id))
            if e.drive_fwd and not e.closed_to_traffic:
                self.drive_adj[e.u].append((e.v, e.id))
            if e.drive_back and not e.closed_to_traffic:
                self.drive_adj[e.v].append((e.u, e.id))
        self.__dict__.pop("_rev", None)
        self._grid: Dict[Tuple[int, int], List[int]] = {}
        for n, (x, y) in self.nodes.items():
            self._grid.setdefault((int(x // 50), int(y // 50)), []).append(n)

    def set_closed_to_traffic(self, ways: Iterable[int], closed: bool = True) -> None:
        ways = set(ways)
        for e in self.edges:
            if e.way in ways:
                e.closed_to_traffic = closed
        self._index()

    def _read_pois(self, els: List[dict]) -> None:
        seen = set()
        for e in els:
            tags = e.get("tags") or {}
            if e["type"] == "node" and "lat" in e:
                lat, lon = e["lat"], e["lon"]
            elif "center" in e:
                lat, lon = e["center"]["lat"], e["center"]["lon"]
            else:
                continue
            kind = poi_kind(tags)
            if not kind:
                continue
            name = tags.get("name", "")
            key = (kind, name, round(lat, 3), round(lon, 3)) if name else (kind, e["type"], e["id"])
            if key in seen:
                continue
            seen.add(key)
            x, y = to_local(self.origin, lat, lon)
            self.pois.append(Poi(f"{e['type'][0]}{e['id']}", kind, name, x, y, tags))
        self._merge_stations()

    def _merge_stations(self) -> None:
        """One entry per station. OSM maps a metro station as a station point plus many entrances
        ("Esplanade Gate 7"), platforms and stop positions; people can use any entrance."""
        cluster_m = {"metro": 350, "rail": 350, "tram": 120, "ferry": 250, "bus_station": 200, "bus": 120}
        out: List[Poi] = []
        groups: List[List[Poi]] = []
        for p in self.pois:
            if p.kind not in cluster_m:
                p.access = [(p.x, p.y)]
                out.append(p)
                continue
            for g in groups:
                if (
                    g[0].kind == p.kind
                    and math.hypot(g[0].x - p.x, g[0].y - p.y) < cluster_m[p.kind]
                    and (_base_name(g[0].name) == _base_name(p.name) or not p.name or not g[0].name)
                ):
                    g.append(p)
                    break
            else:
                groups.append([p])
        for g in groups:
            main = next(
                (
                    p
                    for p in g
                    if p.tags.get("railway") == "station"
                    or p.tags.get("public_transport") == "station"
                    or p.kind in ("bus_station", "ferry")
                ),
                g[0],
            )
            name = _base_name(main.name) or next((_base_name(p.name) for p in g if p.name), "")
            out.append(Poi(main.id, main.kind, name, main.x, main.y, main.tags, access=[(p.x, p.y) for p in g]))
        self.pois = out

    # ------------------------------------------------------------------ queries
    def nearest_node(self, x: float, y: float, mode: str = "walk", max_m: float = 2000.0) -> Optional[int]:
        adj = self.walk_adj if mode == "walk" else self.drive_adj
        cx, cy = int(x // 50), int(y // 50)
        best, best_d = None, max_m
        for ring in range(0, int(max_m // 50) + 2):
            for i in range(cx - ring, cx + ring + 1):
                for j in range(cy - ring, cy + ring + 1):
                    if ring and abs(i - cx) != ring and abs(j - cy) != ring:
                        continue
                    for n in self._grid.get((i, j), ()):
                        if not adj.get(n):
                            continue
                        d = math.hypot(self.nodes[n][0] - x, self.nodes[n][1] - y)
                        if d < best_d:
                            best, best_d = n, d
            if best is not None and best_d <= ring * 50:
                break
        return best

    def boundary_nodes(self, mode: str = "walk", min_rank: int = 0, frac: float = 0.85) -> List[int]:
        """Nodes near the edge of the downloaded area: where people and cars come from and go to."""
        adj = self.walk_adj if mode == "walk" else self.drive_adj
        out = []
        for n, (x, y) in self.nodes.items():
            if math.hypot(x, y) < frac * self.radius or not adj.get(n):
                continue
            if max((self.edges[e].rank for _, e in adj[n]), default=-1) >= min_rank:
                out.append(n)
        return out

    def shortest_paths(
        self, source: int, cost: Callable[[Edge], float], mode: str = "walk", reverse: bool = False
    ) -> Tuple[Dict[int, float], Dict[int, Tuple[int, int]]]:
        """Dijkstra from ``source``. ``reverse`` follows drive edges backwards (paths *to* source)."""
        if mode == "walk":
            adj = self.walk_adj
        elif not reverse:
            adj = self.drive_adj
        else:
            adj = self.__dict__.get("_rev")
            if adj is None:
                adj = {n: [] for n in self.nodes}
                for a, lst in self.drive_adj.items():
                    for b, eid in lst:
                        adj[b].append((a, eid))
                self._rev = adj
        dist = {source: 0.0}
        prev: Dict[int, Tuple[int, int]] = {}
        heap = [(0.0, source)]
        while heap:
            d, n = heapq.heappop(heap)
            if d > dist.get(n, math.inf):
                continue
            for m, eid in adj.get(n, ()):
                nd = d + cost(self.edges[eid])
                if nd < dist.get(m, math.inf):
                    dist[m] = nd
                    prev[m] = (n, eid)
                    heapq.heappush(heap, (nd, m))
        return dist, prev

    @staticmethod
    def path_edges(prev: Dict[int, Tuple[int, int]], target: int) -> List[int]:
        out = []
        n = target
        while n in prev:
            n, eid = prev[n]
            out.append(eid)
        out.reverse()
        return out

    def edge_mid(self, eid: int) -> Tuple[float, float]:
        e = self.edges[eid]
        (x1, y1), (x2, y2) = self.nodes[e.u], self.nodes[e.v]
        return ((x1 + x2) / 2, (y1 + y2) / 2)

    def latlon(self, x: float, y: float) -> Tuple[float, float]:
        return to_latlon(self.origin, x, y)

    def street_name(self, eid: int) -> str:
        """The street an edge belongs to. OSM often leaves split sections (dual carriageways, short links)
        unnamed; follow unnamed edges of the same street type up to a few hops to find the name."""
        cache = self.__dict__.setdefault("_names", {})
        if eid in cache:
            return cache[eid]
        e = self.edges[eid]
        name = e.name
        if not name:
            base = e.highway.replace("_link", "")
            seen, frontier = {eid}, [eid]
            for _ in range(4):
                nxt = []
                for f in frontier:
                    fe = self.edges[f]
                    for node in (fe.u, fe.v):
                        for _, o in self.walk_adj.get(node, ()) or self.drive_adj.get(node, ()):
                            if o in seen:
                                continue
                            seen.add(o)
                            oe = self.edges[o]
                            if oe.highway.replace("_link", "") != base:
                                continue
                            if oe.name:
                                name = oe.name
                                break
                            nxt.append(o)
                        if name:
                            break
                    if name:
                        break
                if name:
                    break
                frontier = nxt
        cache[eid] = name
        return name

    def describe(self, eid: int) -> str:
        """A street's name; for an unnamed lane, the named street it joins ("service lane off Strand Road")."""
        e = self.edges[eid]
        if self.street_name(eid):
            return self.street_name(eid)
        for n in (e.u, e.v):
            for _, o in self.walk_adj.get(n, ()):
                if self.edges[o].name:
                    return f"{e.label} off {self.edges[o].name}"
        x, y = self.edge_mid(eid)
        near = self.street_at(x, y)
        return f"{e.label} near {near}" if near else e.label

    def via(self, edges: List[int]) -> List[str]:
        """The named streets a route follows, in order (unnamed links are skipped)."""
        out: List[str] = []
        for eid in edges:
            n = self.street_name(eid)
            if n and (not out or out[-1] != n) and n not in out[-2:]:
                out.append(n)
        return out

    def street_at(self, x: float, y: float, within_m: float = 150.0) -> str:
        """The nearest named street to a point (within ``within_m``)."""
        best, best_d = "", within_m
        cx, cy, r = int(x // 50), int(y // 50), int(within_m // 50) + 1
        for i in range(cx - r, cx + r + 1):
            for j in range(cy - r, cy + r + 1):
                for n in self._grid.get((i, j), ()):
                    d = math.hypot(self.nodes[n][0] - x, self.nodes[n][1] - y)
                    if d >= best_d:
                        continue
                    for _, eid in self.walk_adj.get(n, ()) or self.drive_adj.get(n, ()):
                        if self.edges[eid].name:
                            best, best_d = self.edges[eid].name, d
                            break
        return best


def poi_kind(tags: dict) -> Optional[str]:
    a = tags.get("amenity", "")
    rw = tags.get("railway", "")
    pt = tags.get("public_transport", "")
    if a == "hospital" or tags.get("healthcare") == "hospital":
        return "hospital"
    if a == "clinic" or tags.get("healthcare") == "clinic":
        return "clinic"
    if a in ("police", "fire_station", "parking", "taxi"):
        return {"fire_station": "fire"}.get(a, a)
    if a == "ferry_terminal" or tags.get("ferry") == "yes":
        return "ferry"
    if a == "bus_station":
        return "bus_station"
    if tags.get("station") == "subway" or tags.get("subway") == "yes" or rw == "subway_entrance":
        return "metro"
    if rw == "tram_stop" or tags.get("tram") == "yes":
        return "tram"
    if rw in ("station", "halt") or tags.get("train") == "yes":
        return "rail"
    if tags.get("highway") == "bus_stop" or tags.get("bus") == "yes" or (pt == "platform" and tags.get("bus")):
        return "bus"
    return None


def _base_name(name: str) -> str:
    """'Esplanade Gate 7' and 'Esplanade (Line 2)' are both Esplanade."""
    n = re.sub(r"\s*\((line|gate|exit|platform)[^)]*\)", "", name or "", flags=re.I)
    n = re.sub(r"\s+(gate|exit|entrance|platform)\s*[\w-]*$", "", n, flags=re.I)
    return re.sub(r"\s+(metro station|metro|station)$", "", n.strip(), flags=re.I).strip()


SPECIALIST = re.compile(
    r"eye|netra|nethralaya|dental|maternity|nursing home|diagnostic|ayur|homoeo|homeo|"
    r"dermat|skin|ivf|fertility|psychiatr|dialysis|physio",
    re.I,
)


def hospital_score(p: Poi) -> float:
    """How suitable a hospital is for crush injuries (trauma, breathing). OSM rarely says, so this is a
    transparent guess from its tags and name; organisers must confirm with the health department."""
    t, name = p.tags, p.name or ""
    s = 0.0
    if t.get("emergency") == "yes":
        s += 3
    if t.get("emergency") == "no" or t.get("healthcare:speciality") or SPECIALIST.search(name):
        s -= 4
    if re.search(r"medical college|district hospital|general hospital|trauma|sskm|aiims|civil hospital", name, re.I):
        s += 2
    if t.get("operator:type") in ("government", "public"):
        s += 1
    if p.kind == "clinic":
        s -= 2
    return s
