"""One answer to one question: is this event safe for this many people, and what should we change?

``assess_event`` runs the checks an experienced crowd-safety planner would run, using the same engine
as the live control room, and turns them into a verdict with plain reasons and concrete fixes:

1. **Capacity**: can the ground hold the crowd, and can the exits drain it in 8 minutes with the
   largest exit blocked (UK Green Guide practice)?
2. **Normal arrival**: does any zone reach crush density as people arrive, with nobody acting?
3. **Surge**: free food, a celebrity or a rumour sends arrivals up 2.5x part-way through. Does the
   response playbook keep every zone below critical, and how many minutes of warning does it give?
4. **Evacuation**: everyone must leave at the peak. How long does it take?
5. **Layout**: a single exit, or a zone with no way out.

Standard library only; it runs in the browser too (Pyodide).
"""

from __future__ import annotations

import math
from typing import List, Tuple

from .planning import Exit, capacity_for_venue, compute_capacity
from .venue import OUTSIDE, Venue
from .venue_wizard import venue_problems
from .whatif import WhatIf, prepare_venue, simulate

EVAC_GOOD_MIN = 8.0  # planning target (Green Guide)
EVAC_BAD_MIN = 15.0
ORDER = {"pass": 0, "warn": 1, "fail": 2}


def _check(level: str, title: str, detail: str) -> dict:
    return {"level": level, "title": title, "detail": detail}


def extra_exit_width(venue: Venue, attendance: int) -> Tuple[float, int]:
    """Extra level exit width (metres, and in how many exits) for the safe capacity to reach ``attendance``.

    Safe capacity assumes the largest exit is blocked, so a new exit only helps up to the width of the
    largest one: wider than that and it becomes the one assumed blocked. So extra width is split into
    exits no wider than the current largest (or 6 m if there is none). Returns (0, 0) if no change is
    needed and (inf, 0) if the ground itself is too small.
    """
    cap = capacity_for_venue(venue)
    if cap.safe_capacity >= attendance:
        return 0.0, 0
    if cap.area_capacity < attendance:
        return float("inf"), 0
    exits = [Exit(g.width_m, g.stairs) for g in venue.gates.values() if OUTSIDE in (g.from_zone, g.to_zone)]
    widest = max((e.width_m for e in exits), default=6.0)
    for total in (x / 2 for x in range(1, 801)):  # up to 400 m in 0.5 m steps
        n = max(1, math.ceil(total / widest))
        new = [Exit(total / n) for _ in range(n)]
        if compute_capacity(venue.total_area, exits + new).safe_capacity >= attendance:
            return total, n
    return float("inf"), 0


