"""Find a venue's ways in automatically: where the streets and paths cross its outline.

The customer types a place name; OpenStreetMap gives the ground's outline (a stadium, a park, a maidan,
a temple compound). This module finds the openings: every walkable street or path that crosses the
outline is a way in or out. Nearby crossings are merged into one opening, and its width is estimated from
the street type (a footpath is narrower than a road). If no mapped path crosses the outline (common for
fenced grounds whose gates are not mapped), the nearest points to the surrounding streets are suggested
instead, clearly marked as guesses.

Everything here is an estimate for a first verdict in seconds; the organiser adjusts gates on the map.
Standard library only (runs in the browser).
"""

from __future__ import annotations

import math
from typing import Dict, List, Optional, Sequence, Tuple

from ..venue import polygon_area
from .network import StreetNetwork, to_latlon, to_local

MERGE_M = 20.0  # crossings closer than this are one opening
MIN_W, MAX_W = 2.0, 12.0
GUESS_WITHIN_M = 40.0
MAX_GATES = 16


def _inside(x: float, y: float, poly: Sequence[Tuple[float, float]]) -> bool:
    inside = False
    j = len(poly) - 1
    for i in range(len(poly)):
        xi, yi = poly[i]
        xj, yj = poly[j]
        if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / ((yj - yi) or 1e-12) + xi:
            inside = not inside
        j = i
    return inside


def _cross(p1, p2, q1, q2) -> Optional[Tuple[float, float, float]]:
    """Intersection of segments p1p2 and q1q2: (x, y, t along q) or None."""
    d1x, d1y = p2[0] - p1[0], p2[1] - p1[1]
    d2x, d2y = q2[0] - q1[0], q2[1] - q1[1]
    den = d1x * d2y - d1y * d2x
    if abs(den) < 1e-12:
        return None
    t = ((q1[0] - p1[0]) * d2y - (q1[1] - p1[1]) * d2x) / den
    u = ((q1[0] - p1[0]) * d1y - (q1[1] - p1[1]) * d1x) / den
    if 0 <= t <= 1 and 0 <= u <= 1:
        return p1[0] + t * d1x, p1[1] + t * d1y, u
    return None


def _width(e) -> float:
    """Estimated opening width where this street meets the venue."""
    if e.drive_fwd or e.drive_back:
        w = e.road_width * 0.6 + e.walk_width / 2  # a vehicle gate is rarely the full road width
    else:
        w = e.walk_width
    return round(max(MIN_W, min(MAX_W, w)) * 2) / 2


def _closest_on_ring(x: float, y: float, ring) -> Tuple[float, float, Tuple[float, float], float]:
    """Closest point on the outline: (px, py, unit direction of that edge, distance)."""
    best = None
    for a, b in zip(ring, ring[1:] + ring[:1]):
        dx, dy = b[0] - a[0], b[1] - a[1]
        L2 = dx * dx + dy * dy
        if L2 < 1e-9:
            continue
        t = max(0.0, min(1.0, ((x - a[0]) * dx + (y - a[1]) * dy) / L2))
        px, py = a[0] + t * dx, a[1] + t * dy
        d = math.hypot(px - x, py - y)
        if best is None or d < best[3]:
            L = math.sqrt(L2)
            best = (px, py, (dx / L, dy / L), d)
    return best


def detect_gates(
    osm: dict, ring_latlon: List[Tuple[float, float]], origin: Optional[Tuple[float, float]] = None, name: str = ""
) -> Dict:
    """Openings of a venue outline ``ring_latlon`` ([lat, lon] points) from map data ``osm``.

    Returns ``{"origin", "area_m2", "gates": [{"name", "street", "width_m", "line" ([lat, lon] x2),
    "guessed"}], "notes"}``. ``line`` is drawn along the outline, centred on the opening, its length
    the estimated width, so it plugs straight into the venue file."""
    if len(ring_latlon) < 3:
        raise ValueError("an outline needs at least 3 points")
    if origin is None:
        origin = (sum(p[0] for p in ring_latlon) / len(ring_latlon), sum(p[1] for p in ring_latlon) / len(ring_latlon))
    ring = [to_local(origin, lat, lon) for lat, lon in ring_latlon]
    if ring[0] == ring[-1]:
        ring = ring[:-1]
    net = StreetNetwork.from_overpass(osm, origin)
    found: List[dict] = []
    for e in net.edges:
        if not e.walk:
            continue
        a, b = net.nodes[e.u], net.nodes[e.v]
        if _inside(*a, ring) == _inside(*b, ring):
            continue
        for q1, q2 in zip(ring, ring[1:] + ring[:1]):
            hit = _cross(a, b, q1, q2)
            if hit:
                L = math.hypot(q2[0] - q1[0], q2[1] - q1[1]) or 1.0
                found.append(
                    {
                        "x": hit[0],
                        "y": hit[1],
                        "dir": ((q2[0] - q1[0]) / L, (q2[1] - q1[1]) / L),
                        "width": _width(e),
                        "street": net.street_name(e.id),
                        "rank": e.rank,
                        "guessed": False,
                    }
                )
                break
    notes = []
    if not found:  # fenced ground with unmapped gates: suggest where the nearest streets come close
        for n, (x, y) in net.nodes.items():
            if _inside(x, y, ring) or not net.walk_adj.get(n):
                continue
            px, py, d, dist = _closest_on_ring(x, y, ring)
            if dist > GUESS_WITHIN_M:
                continue
            rank = max(net.edges[eid].rank for _, eid in net.walk_adj[n])
            street = next((net.street_name(eid) for _, eid in net.walk_adj[n] if net.street_name(eid)), "")
            found.append({"x": px, "y": py, "dir": d, "width": 4.0, "street": street, "rank": rank, "guessed": True})
        if found:
            notes.append(
                "No mapped path crosses the outline, so the ways in are guesses at the points nearest the "
                "surrounding streets. Move them to the real gates."
            )
    # Merge crossings that are really one opening; prefer the bigger street.
    found.sort(key=lambda f: (-f["rank"], -f["width"]))
    gates: List[dict] = []
    for f in found:
        if all(math.hypot(f["x"] - g["x"], f["y"] - g["y"]) >= (MERGE_M if not f["guessed"] else 60) for g in gates):
            gates.append(f)
    gates = gates[:MAX_GATES]
    if not gates:
        notes.append("No streets were found next to the outline. Draw the ways in on the map.")
    out = []
    for i, g in enumerate(sorted(gates, key=lambda g: math.atan2(g["y"], g["x"]))):
        h = g["width"] / 2
        (dx, dy) = g["dir"]
        line = [
            to_latlon(origin, g["x"] - dx * h, g["y"] - dy * h),
            to_latlon(origin, g["x"] + dx * h, g["y"] + dy * h),
        ]
        label = f"Way in {i + 1}" + (f" ({g['street']})" if g["street"] else "")
        out.append(
            {
                "name": label,
                "street": g["street"],
                "width_m": g["width"],
                "line": [list(p) for p in line],
                "guessed": g["guessed"],
            }
        )
    if out and not any(g["guessed"] for g in out):
        notes.append("Widths are estimated from the street type. Measure the real openings and correct them.")
    return {"origin": list(origin), "area_m2": round(polygon_area(ring)), "gates": out, "notes": notes, "name": name}
