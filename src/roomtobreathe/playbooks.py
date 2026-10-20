"""Pre-approved response playbooks.

Decisions are made calmly before the event and carried out in seconds during
it. This addresses the "slow decision-making" failure that recurs in
stampede post-mortems.

A playbook is JSON::

    {
      "messages": {"hold": {"en": "...", "hi": "...", "bn": "..."}},
      "rules": [
        {"when": {"level": "ORANGE", "zone": "*", "trigger": "rumour"},
         "actions": [{"type": "announce", "message": "no_danger"}]}
      ]
    }

Gate selectors resolve against the venue graph, so one generic playbook works
for any venue:

- ``"inflow"``: gates feeding the alerting zone directly
- ``"outflow"``: gates draining it
- ``"entry"``: perimeter gates (from outside) through which people reach the zone
- ``"upstream"``: every gate upstream of the direct inflow gates

Crowd-safety principle: **hold people back upstream**. Never seal the gate at
the crush point while people are still pressing in behind it, or the crush
just moves one zone back.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable, List

from .venue import OUTSIDE, Venue

LEVEL_ORDER = ["GREEN", "YELLOW", "ORANGE", "RED"]
ACTION_TYPES = {"announce", "meter_gate", "close_gate", "open_gate", "dispatch",
                "stop_programme", "notify", "cell_broadcast"}


class Playbook:
    def __init__(self, data: dict):
        self.messages = data.get("messages", {})
        self.rules = data.get("rules", [])
        for r in self.rules:
            for a in r.get("actions", []):
                if a.get("type") not in ACTION_TYPES:
                    raise ValueError(f"unknown action type {a.get('type')!r}")
                if a["type"] == "announce" and a.get("message") not in self.messages:
                    raise ValueError(f"announce references unknown message {a.get('message')!r}")

    @classmethod
    def load(cls, path) -> "Playbook":
        return cls(json.loads(Path(path).read_text(encoding="utf-8")))

    def actions_for(self, venue: Venue, zone_id: str, level: str,
                    triggers: Iterable[str] = ()) -> List[dict]:
        triggers = set(triggers)
        out: List[dict] = []
        seen = set()
        for rule in self.rules:
            w = rule.get("when", {})
            if w.get("zone", "*") not in ("*", zone_id):
                continue
            if "level" in w and w["level"] != level:
                continue
            if "min_level" in w and LEVEL_ORDER.index(level) < LEVEL_ORDER.index(w["min_level"]):
                continue
            if "trigger" in w and w["trigger"] not in triggers:
                continue
            for a in rule.get("actions", []):
                for resolved in self._resolve(venue, zone_id, a):
                    key = json.dumps(resolved, sort_keys=True, ensure_ascii=False)
                    if key not in seen:
                        seen.add(key)
                        out.append(resolved)
        return out

    def _resolve(self, venue: Venue, zone_id: str, action: dict) -> List[dict]:
        a = dict(action)
        a["zone"] = zone_id
        if a["type"] == "announce":
            a["text"] = {lang: t.format(zone=venue.zones[zone_id].name)
                         for lang, t in self.messages[a["message"]].items()
                         if lang in venue.languages or lang == "en"}
        sel = a.get("gates")
        if sel in ("inflow", "outflow", "entry", "upstream"):
            gates = _gates(venue, zone_id, sel)
            a.pop("gates")
            if not gates:
                return []
            return [dict(a, gate=g.id, gate_name=g.name) for g in gates]
        return [a]


def _gates(venue: Venue, zone_id: str, direction: str):
    if direction == "inflow":
        return [g for g in venue.gates.values() if g.to_zone == zone_id]
    if direction == "outflow":
        return [g for g in venue.gates.values() if g.from_zone == zone_id]
    # Walk the graph backwards from the zone to find everything upstream.
    direct = {g.id for g in venue.gates.values() if g.to_zone == zone_id}
    seen_zones, frontier, upstream = {zone_id}, [zone_id], []
    while frontier:
        z = frontier.pop()
        for g in venue.gates.values():
            if g.to_zone == z and g.from_zone not in seen_zones:
                if g.id not in direct:
                    upstream.append(g)
                if g.from_zone != OUTSIDE:
                    seen_zones.add(g.from_zone)
                    frontier.append(g.from_zone)
    if direction == "entry":
        return [g for g in upstream + [venue.gates[i] for i in direct] if g.from_zone == OUTSIDE]
    return [g for g in upstream if g.from_zone != OUTSIDE]


DATA_DIR = Path(__file__).resolve().parent / "data"


def default_playbook() -> Playbook:
    return Playbook.load(DATA_DIR / "default_playbook.json")
