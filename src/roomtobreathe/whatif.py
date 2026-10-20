"""What-if studies on your own venue: what happens if this many people come, a surge starts or a gate
gets blocked, with and without the playbook response. Standard library only.

This runs the same engine as the live control room (fusion, alerts, playbooks) on the flow simulator,
so the warnings and the recommended actions are the ones operators would see on the day. It runs in
the browser too (Pyodide), from ``roomtobreathe whatif`` and from the server API.

Each scenario is run twice:

* **no response**: nobody acts, so you see where and when the venue fails;
* **with response**: an operator approves every action of each zone's first RED alert, so you see
  whether the playbook is enough and how much warning it gives.

The simulator is macroscopic (people per zone, flow through gates limited by width), so it answers
"where and roughly when", not "exactly who". Treat the times as estimates and rehearse your response.
"""

from __future__ import annotations

import copy
from dataclasses import asdict, dataclass, field
from typing import Dict, List, Optional, Tuple

from .engine import Engine
from .planning import capacity_for_venue
from .sim import Scenario
from .venue import OUTSIDE, Venue
from .venue_wizard import venue_problems


@dataclass
class WhatIf:
    attendance: int  # people arriving over the arrival window
    arrival_minutes: float = 60.0
    minutes: float = 90.0  # how long to simulate
    stay_minutes: Dict[str, float] = field(default_factory=dict)  # per zone; default below
    surge_at_min: Optional[float] = None  # e.g. free distribution or a celebrity: arrivals jump
    surge_multiplier: float = 2.5
    surge_zone: Optional[str] = None  # where people linger 3x longer during the surge
    blocked_gate: Optional[str] = None  # a gate whose flow drops (people stopping, an obstacle)
    blocked_at_min: float = 10.0
    blocked_to: float = 0.3  # remaining share of its normal flow
    evacuate_at_min: Optional[float] = None  # everyone tries to leave at once (emergency or event end)

    @classmethod
    def from_dict(cls, d: dict) -> "WhatIf":
        known = {k: v for k, v in d.items() if k in cls.__dataclass_fields__ and v not in ("", None)}
        return cls(**known)


DEFAULT_STAY_MIN = 10.0
FOCAL_STAY_MIN = 30.0  # the largest zone is assumed to be where people come to be
CRITICAL = 6.5
EVACUATE_STAY_S = 5.0  # in an evacuation nobody lingers: people head for the way out at once


def prepare_venue(venue: Venue) -> Tuple[Venue, List[str]]:
    """The simulator moves people one way through each gate, from ``from`` to ``to``. Where a zone has
    no one-way route out of the venue, people must leave the way they came: each gate on that route
    is split, half its width in each direction. Counterflow through one opening is a known crush
    hazard, so this is reported, not hidden."""
    v = copy.deepcopy(venue)
    notes = []

    def can_leave() -> set:
        ok, changed = {OUTSIDE}, True
        while changed:
            changed = False
            for g in v.gates.values():
                if g.to_zone in ok and g.from_zone not in ok:
                    ok.add(g.from_zone)
                    changed = True
        return ok

    while True:
        ok = can_leave()
        stuck = [z for z in v.zones if z not in ok]
        if not stuck:
            break
        # Open a way back through the gates that lead into a stuck zone from somewhere that can leave
        # (or from outside); repeat until every zone has a route out.
        added = False
        for g in list(v.gates.values()):
            if g.to_zone in stuck and g.from_zone in ok and f"{g.id}-out" not in v.gates:
                g.width_m /= 2
                back = copy.deepcopy(g)
                back.id, back.name = f"{g.id}-out", f"{g.name} (way out)"
                back.from_zone, back.to_zone = g.to_zone, g.from_zone
                v.gates[back.id] = back
                notes.append(f"People leave {venue.zones[g.to_zone].name} the way they came, through {g.name}: "
                             "its width is split between the two directions. Two-way flow through one opening "
                             "is dangerous; a separate exit is safer.")
                added = True
        if not added:
            names = ", ".join(venue.zones[z].name for z in stuck)
            notes.append(f"No route out of {names}: nobody can leave in the simulation.")
            break
    return v, notes