def assess_event(venue_dict: dict, attendance: int, arrival_minutes: float = 60.0) -> dict:
    venue = Venue.from_dict(venue_dict)
    attendance = int(attendance)
    arrival_minutes = max(5.0, float(arrival_minutes))
    sim_venue, layout_notes = prepare_venue(venue)
    cap = capacity_for_venue(venue)
    checks: List[dict] = []
    fixes: List[str] = []

    # 1. Capacity
    if attendance > cap.safe_capacity:
        extra, n = extra_exit_width(venue, attendance)
        why = ("the exits cannot drain it in 8 minutes if the largest one is blocked"
               if "exit" in cap.limiting_factor else "the ground cannot hold it at a safe 2 people/m²")
        checks.append(_check("fail", f"Too many people: safe capacity is {cap.safe_capacity:,}",
                             f"{attendance:,} expected, but {why}."))
        fixes.append(f"Let in at most {cap.safe_capacity:,} people at a time (count them at the gates).")
        if extra == float("inf"):
            fixes.append(f"Or use a bigger ground: this one holds {cap.area_capacity:,} at 2 people/m².")
        else:
            split = f" ({n} exits of {extra / n:.1f} m)" if n > 1 else ""
            fixes.append(f"Or add about {extra:g} m of exit width{split}, on other sides of the venue.")
    elif attendance > 0.85 * cap.safe_capacity:
        checks.append(_check("warn", f"Close to the limit: safe capacity is {cap.safe_capacity:,}",
                             f"{attendance:,} expected leaves little margin if more come than planned."))
        fixes.append("Count people at every gate and stop entry at the safe capacity.")
    else:
        checks.append(_check("pass", f"Capacity is fine: safe for {cap.safe_capacity:,}",
                             f"{attendance:,} expected."))

    names = {z.id: z.name for z in venue.zones.values()}

    # 2. Normal arrival
    w = WhatIf(attendance=attendance, arrival_minutes=arrival_minutes, minutes=arrival_minutes + 20)
    normal = simulate(sim_venue, w, respond=False, with_alerts=False)
    crit = [s for s in normal["summary"] if s["critical_at_min"] is not None]
    red = [s for s in normal["summary"] if s["peak_density"] >= 5.5]  # the RED threshold
    if crit:
        s = min(crit, key=lambda s: s["critical_at_min"])
        checks.append(_check("fail", f"{s['zone_name']} becomes a crush as people arrive",
                             f"It reaches critical density (6.5 people/m²) at minute {s['critical_at_min']:.0f}, "
                             "with no surge at all."))
        fixes.append(f"Spread arrivals over a longer time, or give {s['zone_name']} wider ways in and out.")
    elif red:
        s = red[0]
        checks.append(_check("warn", f"{s['zone_name']} gets very crowded",
                             f"Peak {s['peak_density']:.1f} people/m² as people arrive: RED alert, but not a crush."))
    else:
        worst = max(normal["summary"], key=lambda s: s["peak_density"])
        checks.append(_check("pass", "Normal arrival is comfortable",
                             f"Busiest place: {worst['zone_name']} at {worst['peak_density']:.1f} people/m²."))

    # 3. Surge, with and without the playbook
    w = WhatIf(attendance=attendance, arrival_minutes=arrival_minutes, minutes=arrival_minutes + 20,
               surge_at_min=round(arrival_minutes / 3, 1))
    surge_none = simulate(sim_venue, w, respond=False)
    surge_resp = simulate(sim_venue, w, respond=True)
    sc = [s for s in surge_none["summary"] if s["critical_at_min"] is not None]
    if not sc:
        checks.append(_check("pass", "A sudden surge stays safe",
                             f"Even if arrivals jump 2.5x at minute {w.surge_at_min:g}, "
                             "no zone reaches crush density."))
    else:
        first = min(sc, key=lambda s: s["critical_at_min"])
        lead = first["warning_lead_min"]
        lead_txt = f" The RED alert comes {lead:g} min before." if lead else ""
        if surge_resp["reached_critical"]:
            zs = ", ".join(names[z] for z in surge_resp["reached_critical"])
            checks.append(_check("fail", "A surge would cause a crush even with the response plan",
                                 f"{first['zone_name']} reaches critical at minute "
                                 f"{first['critical_at_min']:.0f}.{lead_txt} With the playbook, {zs} still do."))
            fixes.append("Act earlier: hold people back upstream at ORANGE, not RED, and reduce attendance.")
        else:
            acts = surge_resp["actions"]
            how = ""
            if acts:
                a = acts[0]["action"]
                gate = f" ({a['gate_name']})" if a.get("gate_name") else ""
                how = f" First action: {acts[0]['result']}{gate}."
            checks.append(_check("warn", "A surge needs a fast response",
                                 f"If arrivals jump 2.5x at minute {w.surge_at_min:g}, {first['zone_name']} "
                                 f"reaches crush density at minute {first['critical_at_min']:.0f} unless operators "
                                 f"act.{lead_txt} "
                                 f"The response plan prevents it.{how}"))
            fixes.append("Rehearse the response: who closes which gate, and how fast your radios reach them.")

    # 4. Evacuation at the peak
    w = WhatIf(attendance=attendance, arrival_minutes=arrival_minutes, minutes=arrival_minutes + 45,
               evacuate_at_min=arrival_minutes)
    evac = simulate(sim_venue, w, respond=False, with_alerts=False)["evacuation"]
    t = evac["minutes_to_clear_99pct"]
    if t is None:
        checks.append(_check("fail", "Everyone cannot get out in time",
                             f"{evac['people_inside']:,} people inside at the peak did not get out within 45 minutes."))
        fixes.append("Add exits or widen them; open emergency exits for the whole event.")
    elif t > EVAC_BAD_MIN:
        checks.append(_check("fail", f"Evacuation takes {t:g} minutes",
                             f"{evac['people_inside']:,} people at the peak; aim for {EVAC_GOOD_MIN:g} minutes."))
        fixes.append("Widen exits or add more, so everyone can leave in about 8 minutes.")
    elif t > EVAC_GOOD_MIN:
        checks.append(_check("warn", f"Evacuation takes {t:g} minutes",
                             f"{evac['people_inside']:,} people at the peak; "
                             f"the planning target is {EVAC_GOOD_MIN:g} minutes."))
    else:
        checks.append(_check("pass", f"Everyone can get out in {t:g} minutes",
                             f"{evac['people_inside']:,} people at the peak."))

    # 5. Layout
    for p in venue_problems(venue) + [f for f in cap.findings if f.startswith("Single exit")]:
        checks.append(_check("warn", "Layout", p))
    two_way = [n for n in layout_notes if n.startswith("People leave")]
    if two_way:
        places = ", ".join(n.split(" the way they came")[0].replace("People leave ", "") for n in two_way)
        checks.append(_check("warn", "People must leave the way they came in",
                             f"{places}: entry and exit share the same openings. Two-way flow through one "
                             "opening is a known cause of crushes; separate exits are safer."))

    level = max((c["level"] for c in checks), key=ORDER.get)
    headline = {
        "pass": f"Safe for {attendance:,} people",
        "warn": f"Possible for {attendance:,} people, with precautions",
        "fail": f"Not safe for {attendance:,} people",
    }[level]
    seen = set()
    fixes = [f for f in fixes if not (f in seen or seen.add(f))]
    return {
        "venue": venue.name, "attendance": attendance, "arrival_minutes": arrival_minutes,
        "verdict": level, "headline": headline,
        "safe_capacity": cap.safe_capacity, "limiting_factor": cap.limiting_factor,
        "checks": sorted(checks, key=lambda c: -ORDER[c["level"]]), "fixes": fixes,
        "surge": {"no_response": surge_none, "with_response": surge_resp},
    }
