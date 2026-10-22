"""The event overview: the venue *and* its surroundings, in one plan for every agency.

``event_overview`` answers, for an event of a given size at a venue drawn on the map:

1. **Crowd flow outside** (arrival and departure): which streets, stations and stops overflow, when,
   and what fixes them. Recommended road closures are applied and the crowd re-routed, so the plan
   shows the situation *with* its own fixes.
2. **Vehicles**: event cars and taxis, the normal traffic, pick-up points, closures with diversions,
   and the junctions where crowd and traffic meet.
3. **Emergency access**: receiving hospitals with drive times under event traffic, and an ambulance
   route that avoids the crowd.
4. **Deployment**: where stewards, police, traffic police, first aid, ambulances and transport staff
   go, how many, when, and why.

It needs OpenStreetMap data from ``osm.overpass_query`` (Python fetches it with ``osm.fetch_osm``,
the browser fetches it itself). Standard library only, so it also runs in the browser.
"""

from __future__ import annotations

import math
from typing import Dict, List, Optional

from ..venue import Venue
from . import dispersal as D
from . import emergency as EM
from . import traffic as T
from .network import StreetNetwork
from .placement import deployment_plan

ORDER = {"pass": 0, "warn": 1, "fail": 2}


def parse_modes(text: str) -> Dict[str, float]:
    """'metro=30, bus=20, car=15, walk=35' -> shares (percentages or fractions, normalised later)."""
    out: Dict[str, float] = {}
    for part in (text or "").replace(";", ",").split(","):
        if not part.strip():
            continue
        k, _, v = part.partition("=")
        k = k.strip().lower()
        if k not in D.MODE_TARGETS:
            raise ValueError(f"unknown travel mode {k!r}; use {', '.join(sorted(D.MODE_TARGETS))}")
        out[k] = float(v.strip().rstrip("%"))
    return out


def default_arrival_minutes(attendance: int) -> float:
    """People arrive over a longer time than they leave: ~45 min for small events, 2.5 h at most."""
    return float(max(45, min(150, round(30 + attendance / 800))))


def default_departure_minutes(attendance: int) -> float:
    """How long the crowd takes to leave once the event ends: ~20 min for small events, up to an hour
    for a full stadium. Over-short windows overstate peaks; set it from experience at the venue."""
    return float(max(20, min(60, round(20 + attendance / 2000))))


def _windows(arrival: float, end: float, depart: float) -> Dict[str, str]:
    a = f"from 30 min before gates open until {arrival:g} min after"
    d = f"from 15 min before the end (minute {end:g}) until {end + depart:g} min after gates open"
    return {
        "arrival": a,
        "departure": d,
        "arrival_departure": f"{a}, and {d}",
        "whole": f"from 30 min before gates open until {end + depart + 15:g} min after",
    }


def _ll(net: StreetNetwork, x: float, y: float) -> List[float]:
    lat, lon = net.latlon(x, y)
    return [lat, lon]


def _line(net: StreetNetwork, edges: List[int]) -> List[List[List[float]]]:
    """GeoJSON MultiLineString coordinates ([lon, lat]) for a list of edges."""
    out = []
    for eid in edges:
        e = net.edges[eid]
        a, b = net.latlon(*net.nodes[e.u]), net.latlon(*net.nodes[e.v])
        out.append([[a[1], a[0]], [b[1], b[0]]])
    return out