def build_scenario(venue: Venue, w: WhatIf) -> Scenario:
    focal = max(venue.zones.values(), key=lambda z: z.usable_area).id
    stay = {zid: float(w.stay_minutes.get(zid, FOCAL_STAY_MIN if zid == focal else DEFAULT_STAY_MIN)) * 60
            for zid in venue.zones}
    window_s = max(60.0, w.arrival_minutes * 60)
    events = []
    if w.surge_at_min is not None and w.surge_at_min * 60 < window_s:
        t = w.surge_at_min * 60
        z = w.surge_zone if w.surge_zone in venue.zones else focal
        events += [(t, "arrivals", {"multiplier": w.surge_multiplier}),
                   (t, "dwell", {"zone": z, "seconds": stay[z] * 3}),
                   (t, "trigger", {"name": "free_distribution", "zone": z, "active": True})]
    if w.blocked_gate in venue.gates:
        events.append((w.blocked_at_min * 60, "gate_factor", {"gate": w.blocked_gate, "factor": w.blocked_to}))
    events.append((window_s, "arrivals", {"multiplier": 0.0}))
    if w.evacuate_at_min is not None:
        t = w.evacuate_at_min * 60
        events.append((t, "arrivals", {"multiplier": 0.0}))
        events.append((t, "trigger", {"name": "event_end", "zone": "*", "active": True}))
        events += [(t, "dwell", {"zone": z, "seconds": EVACUATE_STAY_S}) for z in venue.zones]
    return Scenario(name="what-if", description="custom what-if", arrival_pps=w.attendance / window_s,
                    dwell_s=stay, events=events)


def simulate(venue: Venue, w: WhatIf, respond: bool, sample_s: float = 30.0,
             with_alerts: bool = True) -> dict:
    """Run one scenario. ``with_alerts=False`` runs only the crowd physics (no alert engine), about 4x faster,
    for questions that need densities and times but not what operators would see."""
    # A private copy: approved actions close gates on the engine's venue, and that must not leak into
    # the next simulation (a closed entry would leave the next run's venue empty).
    e = Engine(copy.deepcopy(venue))
    e.start_demo(build_scenario(venue, w))
    zones = list(venue.zones)
    series = {"t_min": [], **{z: [] for z in zones}}
    peaks = {z: [0.0, 0.0] for z in zones}  # density, minute
    alerts, actions, first, critical_at = [], [], {}, {}
    responded = set()
    steps = int(w.minutes * 60)
    evac_t = w.evacuate_at_min * 60 if w.evacuate_at_min is not None else None
    evac_start_occ = cleared_min = None
    for i in range(steps):
        if not with_alerts:
            e.sim.step()
        for al in (e.tick() if with_alerts else ()):
            t_min = round(e.sim.t / 60, 2)
            alerts.append({"t_min": t_min, "zone": al.zone_id, "zone_name": al.zone_name, "level": al.level,
                           "previous": al.previous, "reasons": al.reasons})
            first.setdefault((al.zone_id, al.level), t_min)
            if respond and al.level == "RED" and al.zone_id not in responded:
                responded.add(al.zone_id)
                for act in al.actions:
                    r = e.execute(act, by="what-if")
                    actions.append({"t_min": t_min, "zone": al.zone_id, "action": act, "result": r["result"]})
        for z in zones:
            d = e.sim.true_density(z)
            if d > peaks[z][0]:
                peaks[z] = [d, e.sim.t / 60]
            if d >= CRITICAL and z not in critical_at:
                critical_at[z] = round(e.sim.t / 60, 2)
        if evac_t is not None and e.sim.t >= evac_t and cleared_min is None:
            inside = sum(e.sim.occ.values())
            if evac_start_occ is None:
                evac_start_occ = inside
            elif inside <= max(1.0, 0.01 * evac_start_occ):  # 99% out
                cleared_min = round((e.sim.t - evac_t) / 60, 1)
        if i % int(sample_s) == 0:
            series["t_min"].append(round(e.sim.t / 60, 2))
            for z in zones:
                series[z].append(round(e.sim.true_density(z), 3))
    summary = []
    for z in zones:
        red = first.get((z, "RED"))
        orange = first.get((z, "ORANGE"))
        crit = critical_at.get(z)
        summary.append({
            "zone": z, "zone_name": venue.zones[z].name,
            "peak_density": round(peaks[z][0], 2), "peak_at_min": round(peaks[z][1], 1),
            "first_orange_min": orange, "first_red_min": red, "critical_at_min": crit,
            # how long operators had between the RED alert and critical density
            "warning_lead_min": round(crit - red, 1) if crit is not None and red is not None else None,
        })
    return {"series": series, "alerts": alerts, "actions": actions, "summary": summary,
            "reached_critical": sorted(critical_at), "outside_queue": round(e.sim.outside_queue),
            "evacuation": None if evac_t is None else {
                "people_inside": round(evac_start_occ or 0), "minutes_to_clear_99pct": cleared_min}}


