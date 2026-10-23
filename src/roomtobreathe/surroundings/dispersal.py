"""Step 1: how the crowd arrives and leaves through the streets around the venue.

People leave the venue's exits and walk to where they are going: metro and railway stations, bus
stops, ferry ghats, car parks and pick-up points, or simply home along the main streets. The model
routes each group over the real street network and adds up, street by street, how many people pass at
the busiest moment. Comparing that with the footpath's capacity gives the outside choke points: the
narrow lane to the metro that jams when the match ends.

Method (static traffic assignment adapted to pedestrians):

1. Demand: attendance x travel-mode share x destination choice (nearer stations get more people).
2. Exit choice: each exit's share of the venue's exit capacity, tilted towards exits nearer the
   destination (people head for the exit on their side).
3. Incremental assignment in 4 steps. Each step routes a quarter of the people on the quickest paths,
   with walking speed reduced on streets that are already busy, so crowds spread onto parallel routes
   the way real crowds do.
4. Peak flow: departures follow a triangle that starts at its peak when the event ends (2x the
   average rate), plus the normal public already on the street.
5. Comfort and safety by Fruin's walkway Levels of Service, in people per metre of width per minute:
   A <=23, B <=33, C <=49, D <=66, E <=82 (the design capacity), F above it.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from ..physics import weidmann_speed
from ..venue import OUTSIDE, Venue
from .network import Poi, StreetNetwork

LOS_LIMITS = [("A", 23), ("B", 33), ("C", 49), ("D", 66), ("E", 82)]
DEFAULT_MODES = {"metro": 0.25, "rail": 0.1, "bus": 0.25, "car": 0.2, "walk": 0.2}
MODE_TARGETS = {
    "metro": ("metro",),
    "rail": ("rail",),
    "tram": ("tram",),
    "ferry": ("ferry",),
    "bus": ("bus_station", "bus"),
    "car": ("parking", "pickup"),
    "walk": ("home",),
}
MODE_LABEL = {
    "metro": "metro",
    "rail": "train",
    "tram": "tram",
    "ferry": "ferry",
    "bus": "bus",
    "car": "car or taxi",
    "walk": "on foot",
}
# The normal public already walking there, in people per metre of footpath per minute (an ordinary
# city evening; ``public_level`` scales it: 0.5 quiet suburb, 2 market district).
PUBLIC_PPM_PER_M = {6: 0, 5: 10, 4: 8, 3: 6, 2: 3, 1: 2, 0: 1}


def public_ppm(e, level: float = 1.0) -> float:
    return PUBLIC_PPM_PER_M.get(e.rank, 1) * e.walk_width * level


MAX_WALK_M = 2500
INCREMENTS = 4


def los(ppm_per_m: float) -> str:
    for grade, limit in LOS_LIMITS:
        if ppm_per_m <= limit:
            return grade
    return "F"


def _speed_for_specific_flow(q: float) -> float:
    lo, hi = 0.0, 2.6  # density where density x speed peaks (~1.37 p/m/s)
    if q >= hi * weidmann_speed(hi):
        return 0.3
    for _ in range(30):
        mid = (lo + hi) / 2
        if mid * weidmann_speed(mid) < q:
            lo = mid
        else:
            hi = mid
    return max(0.3, weidmann_speed(hi))


_Q_STEP = 0.01
_SPEED_TABLE = [_speed_for_specific_flow(i * _Q_STEP) for i in range(141)]  # 0 .. 1.40 p/m/s


def walking_speed(flow_ppm: float, width_m: float) -> float:
    """Walking speed (m/s) on a footpath carrying ``flow_ppm`` people per minute: invert flow = density x
    speed x width on the free-flowing branch of Weidmann's diagram. Over capacity people queue."""
    q = flow_ppm / 60.0 / max(0.3, width_m) / _Q_STEP  # people per metre per second, in table steps
    i = int(q)
    if i >= len(_SPEED_TABLE) - 1:
        return 0.3
    return _SPEED_TABLE[i] + (_SPEED_TABLE[i + 1] - _SPEED_TABLE[i]) * (q - i)


@dataclass
class Access:
    """Where a venue gate meets the street network."""

    gate_id: str
    gate_name: str
    x: float
    y: float
    node: int
    snap_m: float
    capacity: float  # people per second through the gate
    kind: str  # "exit" | "entry" | "emergency"
    located: bool = True


