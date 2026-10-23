"""Step 3: vehicles. Event cars and taxis, the normal traffic, road closures and diversions.

- **Event vehicles**: the car/taxi share of attendees, divided by people per vehicle, drive in from the
  main roads at the edge of the area to car parks and pick-up/drop-off points, and back out afterwards.
- **Normal traffic**: each road already carries a share of its capacity at an evening peak
  (``RoadType.background_vc``), which is what the public needs to keep moving.
- **Pick-up points** are chosen on decent roads 250-800 m walk from the exits and *away from the
  streets the crowd walks on*, so taxis do not stop in the middle of the exit flow (a common cause of
  dangerous mixing).
- **Closures**: streets the crowd needs (from step 1) are closed to traffic for the departure, and each
  closure gets a **diversion** between its ends on the remaining roads.
- **Conflict points**: junctions where heavy crowd flow meets heavy traffic. These need traffic police.

Road delay uses the standard BPR function, t = t0 (1 + 0.15 (v/c)^4). It is coarse, but it shows where
the event pushes roads over capacity, which is what a traffic plan needs.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List, Set, Tuple

from .dispersal import Access, Destination, Phase
from .network import StreetNetwork

PEOPLE_PER_VEHICLE = 2.2
PARKING_DEFAULT_SPACES = 250  # when OSM does not say
INCREMENTS = 4


BPR_MAX_VC = 2.5  # beyond this the formula is meaningless: demand simply queues (see ``clear_min``)


def bpr_time(e, flow_vph: float, max_vc: float = BPR_MAX_VC) -> float:
    cap = e.vehicle_capacity_vph()
    if cap <= 0:
        return math.inf
    t0 = e.length / (max(5.0, e.speed_kmh) / 3.6)
    return t0 * (1 + 0.15 * min(max_vc, flow_vph / cap) ** 4)


@dataclass
class TrafficPhase:
    name: str
    window_min: float
    vehicles: Dict[int, float] = field(default_factory=dict)  # edge -> event vehicles over the phase
    by_dest: Dict[str, float] = field(default_factory=dict)

    def event_vph(self, eid: int) -> float:
        return 2.0 * self.vehicles.get(eid, 0.0) / (self.window_min / 60.0)


def background_vph(e) -> float:
    return e.background_vc * e.vehicle_capacity_vph()


def entry_points(net: StreetNetwork, radius: float) -> List[Tuple[int, float]]:
    """Every main road crossing the edge of the area (one point per road crossing), weighted by its
    capacity: event traffic comes from and goes to all of them, not one."""
    cands = []
    for n, (x, y) in net.nodes.items():
        r = math.hypot(x, y)
        if r < 0.9 * radius or r > 1.1 * radius or not net.drive_adj.get(n):
            continue
        es = [net.edges[eid] for _, eid in net.drive_adj[n]]
        if max(e.rank for e in es) < 3:
            continue
        cands.append((max(e.vehicle_capacity_vph() for e in es), n))
    cands.sort(reverse=True)
    out: List[Tuple[int, float]] = []
    for cap, n in cands:
        x, y = net.nodes[n]
        if all(math.hypot(x - net.nodes[m][0], y - net.nodes[m][1]) > 200 for m, _ in out):
            out.append((n, cap))
    return out


def choose_pickups(
    net: StreetNetwork, accesses: List[Access], crowd_edges: Set[int], car_people: int, radius: float
) -> List[Destination]:
    """Pick-up/drop-off points: on a street at least 'tertiary', 250-800 m walk from an exit, not on
    a street the crowd walks along, and at least 250 m from each other."""
    exits = [a for a in accesses if a.kind in ("exit", "both", "entry") and a.capacity > 0]
    if not exits or car_people <= 0:
        return []
    dist: Dict[int, float] = {}
    for a in exits:
        d, _ = net.shortest_paths(a.node, lambda e: e.length)
        for n, v in d.items():
            dist[n] = min(dist.get(n, math.inf), v)
    crowd_nodes = set()
    for eid in crowd_edges:
        crowd_nodes.add(net.edges[eid].u)
        crowd_nodes.add(net.edges[eid].v)
    cands = []
    for n, d in dist.items():
        if not 250 <= d <= 800 or n in crowd_nodes or not net.drive_adj.get(n):
            continue
        es = [net.edges[eid] for _, eid in net.drive_adj[n]]
        rank = max(e.rank for e in es)
        if rank < 3 or rank > 5:  # not lanes, and not on a highway
            continue
        cands.append((-(rank * 1000) + d, n))
    cands.sort()
    want = max(1, min(4, math.ceil(car_people / 4000)))
    chosen: List[int] = []
    for _, n in cands:
        x, y = net.nodes[n]
        if all(math.hypot(x - net.nodes[m][0], y - net.nodes[m][1]) >= 250 for m in chosen):
            chosen.append(n)
        if len(chosen) == want:
            break
    out = []
    for i, n in enumerate(chosen):
        x, y = net.nodes[n]
        street = net.street_at(x, y)
        out.append(
            Destination(
                f"pickup-{i + 1}",
                "car",
                f"Pick-up point {i + 1}" + (f" ({street})" if street else ""),
                "pickup",
                x,
                y,
                [m for m in [net.nearest_node(x, y, "walk", 60)] if m is not None] or [n],
            )
        )
        out[-1].drive_node = n  # type: ignore[attr-defined]
    return out


def vehicle_destinations(net: StreetNetwork, dests: List[Destination]) -> List[Tuple[Destination, int]]:
    """Drive-network node for each car-mode destination (car park or pick-up point)."""
    out = []
    for d in dests:
        if d.mode != "car":
            continue
        node = getattr(d, "drive_node", None) or net.nearest_node(d.x, d.y, "drive", 300)
        if node is not None:
            out.append((d, node))
    return out


def assign_vehicles(
    net: StreetNetwork,
    ped_phase: Phase,
    car_dests: List[Tuple[Destination, int]],
    entries: List[Tuple[int, float]],
    phase_name: str,
    window_min: float,
    people_per_vehicle: float = PEOPLE_PER_VEHICLE,
) -> Tuple[TrafficPhase, List[str]]:
    """Route event vehicles between the area's entry roads and their car park or pick-up point."""
    tp = TrafficPhase(phase_name, window_min)
    notes = []
    if not car_dests or not entries:
        return tp, notes
    total_cap = sum(c for _, c in entries)
    flows = []
    for d, node in car_dests:
        veh = ped_phase.by_dest.get(d.id, 0.0) / people_per_vehicle
        tp.by_dest[d.id] = veh
        for en, cap in entries:
            flows.append((node, en, veh * cap / total_cap))

    def cost(e) -> float:
        return bpr_time(e, background_vph(e) + tp.event_vph(e.id))

    unreachable = 0
    for _ in range(INCREMENTS):
        # Departure: from the car park/pick-up out to the entry roads; arrival: the reverse.
        for node in {f[0] for f in flows}:
            dist, prev = net.shortest_paths(node, cost, "drive", reverse=(phase_name == "arrival"))
            for src, en, veh in flows:
                if src != node or veh <= 0:
                    continue
                if dist.get(en, math.inf) == math.inf:
                    unreachable += 1
                    continue
                for eid in net.path_edges(prev, en):
                    tp.vehicles[eid] = tp.vehicles.get(eid, 0.0) + veh / INCREMENTS
    if unreachable:
        notes.append(
            "Some car parks or pick-up points have no drivable route to a main road on the map "
            "(one-way streets or missing links); their traffic is left out."
        )
    return tp, notes