def event_overview(
    venue_dict: dict,
    attendance: int,
    osm: dict,
    *,
    modes: Optional[Dict[str, float]] = None,
    arrival_minutes: Optional[float] = None,
    event_end_min: Optional[float] = None,
    departure_minutes: Optional[float] = None,
    public_level: float = 1.0,
    radius_m: float = 1500.0,
    overrides: Optional[Dict[str, dict]] = None,
    people_per_vehicle: float = T.PEOPLE_PER_VEHICLE,
    apply_fixes: bool = True,
) -> dict:
    venue = Venue.from_dict(venue_dict)
    if not venue.origin:
        raise ValueError(
            "The surroundings need the venue's location: draw it on the map (Plan an event → "
            "Find it on the map) or add an 'origin' [lat, lon] to the venue file."
        )
    attendance = int(attendance)
    arrival_minutes = max(5.0, float(arrival_minutes or default_arrival_minutes(attendance)))
    depart = float(departure_minutes or default_departure_minutes(attendance))
    end = float(event_end_min if event_end_min is not None else arrival_minutes + 120)
    modes = dict(modes or D.DEFAULT_MODES)
    total = sum(v for v in modes.values() if v > 0)
    if total <= 0:
        raise ValueError("travel modes must add up to more than 0")
    modes = {k: v / total for k, v in modes.items() if v > 0}

    net = StreetNetwork.from_overpass(osm, venue.origin, overrides)
    if not net.edges:
        raise ValueError("No streets found around the venue in the map data.")
    notes = list(net.notes)
    accesses, n = D.gate_access(venue, net)
    notes += n
    usable = [a for a in accesses if a.capacity > 0]
    if not usable:
        raise ValueError("The venue has no open gate to the outside, so nobody can arrive or leave.")
    free = {a.gate_id: net.shortest_paths(a.node, lambda e: e.length)[0] for a in usable}

    # ---- Step 1, pass 1: where does the crowd walk? (to place pick-up points away from it)
    car_share = modes.get("car", 0.0)
    pre_dests, _ = D.destinations(
        net, {k: v for k, v in modes.items() if k != "car"} or {"walk": 1.0}, radius_m, [], free
    )
    pre = D.assign(net, accesses, pre_dests, attendance, "departure", depart, public_level)
    crowd_edges = {eid for eid in pre.people if pre.peak_event_ppm(eid) > 0.3 * net.edges[eid].ped_capacity_ppm()}
    pickups = T.choose_pickups(net, accesses, crowd_edges, int(attendance * car_share), radius_m)

    dests, n = D.destinations(net, modes, radius_m, pickups, free)
    notes += n
    car_parks = _limit_car_parks(dests, attendance, people_per_vehicle, notes)

    # ---- Step 1: arrival and departure, before fixes
    arrival = D.assign(net, accesses, dests, attendance, "arrival", arrival_minutes, public_level)
    departure0 = D.assign(net, accesses, dests, attendance, "departure", depart, public_level)
    streets_arr = D.street_report(net, arrival, public_level)
    streets_dep0 = D.street_report(net, departure0, public_level)

    # Close the worst crowded streets near the venue to traffic, then route the crowd again.
    closures: List[dict] = []
    groups = _closure_groups(net, streets_dep0, free) if apply_fixes else []
    if groups:
        net.set_closed_to_traffic([w for g in groups for w in g["ways"]])
        divs = [T.diversion(net, _drive_ends(net, g), g["street"], g["length_m"]) for g in groups]
        departure = D.assign(net, accesses, dests, attendance, "departure", depart, public_level)
        for g, dv in zip(groups, divs):
            closures.append(
                {
                    "street": g["street"],
                    "ways": g["ways"],
                    "ends": g["ends"],
                    "length_m": g["length_m"],
                    "diversion_via": dv["via"] if dv else [],
                    "diversion_extra_m": dv["extra_m"] if dv else None,
                    "diversion_edges": dv["edges"] if dv else [],
                    "when": f"from minute {end - 15:g} (15 min before the end) until minute {end + depart:g}",
                }
            )
    else:
        departure = departure0
    streets_dep = D.street_report(net, departure, public_level)

    # ---- Step 3: vehicles
    entries = T.entry_points(net, radius_m)
    car_dests = T.vehicle_destinations(net, dests)
    tr_arr, n1 = T.assign_vehicles(net, arrival, car_dests, entries, "arrival", arrival_minutes, people_per_vehicle)
    tr_dep, n2 = T.assign_vehicles(net, departure, car_dests, entries, "departure", depart, people_per_vehicle)
    notes += sorted(set(n1 + n2))
    if car_dests and not entries:
        notes.append("No main road was found at the edge of the area, so event traffic is not modelled.")
    roads = sorted(T.road_report(net, tr_arr) + T.road_report(net, tr_dep), key=lambda r: -r["vc"])
    conflicts = T.conflict_points(net, departure, tr_dep)
    for cp in car_parks:
        cp["vehicles"] = round(tr_arr.by_dest.get(cp["id"], 0.0))

    # ---- Emergency access
    amb_gate = EM.ambulance_gate(net, accesses, departure)
    hosp, corridor, fire, police = [], {"found": False}, None, None
    if amb_gate:
        gate, gate_node = amb_gate
        hosp = EM.hospitals(net, gate_node, tr_dep)
        for h in hosp:  # the first hospital with a drivable route to the ambulance gate
            corridor = EM.corridor(net, h, gate, gate_node, departure, tr_dep, public_level)
            if corridor.get("found"):
                corridor["gate_xy"] = (gate.x, gate.y)
                break
        fire = EM.nearest_service(net, "fire", gate_node, tr_dep)
        police = EM.nearest_service(net, "police", gate_node, tr_dep)
    if not hosp:
        notes.append(
            "No hospital reachable by road was found on the map within the area. Name the receiving "
            "hospitals with the health department."
        )

    # ---- Step 2: deployment
    transit = [
        t for t in D.transit_report(arrival, dests) + D.transit_report(departure, dests) if t["kind"] != "pickup"
    ]
    for s in streets_dep + streets_arr + streets_dep0:
        s["street"] = net.describe(s["edge"])
    severe = [s for s in streets_dep + streets_arr if s["los"] in ("E", "F")]
    busy = [s for s in streets_dep + streets_arr if s["los"] == "D"][:10]  # level D: watch, not a crisis
    chokes = _dedupe(severe + busy)
    windows = _windows(arrival_minutes, end, depart)
    deployments, rules = deployment_plan(
        venue=venue,
        attendance=attendance,
        accesses=accesses,
        departure=departure,
        arrival=arrival,
        chokes=chokes,
        transit=transit,
        conflicts=conflicts,
        closures=closures,
        pickups=pickups,
        car_parks=car_parks,
        amb=corridor,
        fire=fire,
        windows=windows,
    )

    pickups_rep = pickup_report(net, pickups, tr_dep)
    checks = _checks(
        streets_arr, streets_dep0, streets_dep, closures, roads, conflicts, transit, hosp, corridor, depart, pickups_rep
    )
    level = max((c["level"] for c in checks), key=ORDER.get) if checks else "pass"
    for s in chokes:
        s["fixes"] = D.choke_fixes(s, depart if s["phase"] == "departure" else arrival_minutes)

    from .. import __version__

    result = {
        "version": __version__,
        "venue": venue.name,
        "attendance": attendance,
        "times": {"arrival_minutes": arrival_minutes, "event_end_min": end, "departure_minutes": depart},
        "modes": modes,
        "public_level": public_level,
        "radius_m": radius_m,
        "verdict": level,
        "headline": {
            "pass": "The surroundings can handle this event",
            "warn": "The surroundings need a plan: see the deployments and fixes",
            "fail": "The streets around the venue cannot handle this crowd as planned",
        }[level],
        "checks": sorted(checks, key=lambda c: -ORDER[c["level"]]),
        "gates": [
            {
                "id": a.gate_id,
                "name": a.gate_name,
                "kind": a.kind,
                "located": a.located,
                "snap_m": round(a.snap_m),
                "leaving": round(departure.by_gate.get(a.gate_id, 0.0)),
                "arriving": round(arrival.by_gate.get(a.gate_id, 0.0)),
            }
            for a in accesses
        ],
        "choke_points": [_pub(net, s) for s in chokes],
        "streets": {
            "arrival": [_pub(net, s) for s in streets_arr[:40]],
            "departure_before_fixes": [_pub(net, s) for s in streets_dep0[:40]],
            "departure": [_pub(net, s) for s in streets_dep[:40]],
        },
        "transit": [_pub(net, t) for t in transit],
        "traffic": {
            "event_vehicles": round(attendance * car_share / people_per_vehicle),
            "people_per_vehicle": people_per_vehicle,
            "pickup_points": [_pub(net, p) for p in pickups_rep],
            "car_parks": [_pub(net, c) for c in car_parks],
            "roads": [_pub(net, r) for r in roads[:30]],
            "closures": [
                {
                    k: v
                    for k, v in dict(c, ends=[_ll(net, *e) for e in c["ends"]]).items()
                    if k not in ("diversion_edges", "ways")
                }
                for c in closures
            ],
            "conflict_points": [_pub(net, c) for c in conflicts],
        },
        "emergency": {
            "hospitals": [_pub(net, h) for h in hosp],
            "corridor": {k: v for k, v in corridor.items() if k not in ("edges", "gate_xy")},
            "fire_station": _pub(net, fire) if fire else None,
            "police_station": _pub(net, police) if police else None,
        },
        "deployments": [_pub(net, d) for d in deployments],
        "totals": _totals(deployments),
        "rules_of_thumb": rules,
        "notes": notes,
        "data": {
            "source": "OpenStreetMap contributors (ODbL)",
            "streets": len({e.way for e in net.edges}),
            "segments": len(net.edges),
            "places": len(net.pois),
        },
    }
    result["geojson"] = _geojson(
        net,
        streets_dep,
        streets_arr,
        closures,
        corridor,
        deployments,
        hosp,
        transit,
        pickups,
        departure,
        arrival,
        venue,
    )
    return result