@dataclass
class Destination:
    id: str
    mode: str
    label: str
    kind: str
    x: float
    y: float
    nodes: List[int]
    weight: float = 0.0
    poi: Optional[Poi] = None


@dataclass
class Phase:
    name: str  # "arrival" | "departure"
    window_min: float
    people: Dict[int, float] = field(default_factory=dict)  # edge id -> people over the whole phase
    by_dest: Dict[str, float] = field(default_factory=dict)
    by_gate: Dict[str, float] = field(default_factory=dict)
    paths: Dict[Tuple[str, str], List[int]] = field(default_factory=dict)  # (gate, dest) -> edges (last step)

    def peak_event_ppm(self, eid: int) -> float:
        return 2.0 * self.people.get(eid, 0.0) / self.window_min


def gate_access(venue: Venue, net: StreetNetwork) -> Tuple[List[Access], List[str]]:
    """Locate each venue gate that opens to the outside, and connect it to the nearest walkable street."""
    notes = []
    out = []
    for g in venue.gates.values():
        if OUTSIDE not in (g.from_zone, g.to_zone):
            continue
        inside = g.to_zone if g.from_zone == OUTSIDE else g.from_zone
        kind = "emergency" if not g.open else ("entry" if g.from_zone == OUTSIDE else "exit")
        located = len(g.line) >= 2
        if located:
            x = (g.line[0][0] + g.line[-1][0]) / 2
            y = (g.line[0][1] + g.line[-1][1]) / 2
        else:
            poly = venue.zones[inside].polygon
            x = sum(p[0] for p in poly) / len(poly) if poly else 0.0
            y = sum(p[1] for p in poly) / len(poly) if poly else 0.0
        node = net.nearest_node(x, y, "walk")
        if node is None:
            notes.append(f"{g.name}: no street found near it.")
            continue
        snap = math.hypot(net.nodes[node][0] - x, net.nodes[node][1] - y)
        if not located:
            notes.append(
                f"{g.name} has no position (it was typed, not drawn on the map), so its street "
                "connection is guessed. Draw the gate on the map for accurate routes."
            )
        elif snap > 120:
            notes.append(
                f"{g.name} is {snap:.0f} m from the nearest mapped street or path. If there is a path, "
                "it is missing from OpenStreetMap; routes from this gate are less certain."
            )
        out.append(Access(g.id, g.name, x, y, node, snap, g.capacity_pps if g.open else 0.0, kind, located))
    return out, notes


OTHER_WAY = 0.35  # how much an entry gate is used to leave (and an exit to enter): open gates get used both ways


