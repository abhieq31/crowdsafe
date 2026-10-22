"""Step 2: who goes where. A deployment plan with a reason for every post.

Placement follows the computed risks rather than a template:

- **Stewards** at every venue gate, scaled to the gate's peak flow, and at outside choke points.
- **Police** at gates of large events, at outside choke points that reach level of service E/F, and at
  busy stations (to hold and release people on the platforms' terms).
- **Traffic police** at conflict junctions (big crowd flow meets a busy road), at both ends of every
  road closure, and at pick-up points and car parks.
- **First-aid posts** placed by greedy maximum coverage: every part of the crowd, inside and on the
  outside routes, within ``MEDICAL_REACH_M`` of a post, with at least one post per 10,000 people.
- **Ambulances** staged at the ambulance gate at the end of the emergency corridor.

Staffing numbers use stated rules of thumb (below). They are starting points for the police, health and
organisers to adjust with local guidance, not legal minimums.
"""

from __future__ import annotations

import math
from typing import Dict, List, Tuple

from ..venue import Venue

MEDICAL_REACH_M = 150  # straight line; about 3 minutes for a responder moving through a crowd
PEOPLE_PER_POST = 10_000
PEOPLE_PER_AMBULANCE = 10_000
FIRST_AIDERS_PER_1000 = 2
FLOW_PER_STEWARD_PPM = 40  # people per minute one steward can guide through an exit
EVENT_POLICE_MIN = 2_000  # police at gates for events at least this big

AGENCY = {
    "Steward": "Organisers",
    "Police": "Police",
    "Traffic police": "Traffic police",
    "First-aid post": "Health",
    "Ambulance": "Health",
    "Station staff": "Transport operator",
    "Fire tender": "Fire service",
}
PRIORITY = {"F": 1, "E": 1, "D": 2}


def _item(
    role: str, count: int, x: float, y: float, place: str, why: str, when: str, priority: int = 2, kind: str = ""
) -> dict:
    return {
        "role": role,
        "agency": AGENCY.get(role, "Organisers"),
        "count": int(count),
        "x": x,
        "y": y,
        "place": place,
        "why": why,
        "when": when,
        "priority": priority,
        "kind": kind or role.lower(),
    }


def _inside_points(venue: Venue, attendance: int) -> List[Tuple[float, float, float]]:
    pts = []
    total = venue.total_area or 1.0
    for z in venue.zones.values():
        if len(z.polygon) < 3:
            continue
        xs = [p[0] for p in z.polygon]
        ys = [p[1] for p in z.polygon]
        step = max(10.0, math.sqrt(max(1.0, z.usable_area) / 60))
        inside = []
        y = min(ys) + step / 2
        while y < max(ys):
            x = min(xs) + step / 2
            while x < max(xs):
                if _in_poly(x, y, z.polygon):
                    inside.append((x, y))
                x += step
            y += step
        people = attendance * z.usable_area / total
        for x, y in inside:
            pts.append((x, y, people / len(inside)))
    return pts


def _in_poly(x: float, y: float, poly) -> bool:
    inside = False
    j = len(poly) - 1
    for i in range(len(poly)):
        xi, yi = poly[i]
        xj, yj = poly[j]
        if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / ((yj - yi) or 1e-12) + xi:
            inside = not inside
        j = i
    return inside


def medical_posts(
    venue: Venue,
    attendance: int,
    outside: List[Tuple[float, float, float]],
    extra_candidates: List[Tuple[float, float, str]],
) -> List[Tuple[float, float, str, float]]:
    """Greedy maximum coverage. Returns (x, y, label, people covered) per post."""
    demand = _inside_points(venue, attendance) + outside
    if not demand:
        return []
    cands = [(x, y, "inside the venue") for x, y, _ in _inside_points(venue, attendance)[::3]] + extra_candidates
    need = max(1, math.ceil(attendance / PEOPLE_PER_POST))
    covered = [False] * len(demand)
    total = sum(w for _, _, w in demand)
    posts = []
    while cands:
        best, best_gain = None, 0.0
        for c in cands:
            gain = sum(
                w
                for k, (x, y, w) in enumerate(demand)
                if not covered[k] and math.hypot(x - c[0], y - c[1]) <= MEDICAL_REACH_M
            )
            if gain > best_gain:
                best, best_gain = c, gain
        if best is None:
            break
        for k, (x, y, _) in enumerate(demand):
            if math.hypot(x - best[0], y - best[1]) <= MEDICAL_REACH_M:
                covered[k] = True
        posts.append((best[0], best[1], best[2], best_gain))
        cands = [c for c in cands if math.hypot(c[0] - best[0], c[1] - best[1]) > MEDICAL_REACH_M]
        share = sum(w for k, (_, _, w) in enumerate(demand) if covered[k]) / total
        if share >= 0.95 and len(posts) >= need:
            break
    return posts