MAX_CLOSURES = 8
CLOSE_WITHIN_M = 800  # walking distance from the nearest gate


def _closure_groups(net: StreetNetwork, streets: List[dict], free: Dict[str, Dict[int, float]]) -> List[dict]:
    """Streets worth closing to traffic: overflowing (level E/F), drivable, not a motorway or trunk road,
    and near the venue. Segments of one named street are closed together; at most ``MAX_CLOSURES``."""
    groups: Dict[str, dict] = {}
    for s in streets:
        if s["los"] not in ("E", "F") or not s["drivable"] or s["road_width_m"] <= 0:
            continue
        e = net.edges[s["edge"]]
        if e.rank > 5:
            continue
        near = min((d.get(e.u, math.inf) for d in free.values()), default=math.inf)
        if near > CLOSE_WITHIN_M:
            continue
        key = net.street_name(s["edge"]) or f"way {e.way}"
        g = groups.setdefault(key, {"street": net.describe(s["edge"]), "ways": [], "score": 0.0})
        g["ways"].append(s["way"])
        g["score"] += (s["load"] - 0.8) * sum(x.length for x in net.edges if x.way == s["way"])
    chosen = sorted(groups.values(), key=lambda g: -g["score"])[:MAX_CLOSURES]
    for g in chosen:
        es = [e for e in net.edges if e.way in g["ways"]]
        pts = [net.nodes[n] for e in es for n in (e.u, e.v)]
        a, b = max(
            ((p, q) for p in pts for q in pts), key=lambda pq: math.hypot(pq[0][0] - pq[1][0], pq[0][1] - pq[1][1])
        )
        g["ends"] = [a, b]
        g["length_m"] = round(sum(e.length for e in es))
        g["end_nodes"] = [n for n in (net.nearest_node(*a, "drive", 400), net.nearest_node(*b, "drive", 400))]
    return chosen