def _home_sectors(net: StreetNetwork, radius: float) -> List[Destination]:
    """Walking home: people head out along the streets that leave the area, more along main roads."""
    best: Dict[int, Tuple[float, int]] = {}
    for n, (x, y) in net.nodes.items():
        r = math.hypot(x, y)
        if r < 0.8 * radius or r > 1.05 * radius or not net.walk_adj.get(n):
            continue
        rank = max(net.edges[e].rank for _, e in net.walk_adj[n])
        sector = int(((math.degrees(math.atan2(y, x)) + 360) % 360) // 45)
        score = rank + r / radius
        if sector not in best or score > best[sector][0]:
            best[sector] = (score, n)
    names = ["east", "north-east", "north", "north-west", "west", "south-west", "south", "south-east"]
    out = []
    for s, (score, n) in sorted(best.items()):
        x, y = net.nodes[n]
        street = net.street_at(x, y)
        label = f"Home, towards the {names[s]}" + (f" ({street})" if street else "")
        out.append(Destination(f"home-{s}", "walk", label, "home", x, y, [n], weight=max(0.5, score)))
    return out


def destinations(
    net: StreetNetwork,
    modes: Dict[str, float],
    radius: float,
    pickups: List[Destination],
    free_dist: Dict[str, Dict[int, float]],
) -> Tuple[List[Destination], List[str]]:
    """Choose where each travel mode's people go, and how many go to each place (nearer gets more)."""
    notes = []
    out: List[Destination] = []
    best_gate_dist = {}

    def walk_dist(nodes: List[int]) -> float:
        return min((d.get(n, math.inf) for d in free_dist.values() for n in nodes), default=math.inf)

    for mode, share in modes.items():
        if share <= 0:
            continue
        kinds = MODE_TARGETS.get(mode)
        if kinds is None:
            raise ValueError(f"unknown travel mode {mode!r}; use {sorted(MODE_TARGETS)}")
        cands: List[Destination] = []
        if "home" in kinds:
            cands = _home_sectors(net, radius)
        else:
            for p in net.pois:
                if p.kind not in kinds:
                    continue
                nodes = [n for n in (net.nearest_node(ax, ay, "walk", 250) for ax, ay in p.access) if n is not None]
                if nodes:
                    cands.append(Destination(f"{mode}:{p.id}", mode, p.label, p.kind, p.x, p.y, nodes, poi=p))
            if "pickup" in kinds:
                cands += [Destination(f"{mode}:{d.id}", mode, d.label, "pickup", d.x, d.y, d.nodes) for d in pickups]
        scored = []
        for c in cands:
            d = walk_dist(c.nodes)
            if d <= MAX_WALK_M:
                best_gate_dist[c.id] = d
                scored.append((d, c))
        scored.sort(key=lambda t: t[0])
        if mode == "bus":  # prefer bus stations over single stops, but use stops too
            scored.sort(key=lambda t: t[0] * (0.6 if t[1].kind == "bus_station" else 1.0))
        if mode == "car":
            scored = scored[:6]
        elif mode != "walk":
            scored = scored[:3]
        if not scored:
            notes.append(
                f"No {MODE_LABEL.get(mode, mode)} destination found within {MAX_WALK_M / 1000:g} km on "
                f"OpenStreetMap; those {share:.0%} are treated as walking home."
            )
            continue
        if mode == "walk":
            tot = sum(c.weight for _, c in scored)
            for _, c in scored:
                c.weight = share * c.weight / tot
        elif mode == "car":
            # Drivers are told where to park or be picked up, so spread them evenly, not to the nearest.
            for _, c in scored:
                c.weight = share / len(scored)
        else:
            inv = [1.0 / max(150.0, d) ** 2 for d, _ in scored]
            for w, (_, c) in zip(inv, scored):
                c.weight = share * w / sum(inv)
        out += [c for _, c in scored]
    lost = 1.0 - sum(d.weight for d in out)
    if lost > 1e-6:
        homes = [d for d in out if d.kind == "home"] or _home_sectors(net, radius)
        if not [d for d in out if d.kind == "home"]:
            out += homes
        tot = sum(1.0 for _ in homes)
        for h in homes:
            h.weight += lost / tot
    return out, notes


def assign(
    net: StreetNetwork,
    accesses: List[Access],
    dests: List[Destination],
    attendance: int,
    phase_name: str,
    window_min: float,
    public_level: float = 1.0,
) -> Phase:
    """Route everyone for one phase. Arrival uses entry gates, departure exit gates (walk graph is two-way)."""
    want = "entry" if phase_name == "arrival" else "exit"
    gates = [a for a in accesses if a.capacity > 0]
    phase = Phase(phase_name, window_min)
    if not gates or not dests:
        return phase
    pref = {a.gate_id: a.capacity * (1.0 if a.kind in (want, "both") else OTHER_WAY) for a in gates}
    cap_total = sum(pref.values())

    costs: List[float] = []

    def refresh() -> None:  # once per step, not once per edge visit
        costs[:] = [
            e.length / walking_speed(phase.peak_event_ppm(e.id) + public_ppm(e, public_level), e.effective_walk_width())
            for e in net.edges
        ]

    def cost(e) -> float:
        return costs[e.id]

    free = {a.gate_id: net.shortest_paths(a.node, lambda e: e.length)[0] for a in gates}
    split: Dict[Tuple[str, str], float] = {}
    for d in dests:
        dist = {a.gate_id: min(free[a.gate_id].get(n, math.inf) for n in d.nodes) for a in gates}
        dmin = min(dist.values())
        if dmin == math.inf:
            continue
        w = {a.gate_id: (pref[a.gate_id] / cap_total) * math.exp(-(dist[a.gate_id] - dmin) / 250.0) for a in gates}
        tot = sum(w.values())
        for gid, v in w.items():
            split[(gid, d.id)] = attendance * d.weight * v / tot
    by_id = {d.id: d for d in dests}
    for _ in range(INCREMENTS):
        refresh()
        for a in gates:
            pairs = [(did, n) for (gid, did), n in split.items() if gid == a.gate_id and n > 0]
            if not pairs:
                continue
            dist, prev = net.shortest_paths(a.node, cost)
            for did, n in pairs:
                target = min(by_id[did].nodes, key=lambda t: dist.get(t, math.inf))
                if dist.get(target, math.inf) == math.inf:
                    continue
                path = net.path_edges(prev, target)
                for eid in path:
                    phase.people[eid] = phase.people.get(eid, 0.0) + n / INCREMENTS
                phase.paths[(a.gate_id, did)] = path
                phase.by_dest[did] = phase.by_dest.get(did, 0.0) + n / INCREMENTS
                phase.by_gate[a.gate_id] = phase.by_gate.get(a.gate_id, 0.0) + n / INCREMENTS
    return phase


def street_report(net: StreetNetwork, phase: Phase, public_level: float = 1.0) -> List[dict]:
    """Per street (OSM way), the busiest segment at the phase's peak."""
    worst: Dict[int, dict] = {}
    for eid, people in phase.people.items():
        if people < 1:
            continue
        e = net.edges[eid]
        w = e.effective_walk_width()
        event = phase.peak_event_ppm(eid)
        public = public_ppm(e, public_level)
        total = event + public
        per_m = total / w
        cap = e.ped_capacity_ppm()
        cur = worst.get(e.way)
        if cur is None or per_m > cur["ppm_per_m"]:
            x, y = net.edge_mid(eid)
            worst[e.way] = {
                "way": e.way,
                "edge": eid,
                "street": net.describe(eid),
                "highway": e.highway,
                "phase": phase.name,
                "x": x,
                "y": y,
                "walk_width_m": round(w, 1),
                "width_estimated": e.estimated_width,
                "event_ppm": round(event),
                "public_ppm": round(public),
                "ppm_per_m": round(per_m, 1),
                "capacity_ppm": round(cap),
                "load": round(total / cap, 2),
                "los": los(per_m),
                "people": round(people),
                "drivable": e.drive_fwd or e.drive_back,
                "road_width_m": e.road_width,
                "closed_to_traffic": e.closed_to_traffic,
            }
    return sorted(worst.values(), key=lambda s: -s["load"])


def choke_fixes(s: dict, window_min: float) -> List[str]:
    """Concrete fixes for an outside choke point, computed from its numbers."""
    fixes = []
    target = 66 * s["walk_width_m"]  # top of level of service D
    room = target - s["public_ppm"]
    if s["event_ppm"] > 0 and room > 0:
        need = window_min * s["event_ppm"] / room
        if need > window_min:
            mins = f"about {math.ceil(need / 5) * 5:.0f} minutes instead of {window_min:g}"
            fixes.append(
                f"Spread arrivals over {mins}: open the gates earlier, timed entry or tickets with entry times."
                if s["phase"] == "arrival"
                else f"Stagger the exit: spread departures over {mins} (release stands or blocks in turn)."
            )
    if s["drivable"] and not s["closed_to_traffic"] and s["road_width_m"] > 0:
        new_cap = (s["walk_width_m"] + s["road_width_m"]) * 82
        new_load = (s["event_ppm"] + s["public_ppm"]) / new_cap
        if new_load < s["load"]:
            fixes.append(
                f"Close {s['street']} to traffic for the {s['phase']} so people can use the whole road "
                f"(its load would fall from {s['load']:.0%} to {new_load:.0%} of capacity)."
            )
    fixes.append(
        "Send part of the crowd another way: signs and marshals "
        + ("on the approach" if s["phase"] == "arrival" else "at the exits")
        + " pointing to other gates, stations or streets."
    )
    return fixes


def transit_report(phase: Phase, dests: List[Destination]) -> List[dict]:
    out = []
    for d in dests:
        n = phase.by_dest.get(d.id, 0.0)
        if n < 1 or d.kind == "home":
            continue
        peak15 = min(n, 2.0 * n / phase.window_min * 15)
        out.append(
            {
                "id": d.id,
                "mode": d.mode,
                "kind": d.kind,
                "name": d.label,
                "x": d.x,
                "y": d.y,
                "phase": phase.name,
                "people": round(n),
                "peak_15min": round(peak15),
            }
        )
    return sorted(out, key=lambda t: -t["people"])