def run_whatif(venue_dict: dict, params: dict) -> dict:
    """Entry point for the browser, the server and the CLI. Plain dicts in, plain dicts out."""
    venue = Venue.from_dict(venue_dict)
    w = WhatIf.from_dict(params)
    sim_venue, notes = prepare_venue(venue)
    cap = capacity_for_venue(venue)
    return {
        "venue": venue.name,
        "params": asdict(w),
        "safe_capacity": cap.safe_capacity,
        "limiting_factor": cap.limiting_factor,
        "findings": cap.findings + venue_problems(venue) + notes,
        "no_response": simulate(sim_venue, w, respond=False),
        "with_response": simulate(sim_venue, w, respond=True),
    }


def describe(result: dict) -> List[str]:
    """Plain-language conclusions, most important first."""
    out = []
    a, b = result["no_response"], result["with_response"]
    if not a["reached_critical"]:
        out.append("No zone reaches critical density (6.5 people/m²) even without any response.")
    for s in a["summary"]:
        if s["critical_at_min"] is not None:
            lead = (f", {s['warning_lead_min']:g} min after the RED alert" if s["warning_lead_min"] is not None
                    else "")
            out.append(f"Without action, {s['zone_name']} reaches critical density at minute "
                       f"{s['critical_at_min']:.0f}{lead}.")
    if a["reached_critical"]:
        if b["reached_critical"]:
            names = ", ".join(s["zone_name"] for s in b["summary"] if s["critical_at_min"] is not None)
            out.append(f"Even with the playbook, {names} still reach critical density: reduce attendance, "
                       "widen exits or act earlier (at ORANGE).")
        else:
            worst = max(b["summary"], key=lambda s: s["peak_density"])
            out.append(f"With the playbook, every zone stays below critical; the worst is {worst['zone_name']} at "
                       f"{worst['peak_density']:g} people/m².")
    ev = a.get("evacuation")
    if ev:
        if ev["minutes_to_clear_99pct"] is None:
            out.insert(0, f"Evacuation: {ev['people_inside']:,} people were inside and the venue did NOT clear "
                          "before the simulation ended. The exits are too narrow for this crowd.")
        else:
            out.insert(0, f"Evacuation: {ev['people_inside']:,} people take about {ev['minutes_to_clear_99pct']:g} "
                          "min to get out (99%). The planning target used here is 8 min (after the UK Green Guide).")
    if b["outside_queue"] > 0 and not ev:
        out.append(f"With the response, about {b['outside_queue']:,} people are still waiting outside at the "
                   "end: plan space and information for them, because a held-back crowd is still a crowd.")
    return out