def _drive_core(net: StreetNetwork) -> set:
    """Junctions from which a car can reach the rest of the road network and come back (the largest
    strongly connected part). Closures leave small islands of streets; diversions must not start there."""
    # Seed on decent roads at several distances: roads at the map's edge are cut off and lead nowhere,
    # and right next to the venue the closures leave islands, so either alone can give a tiny "core".
    cands = [n for n in net.nodes if net.drive_adj.get(n) and max(net.edges[e].rank for _, e in net.drive_adj[n]) >= 3]
    starts = []
    for r in (300, 600, 900, 1200, 1800):
        if cands:
            starts.append(min(cands, key=lambda n: abs(math.hypot(*net.nodes[n]) - r)))
    best: set = set()
    for s in starts:
        fwd, _ = net.shortest_paths(s, lambda e: e.length, "drive")
        rev, _ = net.shortest_paths(s, lambda e: e.length, "drive", reverse=True)
        core = set(fwd) & set(rev)
        if len(core) > len(best):
            best = core
        if len(best) > 0.5 * len(net.nodes):
            break
    return best


def _drive_ends(net: StreetNetwork, g: dict):
    """Where traffic is turned away at each end of a closed street: the nearest junction on a road of at
    least 'tertiary' class that is still open (looked up after all closures are applied)."""
    core = _drive_core(net)

    def main_road_node(x: float, y: float):
        best, best_d = None, 800.0
        for n, (nx, ny) in net.nodes.items():
            d = math.hypot(nx - x, ny - y)
            if d >= best_d or n not in core:
                continue
            if max(net.edges[eid].rank for _, eid in net.drive_adj[n]) >= 3:
                best, best_d = n, d
        return best

    a, b = g["ends"]
    return (main_road_node(*a), main_road_node(*b))


