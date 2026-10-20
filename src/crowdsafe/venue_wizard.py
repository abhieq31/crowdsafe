"""``crowdsafe venue new``: describe a venue in plain questions and get a checked venue file.

No map or drawing is needed: a zone is a name and an area (or length x width
in metres), and a gate is a width between two zones. That is enough for the
capacity maths and the live monitor. Standard library only.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Callable, Dict, List, Optional

from .planning import capacity_for_venue
from .venue import OUTSIDE, Venue

Ask = Callable[[str], str]
TERRAIN = ("mud", "slope", "steps", "riverbank", "dead_end")


def slug(name: str, taken) -> str:
    base = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_") or "zone"
    s, i = base, 2
    while s in taken or s == OUTSIDE:
        s, i = f"{base}_{i}", i + 1
    return s


def parse_area(text: str) -> Optional[float]:
    """'960', '40 x 24' or '40*24' (metres) -> square metres."""
    t = text.lower().replace("m2", "").replace("m²", "").replace("m", "").strip()
    m = re.fullmatch(r"([\d.]+)\s*[x*×]\s*([\d.]+)", t)
    try:
        v = float(m.group(1)) * float(m.group(2)) if m else float(t)
    except (ValueError, AttributeError):
        return None
    return v if v > 0 else None


def venue_problems(venue: Venue) -> List[str]:
    """Structural problems that make the capacity numbers or the playbook meaningless."""
    out = []
    exits = venue.exits_to_outside()
    if not exits:
        out.append("No gate leads outside. Every venue needs at least one exit.")
    # Every zone must have a route to outside (people trapped in a dead end are the classic crush).
    reach = {OUTSIDE}
    changed = True
    while changed:
        changed = False
        for g in venue.gates.values():
            ends = {g.from_zone, g.to_zone}
            if ends & reach and not ends <= reach:  # in an emergency people use any opening
                reach |= ends
                changed = True
    trapped = [z.name for zid, z in venue.zones.items() if zid not in reach]
    if trapped:
        out.append("No route to outside from: " + ", ".join(trapped) + ". Add a gate.")
    return out


class _Wizard:
    def __init__(self, ask: Ask, log: Callable[[str], None]):
        self.ask, self.log = ask, log

    def q(self, question: str, default: Optional[str] = None) -> str:
        ans = self.ask(f"{question}{f' [{default}]' if default else ''}: ").strip()
        return ans or (default or "")

    def number(self, question: str, default: Optional[str] = None, allow_empty: bool = False,
               parse=None) -> Optional[float]:
        parse = parse or (lambda t: float(t) if float(t) > 0 else None)
        while True:
            t = self.q(question, default)
            if not t and allow_empty:
                return None
            try:
                v = parse(t)
            except ValueError:
                v = None
            if v is not None:
                return v
            self.log("  Please type a positive number" + (" (or 40 x 24 for a rectangle)." if parse is parse_area
                                                         else "."))

    def yes(self, question: str, default: bool = False) -> bool:
        t = self.q(question + " (y/n)", "y" if default else "n").lower()
        return t.startswith("y")

    def run(self) -> dict:
        self.log("Describe the venue. Areas and widths are in metres. Press Enter to accept [defaults].\n")
        name = self.q("Venue or event name", "My venue")
        permitted = self.number("Permitted or expected attendance (Enter to skip)", allow_empty=True)
        langs = self.q("Announcement languages, comma separated (en, hi, bn, ...)", "en")

        self.log("\nZONES: the areas people stand in (queue, hall, ground, exit lane...).")
        zones: List[dict] = []
        while True:
            zname = self.q(f"Zone {len(zones) + 1} name" + (" (Enter to finish)" if zones else ""))
            if not zname:
                if zones:
                    break
                self.log("  Add at least one zone.")
                continue
            area = self.number("  Area in m2, or length x width", parse=parse_area)
            frac = self.number("  Share of it people can stand on, after stalls, stage, barriers (0.1-1)", "1",
                               parse=lambda t: float(t) if 0.1 <= float(t) <= 1 else None)
            terr = self.q(f"  Hazards, comma separated ({', '.join(TERRAIN)}), Enter for none")
            terrain = [t.strip() for t in terr.split(",") if t.strip() in TERRAIN]
            zones.append({"id": slug(zname, {z["id"] for z in zones}), "name": zname, "area_m2": round(area, 1),
                          "usable_fraction": frac, "terrain": terrain})

        names = {z["id"]: z["name"] for z in zones}
        menu = ", ".join([f"{i}={n}" for i, n in enumerate(names.values(), 1)] + ["0=outside"])
        ids = list(names)

        def zone_ref(question):
            while True:
                t = self.q(f"  {question} ({menu})")
                if t.isdigit() and 0 <= int(t) <= len(ids):
                    return OUTSIDE if t == "0" else ids[int(t) - 1]
                self.log("  Type one of the numbers.")

        self.log("\nGATES: every opening between two zones, or between a zone and outside, with its narrowest width.")
        gates: List[dict] = []
        while True:
            label = f"Gate {len(gates) + 1} name"
            gname = self.q(label + (" (Enter to finish)" if gates else ""), None if gates else "Main gate")
            if not gname:
                break
            frm = zone_ref("From")
            to = zone_ref("To")
            if frm == to:
                self.log("  A gate must join two different places.")
                continue
            width = self.number("  Narrowest clear width in metres")
            g = {"id": f"G{len(gates) + 1}", "name": gname, "from": frm, "to": to, "width_m": width}
            if self.yes("  Are there steps or stairs at this gate?"):
                g["stairs"] = True
            if self.yes("  Is it normally kept closed (emergency exit)?"):
                g["open"] = False
            gates.append(g)

        languages = [s.strip() for s in langs.split(",") if s.strip()]
        d: Dict = {"id": slug(name, set()), "name": name, "languages": languages, "zones": zones, "gates": gates}
        if permitted:
            d["permitted_capacity"] = int(permitted)
        return d


def summarise(venue: Venue, log: Callable[[str], None] = print) -> List[str]:
    cap = capacity_for_venue(venue)
    log(f"\nUsable area      {venue.total_area:,.0f} m2")
    log(f"SAFE CAPACITY    {cap.safe_capacity:,}   (limited by {cap.limiting_factor})")
    if venue.permitted_capacity:
        ratio = venue.permitted_capacity / max(1, cap.safe_capacity)
        if ratio > 1:
            log(f"  ! The expected {venue.permitted_capacity:,} people is {ratio:.1f}x the safe capacity. "
                "Reduce entry, add exits, or use more space.")
    for f in cap.findings:
        log(f"  ! {f}")
    problems = venue_problems(venue)
    for p in problems:
        log(f"  ! {p}")
    return problems


def run_venue_new(out: str, ask: Ask = input, log: Callable[[str], None] = print, force: bool = False) -> int:
    path = Path(out)
    if path.exists() and not force:
        log(f"{path} already exists. Choose another name, or pass --force to replace it.")
        return 1
    d = _Wizard(ask, log).run()
    try:
        venue = Venue.from_dict(d)
    except (ValueError, KeyError) as e:
        log(f"The venue is not valid: {e}")
        return 1
    summarise(venue, log)
    path.write_text(json.dumps(d, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    log(f"\nSaved {path}. Zone names for camera calibration files: {', '.join(z['id'] for z in d['zones'])}")
    log(f"Start the control room with:  crowdsafe serve --venue {path} --host 0.0.0.0")
    return 0
