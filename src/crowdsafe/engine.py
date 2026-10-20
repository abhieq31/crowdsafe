"""The CrowdSafe engine: fusion -> assessment -> alerts -> playbook actions.

Transport-agnostic. The HTTP server, the CLI and the tests all drive the same
object, so behaviour stays identical from a laptop demo to a control room.
"""

from __future__ import annotations

import time
from typing import Dict, List, Optional

from .alerts import Alert, AlertManager, assess_zone
from .playbooks import Playbook, default_playbook
from .sim import FlowSimulator, load_scenario
from .venue import Venue
from .zones import Observation, ZoneEstimator


class Engine:
    def __init__(self, venue: Venue, playbook: Optional[Playbook] = None,
                 stale_after_s: float = 15.0, cooldown_s: float = 60.0):
        self.venue = venue
        self.playbook = playbook or default_playbook()
        self.stale_after_s = stale_after_s
        self.cooldown_s = cooldown_s
        self._reset()

    def _reset(self) -> None:
        self.estimators: Dict[str, ZoneEstimator] = {
            z.id: ZoneEstimator(z, stale_after_s=self.stale_after_s) for z in self.venue.zones.values()
        }
        self.alerts = AlertManager(self.venue, cooldown_s=self.cooldown_s, playbook=self.playbook)
        self.sim: Optional[FlowSimulator] = None
        self.sim_start = 0.0
        self.action_log: List[dict] = []
        self.last: dict = {}

    # --- inputs ----------------------------------------------------------
    def ingest(self, observations: List[Observation]) -> int:
        n = 0
        for o in observations:
            est = self.estimators.get(o.zone_id)
            if est is None:
                continue
            est.ingest(o)
            n += 1
        return n

    def set_trigger(self, name: str, active: bool, zone_id: str = "*") -> None:
        self.alerts.set_trigger(name, active, zone_id)

    def execute(self, action: dict, by: str = "operator") -> dict:
        """Record an operator-approved action and apply it (to the simulator in demo mode).

        In a real deployment the integrations layer (PA, gate actuators, SMS,
        CAP) subscribes to the action log. CrowdSafe recommends and a human approves.
        """
        result = "logged"
        g = self.venue.gates.get(action.get("gate", ""))
        if g is not None and action.get("type") in ("close_gate", "open_gate"):
            g.open = action["type"] == "open_gate"
        if self.sim is not None:
            result = self.sim.apply_action(action)
            for ch in self.sim.trigger_changes:
                self.set_trigger(ch["name"], ch["active"], ch.get("zone", "*"))
            self.sim.trigger_changes.clear()
        entry = {"ts": self.now(), "by": by, "action": action, "result": result}
        self.action_log.append(entry)
        return entry

    # --- demo ------------------------------------------------------------
    def start_demo(self, scenario: str = "pandal_surge") -> None:
        self._reset()
        self.sim = FlowSimulator(self.venue, load_scenario(scenario))
        self.sim_start = time.time()

    def stop_demo(self) -> None:
        self._reset()

    def now(self) -> float:
        # In demo mode the engine runs on simulator time, so the simulation can
        # run faster than real time while trends and countdowns stay consistent.
        if self.sim is not None:
            return self.sim_start + self.sim.t
        return time.time()

    # --- main loop -------------------------------------------------------
    def tick(self, sim_steps: int = 1) -> List[Alert]:
        new_alerts: List[Alert] = []
        for _ in range(sim_steps if self.sim is not None else 1):
            if self.sim is not None:
                self.sim.step()
                for ch in self.sim.trigger_changes:
                    self.set_trigger(ch["name"], ch["active"], ch.get("zone", "*"))
                self.sim.trigger_changes.clear()
                self.ingest(self.sim.observations(self.now()))
            new_alerts += self._evaluate()
        return new_alerts

    def _evaluate(self) -> List[Alert]:
        now = self.now()
        states = {zid: est.snapshot(now) for zid, est in self.estimators.items()}
        new = self.alerts.update(states, now)
        zones = []
        total = 0
        for zid, st in states.items():
            zone = self.venue.zones[zid]
            a = assess_zone(st, zone, self.alerts.active_triggers(zid))
            if st.occupancy is not None:
                total += st.occupancy
            zones.append({
                **st.to_dict(),
                "name": zone.name,
                "area_m2": round(zone.usable_area, 1),
                "level": self.alerts.current[zid],
                "instant_level": a.label,
                "reasons": a.reasons,
                "triggers": sorted(self.alerts.active_triggers(zid)),
            })
        self.last = {
            "venue_id": self.venue.id,
            "ts": now,
            "mode": "demo" if self.sim else "live",
            "sim_t": self.sim.t if self.sim else None,
            "scenario": self.sim.scenario.name if self.sim else None,
            "occupancy_total": total,
            "permitted_capacity": self.venue.permitted_capacity,
            "zones": zones,
            "gates": self.sim.gate_status() if self.sim else [
                {"id": g.id, "open": g.open, "factor": 1.0, "flow_pps": None}
                for g in self.venue.gates.values()],
            "alerts": [a.to_dict() for a in self.alerts.history[-30:]][::-1],
            "actions": self.action_log[-30:][::-1],
            "sim_log": self.sim.log[-10:][::-1] if self.sim else [],
        }
        return new

    def state(self) -> dict:
        if not self.last:
            self._evaluate()
        return self.last