def _limit_car_parks(dests: List[D.Destination], attendance: int, ppv: float, notes: List[str]) -> List[dict]:
    """Car parks hold only so many cars. Move the overflow to pick-up points (or say it has nowhere to go)."""
    parks = [d for d in dests if d.kind == "parking"]
    pickups = [d for d in dests if d.kind == "pickup"]
    overflow = 0.0
    out = []
    for d in parks:
        spaces = d.poi.tags.get("capacity") if d.poi else None
        try:
            spaces = int(spaces)
            known = True
        except (TypeError, ValueError):
            spaces, known = T.PARKING_DEFAULT_SPACES, False
        max_w = spaces * ppv / max(1, attendance)
        if d.weight > max_w:
            overflow += d.weight - max_w
            d.weight = max_w
        out.append({"id": d.id, "name": d.label, "x": d.x, "y": d.y, "spaces": spaces, "spaces_known": known})
    if overflow > 0:
        if pickups:
            for p in pickups:
                p.weight += overflow / len(pickups)
        else:
            notes.append(
                f"The car parks found hold fewer cars than expected (about {overflow * attendance:,.0f} "
                "people). Arrange extra parking or shuttle buses."
            )
    if parks and not any(c["spaces_known"] for c in out):
        notes.append(f"Car park sizes are not on the map; each is assumed to hold {T.PARKING_DEFAULT_SPACES} cars.")
    return out


def _dedupe(items: List[dict]) -> List[dict]:
    out: List[dict] = []
    for s in sorted(items, key=lambda s: -s["load"]):
        if all(s["way"] != o["way"] for o in out):
            out.append(s)
    return out


PICKUP_BAYS = 6  # vehicles that can stop and load at once, along one kerb
PICKUP_VPH_PER_BAY = 40  # a stop takes about 90 seconds: find the passenger, load, leave


def _dur(minutes: float) -> str:
    return f"{minutes:.0f} min" if minutes < 120 else f"{minutes / 60:.1f} hours"


def pickup_report(net: StreetNetwork, pickups, tr: "T.TrafficPhase") -> List[dict]:
    out = []
    for p in pickups:
        veh = tr.by_dest.get(f"car:{p.id}", 0.0)
        out.append(
            {
                "name": p.label,
                "x": p.x,
                "y": p.y,
                "vehicles": round(veh),
                "clear_min": round(60 * veh / (PICKUP_BAYS * PICKUP_VPH_PER_BAY)),
            }
        )
    return out


