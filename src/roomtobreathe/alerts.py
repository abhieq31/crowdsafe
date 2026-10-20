"""Explainable alerting with hysteresis.

Every alert says *why* it fired, so operators can trust it and act fast.
Escalation is immediate. De-escalation waits for a cooldown so alerts don't
flap while operators are acting.
"""

from __future__ import annotations

import itertools
import time
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Set

from .physics import Level, PressureThresholds
from .venue import Venue, Zone
from .zones import ZoneState

# Context triggers taken from the stampede literature (Singh & Kishore 2025, §4.2).
TRIGGERS = {
    "free_distribution": "Free items being distributed",
    "rumour": "Rumour reported",
    "gate_change": "Sudden change of gate, platform or route",
    "vip_arrival": "VIP or celebrity arrival",
    "rain": "Rain (slippery ground, people crowding for shelter)",
    "power_cut": "Power or lighting failure",
    "event_end": "Event ending (exit surge)",
    "event_start": "Event starting (entry surge)",
}

TTC_RED_S = 180.0
TTC_ORANGE_S = 600.0
TTC_YELLOW_S = 1200.0
STOP_GO_STRENGTH = 0.4


@dataclass
class Assessment:
    zone_id: str
    level: Optional[Level]  # None means UNKNOWN (no fresh data)
    reasons: List[str] = field(default_factory=list)

    @property
    def label(self) -> str:
        return self.level.name if self.level is not None else "UNKNOWN"


def assess_zone(state: ZoneState, zone: Zone, triggers: Iterable[str] = (),
                pressure_th: PressureThresholds = PressureThresholds()) -> Assessment:
    if state.stale or state.density is None:
        return Assessment(zone.id, None, ["No fresh sensor data. Check cameras and counters."])

    th = zone.thresholds
    d = state.density
    level = th.level(d)
    reasons = [f"density {d:.1f} p/m² ({level.name.lower()} at ≥{_thr(th, level)})"] \
        if level > Level.GREEN else []

    if state.trend_per_min is not None and state.trend_per_min > 0.05:
        reasons.append(f"rising {state.trend_per_min:+.2f} p/m² per min")

    if state.pressure is not None and d >= th.yellow:
        pl = pressure_th.level(state.pressure)
        if pl > Level.GREEN:
            reasons.append(f"crowd pressure {state.pressure:.3f}/s²: turbulent motion")
            level = max(level, pl)

    if state.ttc_s is not None:
        t = state.ttc_s
        tl = Level.GREEN
        if t <= TTC_RED_S:
            tl = Level.RED
        elif t <= TTC_ORANGE_S:
            tl = Level.ORANGE
        elif t <= TTC_YELLOW_S:
            tl = Level.YELLOW
        if tl > Level.GREEN:
            reasons.append(f"critical density in ~{_fmt_s(t)} at current net inflow"
                           if t > 0 else "critical density reached")
            level = max(level, tl)

    if state.inflow_pps is not None and state.outflow_pps is not None \
            and state.inflow_pps > 1.5 * max(state.outflow_pps, 0.1) and d >= th.yellow:
        ratio = state.inflow_pps / max(state.outflow_pps, 0.1)
        reasons.append(f"inflow {state.inflow_pps:.1f}/s is {ratio:.1f}× outflow")

    if state.stop_go_strength is not None and state.stop_go_strength >= STOP_GO_STRENGTH \
            and d >= 3.0:
        reasons.append(f"stop-and-go waves (period ~{state.stop_go_period_s:.0f}s)")
        level = max(level, Level.ORANGE)

    active = [t for t in triggers if t in TRIGGERS]
    if active and level >= Level.YELLOW:
        level = Level(min(Level.RED, level + 1))
        reasons.append("trigger active: " + ", ".join(TRIGGERS[t] for t in active))

    if "mud" in zone.terrain or "steps" in zone.terrain or "slope" in zone.terrain:
        if level >= Level.ORANGE:
            reasons.append("hazardous terrain: " + ", ".join(zone.terrain))

    return Assessment(zone.id, level, reasons)


def _thr(th, level: Level) -> float:
    return {Level.YELLOW: th.yellow, Level.ORANGE: th.orange, Level.RED: th.red}.get(level, 0)


def _fmt_s(s: float) -> str:
    if s < 90:
        return f"{int(s)}s"
    return f"{s / 60:.0f} min"


_alert_ids = itertools.count(1)


@dataclass
class Alert:
    id: str
    venue_id: str
    zone_id: str
    zone_name: str
    level: str
    previous: str
    reasons: List[str]
    ts: float
    actions: List[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return self.__dict__.copy()


class AlertManager:
    """Tracks the current level per zone and emits an Alert whenever it changes."""

    def __init__(self, venue: Venue, cooldown_s: float = 60.0, playbook=None):
        self.venue = venue
        self.cooldown_s = cooldown_s
        self.playbook = playbook
        self.current: Dict[str, str] = {z: "GREEN" for z in venue.zones}
        self._below_since: Dict[str, Optional[float]] = {z: None for z in venue.zones}
        self.triggers: Dict[str, Set[str]] = {"*": set()}
        self.history: List[Alert] = []

    def set_trigger(self, name: str, active: bool, zone_id: str = "*") -> None:
        if name not in TRIGGERS:
            raise ValueError(f"unknown trigger {name!r}; known: {sorted(TRIGGERS)}")
        s = self.triggers.setdefault(zone_id, set())
        (s.add if active else s.discard)(name)

    def active_triggers(self, zone_id: str) -> Set[str]:
        return self.triggers.get("*", set()) | self.triggers.get(zone_id, set())

    def update(self, states: Dict[str, ZoneState], now: Optional[float] = None) -> List[Alert]:
        now = time.time() if now is None else now
        emitted: List[Alert] = []
        for zid, st in states.items():
            zone = self.venue.zones[zid]
            a = assess_zone(st, zone, self.active_triggers(zid))
            new, cur = a.label, self.current[zid]
            if new == cur:
                self._below_since[zid] = None
                continue
            if _rank(new) < _rank(cur) and cur != "UNKNOWN":
                # De-escalate only once the lower level has held for the cooldown.
                since = self._below_since[zid]
                if since is None:
                    self._below_since[zid] = now
                    continue
                if now - since < self.cooldown_s:
                    continue
            self._below_since[zid] = None
            alert = Alert(
                id=f"{self.venue.id}-{next(_alert_ids)}",
                venue_id=self.venue.id,
                zone_id=zid,
                zone_name=zone.name,
                level=new,
                previous=cur,
                reasons=a.reasons,
                ts=now,
            )
            if self.playbook is not None and new != "UNKNOWN":
                alert.actions = self.playbook.actions_for(self.venue, zid, new,
                                                          self.active_triggers(zid))
            self.current[zid] = new
            self.history.append(alert)
            emitted.append(alert)
        return emitted


def _rank(label: str) -> int:
    # UNKNOWN ranks as high as ORANGE: missing data should get attention.
    return {"GREEN": 0, "YELLOW": 1, "ORANGE": 2, "UNKNOWN": 2, "RED": 3}[label]
