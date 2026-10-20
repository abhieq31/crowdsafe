"""Macroscopic crowd-flow simulator.

A cell-transmission-style model on the venue graph. Each zone holds people.
Each gate passes ``min(demand upstream, supply downstream, gate capacity)``
per tick. It is deliberately simple, deterministic given a seed, and runs in
pure Python. It serves three purposes:

1. **Demo and training**: drive the whole stack (alerts, playbooks, dashboard)
   with no cameras, and let operators rehearse.
2. **What-if**: "if Gate 3 closes now, where does the pressure go?"
3. **Testing**: a reproducible crowd to regression-test the alert engine.

For agent-level studies, plug in JuPedSim or Vadere (see docs/PLAN.md).
Gate clogging follows the "faster-is-slower" effect: above ~4 p/m2 upstream,
effective exit flow drops.
"""

from __future__ import annotations

import copy
import math
import random
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Tuple

from ..physics import weidmann_speed
from ..venue import OUTSIDE, Venue
from ..zones import Observation

RHO_JAM = 9.0
SUPPLY_DROP_DENSITY = 5.5


@dataclass
class Scenario:
    name: str
    description: str
    arrival_pps: float  # base arrivals from outside, split across entry gates
    dwell_s: Dict[str, float]  # mean time people want to stay in each zone
    events: List[Tuple[float, str, dict]] = field(default_factory=list)
    seed: int = 7


def _pandal_surge() -> Scenario:
    return Scenario(
        name="pandal_surge",
        description=(
            "Evening rush at a puja pandal. At 3 min, free prasad starts in the hall, "
            "so people linger longer and word spreads to the street. At 7 min, people "
            "stopping on the exit steps cut the hall's exit flow to 40%. Without action the "
            "hall goes critical. Run the RED playbook to watch it recover."
        ),
        arrival_pps=3.0,
        dwell_s={"approach": 60, "corridor": 25, "hall": 240, "exit_lane": 20},
        events=[
            (180, "trigger", {"name": "free_distribution", "zone": "hall", "active": True}),
            (180, "dwell", {"zone": "hall", "seconds": 900}),
            (180, "arrivals", {"multiplier": 2.5}),
            (420, "gate_factor", {"gate": "G4", "factor": 0.4}),
        ],
    )


def _steady() -> Scenario:
    return Scenario(
        name="steady",
        description="Normal steady flow, nothing goes wrong. Useful as a false-alarm baseline.",
        arrival_pps=2.0,
        dwell_s={"approach": 60, "corridor": 25, "hall": 200, "exit_lane": 20},
    )


SCENARIOS: Dict[str, Callable[[], Scenario]] = {"pandal_surge": _pandal_surge, "steady": _steady}


def load_scenario(name: str) -> Scenario:
    if name not in SCENARIOS:
        raise KeyError(f"unknown scenario {name!r}; available: {sorted(SCENARIOS)}")
    return SCENARIOS[name]()