def road_report(net: StreetNetwork, tp: TrafficPhase) -> List[dict]:
    """Per road: the busiest segment's volume against capacity, with and without the event."""
    worst: Dict[int, dict] = {}
    for eid, veh in tp.vehicles.items():
        e = net.edges[eid]
        cap = e.vehicle_capacity_vph()
        if cap <= 0 or veh < 1:
            continue
        bg = background_vph(e)
        ev = tp.event_vph(eid)
        vc = (bg + ev) / cap
        cur = worst.get(e.way)
        if cur is None or vc > cur["vc"]:
            x, y = net.edge_mid(eid)
            worst[e.way] = {
                "way": e.way,
                "edge": eid,
                "street": net.describe(eid),
                "highway": e.highway,
                "phase": tp.name,
                "x": x,
                "y": y,
                "capacity_vph": round(cap),
                "background_vph": round(bg),
                "event_vph": round(ev),
                "vc": round(vc, 2),
                "vc_without_event": round(bg / cap, 2),
                "event_vehicles": round(veh),
                # Minutes for this road to carry its event vehicles on top of normal traffic.
                "clear_min": round(60 * veh / max(1.0, cap - bg)),
            }
    return sorted(worst.values(), key=lambda r: -r["vc"])


def conflict_points(
    net: StreetNetwork, ped: Phase, tp: TrafficPhase, min_ppm: float = 40, min_vph: float = 500
) -> List[dict]:
    """Junctions where a big crowd flow meets a busy road: people will cross or spill onto the road."""
    out = []
    for n in net.nodes:
        walk = [(eid, ped.peak_event_ppm(eid)) for _, eid in net.walk_adj.get(n, ())]
        crowd = max((p for _, p in walk), default=0.0)
        if crowd < min_ppm:
            continue
        roads = []
        for _, eid in net.walk_adj.get(n, ()):
            e = net.edges[eid]
            if e.drivable and e.rank >= 3:
                roads.append((background_vph(e) + tp.event_vph(eid), e))
        if not roads:
            continue
        vph, road = max(roads, key=lambda t: t[0])
        if vph < min_vph:
            continue
        x, y = net.nodes[n]
        out.append(
            {
                "node": n,
                "x": x,
                "y": y,
                "street": net.describe(road.id),
                "crowd_ppm": round(crowd),
                "vehicles_vph": round(vph),
                "score": crowd * vph,
            }
        )
    out.sort(key=lambda c: -c["score"])
    merged: List[dict] = []
    for c in out:  # one post per junction cluster
        if all(math.hypot(c["x"] - m["x"], c["y"] - m["y"]) > 60 for m in merged):
            merged.append(c)
    return merged[:12]


def diversion(net: StreetNetwork, ends: Tuple[int, int], street: str, closed_length_m: float) -> dict:
    """The quickest drivable way between the two ends of a closed street, on the roads still open.
    Call it after the closures are applied, so a diversion never uses another closed street."""
    a, b = ends
    if a is None or b is None or a == b:
        return {"street": street, "edges": [], "via": [], "length_m": None, "extra_m": None}

    def cost(e) -> float:
        return bpr_time(e, background_vph(e))

    for s_, t_ in ((a, b), (b, a)):
        if not net.drive_adj.get(s_):
            continue
        dist, prev = net.shortest_paths(s_, cost, "drive")
        if dist.get(t_, math.inf) < math.inf:
            path = net.path_edges(prev, t_)
            length = sum(net.edges[eid].length for eid in path)
            return {
                "street": street,
                "edges": path,
                "via": [v for v in net.via(path) if v != street],
                "length_m": round(length),
                "extra_m": round(max(0.0, length - closed_length_m)),
            }
    return {"street": street, "edges": [], "via": [], "length_m": None, "extra_m": None}