def _checks(arr, dep0, dep, closures, roads, conflicts, transit, hosp, corridor, depart, pickups_rep) -> List[dict]:
    c: List[dict] = []
    bad0 = [s for s in dep0 if s["los"] in ("E", "F")]
    bad = [s for s in dep if s["los"] in ("E", "F")]
    if not bad0:
        c.append(
            {
                "level": "pass",
                "title": "Streets can take the crowd leaving",
                "detail": "No street outside the venue goes beyond level of service D when the event ends.",
            }
        )
    elif not bad:
        c.append(
            {
                "level": "warn",
                "title": f"{len(bad0)} street(s) overflow when the event ends, unless closed to traffic",
                "detail": "With the road closures in this plan, the crowd fits: "
                + ", ".join(cl["street"] for cl in closures[:5])
                + ".",
            }
        )
    else:
        worst = bad[0]
        c.append(
            {
                "level": "fail",
                "title": f"{len(bad)} street(s) still overflow when the event ends",
                "detail": f"Worst: {worst['street']} at {worst['load']:.0%} of its footpath capacity, even after the "
                "closures. Stagger the exit, open other routes, or reduce the crowd.",
            }
        )
    bad_arr = [s for s in arr if s["los"] in ("E", "F")]
    if bad_arr:
        c.append(
            {
                "level": "warn",
                "title": f"{len(bad_arr)} street(s) overflow as people arrive",
                "detail": f"Worst: {bad_arr[0]['street']} ({bad_arr[0]['load']:.0%}). Open gates earlier or "
                "spread arrivals (timed entry).",
            }
        )
    hot = [t for t in transit if t["kind"] != "pickup" and t["peak_15min"] >= 3000]
    if hot:
        t = hot[0]
        c.append(
            {
                "level": "warn",
                "title": "Stations and stops need crowd control",
                "detail": f"{t['name']}: about {t['peak_15min']:,} event-goers in the busiest 15 minutes of the "
                f"{t['phase']}. Agree extra services and hold-and-release with the operator.",
            }
        )
    jam = [
        r
        for r in roads
        if r["vc"] >= 1.0
        and r["vc_without_event"] < 1.0
        and r["highway"] not in ("service", "residential", "living_street", "unclassified", "track")
    ]
    named = {r["street"] for r in jam}
    if jam:
        r = jam[0]
        c.append(
            {
                "level": "warn",
                "title": f"Event traffic overloads {len(named)} road(s)",
                "detail": f"{r['street']} goes from {r['vc_without_event']:.0%} to {r['vc']:.0%} of capacity in the "
                f"{r['phase']}. Use park-and-ride or shuttle buses, spread pick-ups, and stagger the end.",
            }
        )
    slow = [p for p in pickups_rep if p["clear_min"] > 60]
    if slow:
        p = max(slow, key=lambda p: p["clear_min"])
        veh = sum(q["vehicles"] for q in pickups_rep)
        need = math.ceil(veh / (PICKUP_BAYS * PICKUP_VPH_PER_BAY))
        c.append(
            {
                "level": "warn",
                "title": "Taxi and car pick-up cannot keep up",
                "detail": f"About {veh:,} cars and taxis use {len(pickups_rep)} pick-up point(s); "
                f"{p['name']} alone needs "
                f"about {_dur(p['clear_min'])} ({PICKUP_BAYS} loading bays, {PICKUP_VPH_PER_BAY} vehicles an hour "
                f"each). Clearing them in an hour needs about {need} such points: plan shuttle buses, "
                "park-and-ride or more public transport instead.",
            }
        )
    if conflicts:
        c.append(
            {
                "level": "warn",
                "title": f"{len(conflicts)} junction(s) where the crowd meets busy traffic",
                "detail": f"Busiest: {conflicts[0]['street']}. Traffic police must control these crossings.",
            }
        )
    if not hosp:
        c.append(
            {
                "level": "fail",
                "title": "No receiving hospital identified",
                "detail": "Agree receiving hospitals and ambulance routes with the health department.",
            }
        )
    elif corridor.get("found"):
        shared = sum(s["length_m"] for s in corridor["shared_with_crowd"])
        lvl = "pass" if shared == 0 else "warn"
        c.append(
            {
                "level": lvl,
                "title": f"Ambulance route: {corridor['hospital']}, {corridor['drive_min']:g} min",
                "detail": (
                    "The route to " + corridor["gate"] + " avoids the crowd."
                    if shared == 0
                    else f"It shares {shared:,} m with the departing crowd ("
                    + ", ".join(s["street"] for s in corridor["shared_with_crowd"][:3])
                    + "). Keep one lane coned off there."
                ),
            }
        )
    else:
        c.append(
            {
                "level": "warn",
                "title": "No ambulance route found on the map",
                "detail": "No drivable route from the hospitals to an ambulance gate was found (one-way streets or "
                "missing links in OpenStreetMap). Plan the ambulance route with the traffic police and keep it clear.",
            }
        )
    return c