class FlowSimulator:
    def __init__(self, venue: Venue, scenario: Scenario, dt: float = 1.0):
        self.venue = copy.deepcopy(venue)  # gates get opened and closed during the sim
        self.scenario = scenario
        self.dt = dt
        self.t = 0.0
        self.rng = random.Random(scenario.seed)
        self.occ: Dict[str, float] = {z: 0.0 for z in self.venue.zones}
        self.outside_queue = 0.0
        self.gate_factor: Dict[str, float] = {g: 1.0 for g in self.venue.gates}
        self.dwell = dict(scenario.dwell_s)
        self.base_dwell = dict(scenario.dwell_s)
        self.arrival_mult = 1.0
        self.last_flow: Dict[str, float] = {g: 0.0 for g in self.venue.gates}
        self.trigger_changes: List[dict] = []  # drained by the engine
        self._pending = sorted(scenario.events, key=lambda e: e[0])
        self.log: List[str] = []

    # --- operator actions ------------------------------------------------
    def apply_action(self, action: dict) -> str:
        t = action.get("type")
        g = action.get("gate")
        if t == "close_gate" and g in self.venue.gates:
            self.venue.gates[g].open = False
            msg = f"closed {g}"
        elif t == "open_gate" and g in self.venue.gates:
            self.venue.gates[g].open = True
            self.gate_factor[g] = max(self.gate_factor[g], 1.0)
            msg = f"opened {g}"
        elif t == "meter_gate" and g in self.venue.gates:
            self.gate_factor[g] = float(action.get("factor", 0.5))
            msg = f"metered {g} to {self.gate_factor[g]:.0%}"
        elif t == "stop_programme":
            # People stop competing for the focal point: dwell returns to normal
            # and word-of-mouth arrivals stop.
            self.dwell = dict(self.base_dwell)
            self.arrival_mult = 1.0
            msg = "programme paused"
        elif t == "announce" and action.get("message") == "no_danger":
            self.trigger_changes.append({"name": "rumour", "zone": "*", "active": False})
            msg = "rumour countered"
        else:
            msg = f"{t}: noted (no effect in simulator)"
        self.log.append(f"t={self.t:.0f}s {msg}")
        return msg

    # --- dynamics --------------------------------------------------------
    def _density(self, zid: str) -> float:
        return self.occ[zid] / max(1.0, self.venue.zones[zid].usable_area)

    def _apply_events(self) -> None:
        while self._pending and self._pending[0][0] <= self.t:
            _, kind, p = self._pending.pop(0)
            if kind == "trigger":
                self.trigger_changes.append(p)
            elif kind == "dwell":
                self.dwell[p["zone"]] = p["seconds"]
            elif kind == "arrivals":
                self.arrival_mult = p["multiplier"]
            elif kind == "gate_factor":
                self.gate_factor[p["gate"]] = p["factor"]
            self.log.append(f"t={self.t:.0f}s scenario event: {kind} {p}")

    def step(self) -> None:
        self._apply_events()
        dt = self.dt
        self.outside_queue += self.scenario.arrival_pps * self.arrival_mult * dt
        gates = list(self.venue.gates.values())

        # Demand: how many people in each source want to pass each gate this tick.
        demand: Dict[str, float] = {}
        by_source: Dict[str, List] = {}
        for g in gates:
            by_source.setdefault(g.from_zone, []).append(g)
        for src, gs in by_source.items():
            caps = {g.id: g.capacity_pps * self.gate_factor[g.id] for g in gs}
            total_cap = sum(caps.values())
            if src == OUTSIDE:
                want = self.outside_queue / dt
            else:
                want = self.occ[src] / max(1.0, self.dwell.get(src, 30.0))
                d = self._density(src)
                if d > 4.0:  # clogging at the exit: faster is slower
                    want *= max(0.6, 1.0 - 0.13 * (d - 4.0))
            for g in gs:
                share = caps[g.id] / total_cap if total_cap > 0 else 0.0
                demand[g.id] = min(want * share, caps[g.id])

        # Supply: downstream zones accept less as they approach jam density.
        flow: Dict[str, float] = {}
        for g in gates:
            q = demand.get(g.id, 0.0)
            if g.to_zone != OUTSIDE:
                # People behind keep pushing in, so supply only collapses near jam density.
                d = self._density(g.to_zone)
                if d > SUPPLY_DROP_DENSITY:
                    q *= max(0.0, (RHO_JAM - d) / (RHO_JAM - SUPPLY_DROP_DENSITY))
            flow[g.id] = q

        for g in gates:
            n = flow[g.id] * dt
            if g.from_zone == OUTSIDE:
                n = min(n, self.outside_queue)
                self.outside_queue -= n
            else:
                n = min(n, self.occ[g.from_zone])
                self.occ[g.from_zone] -= n
            if g.to_zone != OUTSIDE:
                self.occ[g.to_zone] += n
            self.last_flow[g.id] = n / dt
        self.t += dt

    # --- synthetic sensors ---------------------------------------------
    def observations(self, ts: float) -> List[Observation]:
        out = []
        for zid in self.venue.zones:
            d = self._density(zid)
            noise = self.rng.gauss(0, 0.05 + 0.04 * d)
            speed = weidmann_speed(d)
            if d > 4.5:  # stop-and-go waves appear in dense, pushing crowds
                speed *= 1.0 + 0.6 * math.sin(2 * math.pi * self.t / 20.0)
            speed = max(0.0, speed + self.rng.gauss(0, 0.03))
            vvar = 0.0003 + max(0.0, d - 3.5) ** 2 * 0.0008
            inflow = sum(self.last_flow[g.id] for g in self.venue.gates.values() if g.to_zone == zid)
            outflow = sum(self.last_flow[g.id] for g in self.venue.gates.values() if g.from_zone == zid)
            out.append(Observation(
                zone_id=zid, source=f"sim-cam-{zid}", ts=ts,
                density=max(0.0, d + noise), density_sigma=0.1 + 0.05 * d,
                mean_speed=speed, velocity_variance=vvar * (1 + self.rng.gauss(0, 0.1)),
                inflow_pps=inflow, outflow_pps=outflow,
            ))
        return out

    def gate_status(self) -> List[dict]:
        return [{"id": g.id, "open": g.open, "factor": self.gate_factor[g.id],
                 "flow_pps": round(self.last_flow[g.id], 2)} for g in self.venue.gates.values()]

    def true_density(self, zid: str) -> float:
        return self._density(zid)