def deployment_plan(
    *,
    venue: Venue,
    attendance: int,
    accesses,
    departure,
    arrival,
    chokes: List[dict],
    transit: List[dict],
    conflicts: List[dict],
    closures: List[dict],
    pickups,
    car_parks: List[dict],
    amb: dict,
    fire: dict,
    windows: Dict[str, str],
) -> Tuple[List[dict], List[str]]:
    items: List[dict] = []
    rules = [
        f"Stewards: 1 per {FLOW_PER_STEWARD_PPM} people a minute through a gate at its busiest, at least 2 per gate.",
        f"First-aid posts: every part of the crowd within {MEDICAL_REACH_M} m, at least 1 per "
        f"{PEOPLE_PER_POST:,} people, with {FIRST_AIDERS_PER_1000} first aiders per 1,000 people in total.",
        f"Ambulances: at least 1 per {PEOPLE_PER_AMBULANCE:,} people, staged at the ambulance gate.",
        f"Police at the gates for events of {EVENT_POLICE_MIN:,} or more; at outside choke points of level E/F.",
    ]
    # Gates
    for a in accesses:
        if a.capacity <= 0 and a.kind != "emergency":
            continue
        out = departure.by_gate.get(a.gate_id, 0.0)
        inn = arrival.by_gate.get(a.gate_id, 0.0)
        peak = max(2 * out / departure.window_min, 2 * inn / arrival.window_min)
        if a.kind == "emergency":
            items.append(
                _item(
                    "Steward",
                    2,
                    a.x,
                    a.y,
                    a.gate_name,
                    "Emergency gate: keep it unlocked, clear on both sides, and staffed all the time.",
                    windows["whole"],
                    1,
                    "gate",
                )
            )
            continue
        stewards = max(2, math.ceil(peak / FLOW_PER_STEWARD_PPM))
        items.append(
            _item(
                "Steward",
                stewards,
                a.x,
                a.y,
                a.gate_name,
                f"About {peak:,.0f} people a minute pass here at the busiest moment "
                f"({round(out):,} leave, {round(inn):,} arrive through it).",
                windows["whole"],
                2,
                "gate",
            )
        )
        if attendance >= EVENT_POLICE_MIN:
            items.append(
                _item(
                    "Police",
                    1 if peak < 200 else 2,
                    a.x,
                    a.y,
                    a.gate_name,
                    "Visible police at the gate keep entry and exit orderly.",
                    windows["whole"],
                    3,
                    "gate",
                )
            )
    # Outside choke points
    for s in chokes:
        if s["los"] not in ("D", "E", "F"):
            continue
        sev = s["los"] in ("E", "F")
        items.append(
            _item(
                "Steward",
                4 if sev else 2,
                s["x"],
                s["y"],
                s["street"],
                f"Level of service {s['los']} during the {s['phase']}: {s['event_ppm'] + s['public_ppm']:,} "
                f"people a minute on a {s['walk_width_m']:g} m footpath ({s['load']:.0%} of capacity).",
                windows[s["phase"]],
                PRIORITY[s["los"]],
                "choke",
            )
        )
        if sev:
            items.append(
                _item(
                    "Police",
                    2,
                    s["x"],
                    s["y"],
                    s["street"],
                    "Overflowing footpath: police to stop pushing, hold people back and keep the road edge safe.",
                    windows[s["phase"]],
                    1,
                    "choke",
                )
            )
    # Stations and stops
    for t in transit:
        if t["kind"] == "pickup" or t["peak_15min"] < 1000:
            continue
        items.append(
            _item(
                "Station staff",
                2 if t["peak_15min"] < 3000 else 4,
                t["x"],
                t["y"],
                t["name"],
                f"About {t['peak_15min']:,} event-goers arrive in the busiest 15 minutes of the {t['phase']}. "
                "Ask the operator for extra services and for staff to hold people outside in batches "
                "rather than letting the platform overfill.",
                windows[t["phase"]],
                1 if t["peak_15min"] >= 3000 else 2,
                "transit",
            )
        )
        items.append(
            _item(
                "Police",
                2,
                t["x"],
                t["y"],
                t["name"],
                "Hold-and-release at the station entrance in step with the operator.",
                windows[t["phase"]],
                2,
                "transit",
            )
        )
    # Traffic
    for c in conflicts:
        items.append(
            _item(
                "Traffic police",
                2,
                c["x"],
                c["y"],
                c["street"],
                f"Junction where about {c['crowd_ppm']:,} people a minute meet {c['vehicles_vph']:,} vehicles "
                "an hour: control the crossing (hold traffic in turns) and keep people off the carriageway.",
                windows["departure"],
                1,
                "conflict",
            )
        )
    for cl in closures:
        for end, (x, y) in enumerate(cl["ends"]):
            items.append(
                _item(
                    "Traffic police",
                    2,
                    x,
                    y,
                    f"{cl['street']} ({'start' if end == 0 else 'end'} of closure)",
                    f"Road closed to traffic for the crowd. Turn vehicles onto the diversion via "
                    f"{', '.join(cl['diversion_via'][:3]) or 'local streets'}.",
                    cl["when"],
                    1,
                    "closure",
                )
            )
    for p in pickups:
        items.append(
            _item(
                "Traffic police",
                2,
                p.x,
                p.y,
                p.label,
                "Taxi and car pick-up and drop-off point: keep vehicles moving, one lane only, no "
                "stopping elsewhere near the exits.",
                windows["arrival_departure"],
                2,
                "pickup",
            )
        )
    for cp in car_parks:
        items.append(
            _item(
                "Traffic police",
                1,
                cp["x"],
                cp["y"],
                cp["name"],
                f"Car park entrance: about {cp['vehicles']:,} event vehicles. Close it when full and "
                "send drivers on instead of letting them queue on the road.",
                windows["arrival_departure"],
                3,
                "parking",
            )
        )
    # Health
    outside_demand = [(s["x"], s["y"], s["people"] * 0.2) for s in chokes if s["los"] in ("D", "E", "F")]
    gate_cands = [(a.x, a.y, a.gate_name) for a in accesses]
    choke_cands = [(s["x"], s["y"], s["street"]) for s in chokes if s["los"] in ("E", "F")]
    posts = medical_posts(venue, attendance, outside_demand, gate_cands + choke_cands)
    aiders = max(2, math.ceil(attendance / 1000 * FIRST_AIDERS_PER_1000))
    per_post = max(2, math.ceil(aiders / max(1, len(posts))))
    for x, y, label, covered in posts:
        items.append(
            _item(
                "First-aid post",
                per_post,
                x,
                y,
                label,
                f"Covers about {covered:,.0f} people within {MEDICAL_REACH_M} m. Staff: {per_post} first aiders, "
                "with triage tags and a casualty register.",
                windows["whole"],
                1,
                "medical",
            )
        )
    n_amb = max(1, math.ceil(attendance / PEOPLE_PER_AMBULANCE))
    if amb.get("found"):
        gx, gy = amb["gate_xy"]
        items.append(
            _item(
                "Ambulance",
                n_amb,
                gx,
                gy,
                f"Ambulance point at {amb['gate']}",
                f"End of the emergency route from {amb['hospital']} ({amb['drive_min']:g} min). Keep this "
                "route and gate free of crowds and parked vehicles.",
                windows["whole"],
                1,
                "ambulance",
            )
        )
    if fire and amb.get("gate_xy"):
        gx, gy = amb["gate_xy"]
        items.append(
            _item(
                "Fire tender",
                1 if attendance < 20000 else 2,
                gx,
                gy,
                "Next to the ambulance point",
                f"Nearest fire station: {fire['name']} ({fire['drive_min']:g} min with event traffic).",
                windows["whole"],
                3,
                "fire",
            )
        )
    items = merge_nearby(items)
    items.sort(key=lambda i: (i["priority"], i["agency"]))
    return items, rules


MERGE_M = 80


def merge_nearby(items: List[dict]) -> List[dict]:
    """One post per role per spot: posts of the same role closer than ``MERGE_M`` become one team."""
    out: List[dict] = []
    for i in sorted(items, key=lambda i: (i["priority"], -i["count"])):
        for o in out:
            if o["role"] == i["role"] and math.hypot(o["x"] - i["x"], o["y"] - i["y"]) < MERGE_M:
                o["count"] = max(o["count"], i["count"]) + (0 if o["kind"] == i["kind"] else min(2, i["count"]))
                o.setdefault("_places", [o["place"]])
                if i["place"] not in o["_places"]:
                    o["_places"].append(i["place"])
                if i["why"] not in o["why"] and o["why"].count(" Also: ") < 1:
                    o["why"] += " Also: " + i["why"]
                break
        else:
            out.append(dict(i))
    for o in out:
        places = o.pop("_places", None)
        if places and len(places) > 1:
            named = sorted(places, key=lambda p: p.startswith("unnamed"))  # a named street reads better
            o["place"] = named[0] + (f" (and {len(places) - 1} more spot nearby)" if len(places) == 2
                                     else f" (and {len(places) - 1} more spots nearby)")
    return out