def _pub(net: StreetNetwork, d: Optional[dict]) -> Optional[dict]:
    """Replace local x, y with lat/lon and drop internal ids."""
    if d is None:
        return None
    out = {k: v for k, v in d.items() if k not in ("x", "y", "edge", "node", "score")}
    if "x" in d:
        out["lat_lon"] = _ll(net, d["x"], d["y"])
    return out


def _totals(items: List[dict]) -> List[dict]:
    t: Dict[str, dict] = {}
    for i in items:
        k = i["role"]
        cur = t.setdefault(k, {"role": k, "agency": i["agency"], "count": 0, "posts": 0})
        cur["count"] += i["count"]
        cur["posts"] += 1
    return sorted(t.values(), key=lambda x: x["agency"])


def _geojson(
    net, streets_dep, streets_arr, closures, corridor, deployments, hosp, transit, pickups, departure, arrival, venue
) -> dict:
    feats = []
    for z in venue.zones.values():
        if len(z.polygon) >= 3:
            ring = [[ll[1], ll[0]] for ll in (net.latlon(x, y) for x, y in list(z.polygon) + [z.polygon[0]])]
            feats.append(
                {
                    "type": "Feature",
                    "geometry": {"type": "Polygon", "coordinates": [ring]},
                    "properties": {"layer": "venue", "name": z.name},
                }
            )

    def pt(x, y, **props):
        lat, lon = net.latlon(x, y)
        feats.append({"type": "Feature", "geometry": {"type": "Point", "coordinates": [lon, lat]}, "properties": props})

    def lines(edges, **props):
        if edges:
            feats.append(
                {
                    "type": "Feature",
                    "geometry": {"type": "MultiLineString", "coordinates": _line(net, edges)},
                    "properties": props,
                }
            )

    for phase, ph in (("departure", departure), ("arrival", arrival)):
        by_los: Dict[str, List[int]] = {}
        for eid in ph.people:
            e = net.edges[eid]
            total = ph.peak_event_ppm(eid) + D.public_ppm(e)
            g = D.los(total / e.effective_walk_width())
            if ph.people[eid] >= 20:
                by_los.setdefault(g, []).append(eid)
        for g, es in by_los.items():
            lines(es, layer=f"crowd_{phase}", los=g)
    for cl in closures:
        lines([e.id for e in net.edges if e.way in cl["ways"]], layer="closure", street=cl["street"])
        lines(cl.get("diversion_edges", []), layer="diversion", street=cl["street"])
    if corridor.get("found"):
        lines(corridor["edges"], layer="ambulance_route", hospital=corridor["hospital"])
    for d in deployments:
        pt(
            d["x"],
            d["y"],
            layer="deployment",
            role=d["role"],
            agency=d["agency"],
            count=d["count"],
            place=d["place"],
            why=d["why"],
            when=d["when"],
            priority=d["priority"],
        )
    for h in hosp:
        pt(h["x"], h["y"], layer="hospital", name=h["name"], drive_min=h["drive_min"], suitability=h["suitability"])
    seen = set()
    for t in transit:
        if t["id"] in seen:
            continue
        seen.add(t["id"])
        pt(
            t["x"],
            t["y"],
            layer="transit",
            name=t["name"],
            kind=t["kind"],
            people=t["people"],
            peak_15min=t["peak_15min"],
            phase=t["phase"],
        )
    for p in pickups:
        pt(p.x, p.y, layer="pickup", name=p.label)
    return {"type": "FeatureCollection", "features": feats}
