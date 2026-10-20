"""Crowd physics primitives.

Everything here is pure Python (no numpy) so the core runs on any device,
including a cheap phone running a Tier 0 deployment.

References
----------
- Fruin, J. (1971). *Pedestrian Planning and Design* (Levels of Service).
- Weidmann, U. (1993). Transporttechnik der Fussgaenger (fundamental diagram).
- Helbing, Johansson & Al-Abideen (2007). Dynamics of crowd disasters: an
  empirical study. Phys. Rev. E 75, 046109 ("crowd pressure" = rho * Var(v)).
- UK Green Guide (SGSA), 6th ed. - flow rates through exits.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import IntEnum
from typing import Optional, Sequence


class Level(IntEnum):
    """Alert level. Ordered so ``max()`` picks the most severe."""

    GREEN = 0
    YELLOW = 1
    ORANGE = 2
    RED = 3

    @property
    def label(self) -> str:
        return self.name


@dataclass(frozen=True)
class DensityThresholds:
    """Persons per square metre at which each level begins.

    Defaults follow the consensus range in the crowd-safety literature: moving
    crowds become unsafe from ~4 p/m2, and at ~6-7 p/m2 individuals lose
    control of their movement. Sources disagree (5-10 p/m2), so these are
    *calibrated parameters*; venues override them.
    """

    yellow: float = 2.0
    orange: float = 4.0
    red: float = 5.5
    critical: float = 6.5  # used for time-to-critical

    def level(self, density: float) -> Level:
        if density >= self.red:
            return Level.RED
        if density >= self.orange:
            return Level.ORANGE
        if density >= self.yellow:
            return Level.YELLOW
        return Level.GREEN


@dataclass(frozen=True)
class PressureThresholds:
    """Crowd pressure (1/s^2). Helbing et al. observed turbulence above ~0.02."""

    orange: float = 0.01
    red: float = 0.02

    def level(self, pressure: float) -> Level:
        if pressure >= self.red:
            return Level.RED
        if pressure >= self.orange:
            return Level.ORANGE
        return Level.GREEN


def fruin_los(density: float) -> str:
    """Fruin Level of Service (walkways) A-F from density in p/m2."""
    # Fruin walkway LoS by area per pedestrian (m2/p): A>3.24, B>2.32, C>1.39,
    # D>0.93, E>0.46, F<=0.46.
    if density <= 0:
        return "A"
    area = 1.0 / density
    for los, limit in (("A", 3.24), ("B", 2.32), ("C", 1.39), ("D", 0.93), ("E", 0.46)):
        if area > limit:
            return los
    return "F"


def crowd_pressure(density: float, velocity_variance: float) -> float:
    """Helbing's crowd pressure P = rho * Var(v) in 1/s^2.

    ``velocity_variance`` is the local variance of the velocity vector field
    (m^2/s^2). High density alone is dangerous; high density *with* erratic
    motion is the signature of crowd turbulence that precedes crushes.
    """
    return max(0.0, density) * max(0.0, velocity_variance)


def time_to_critical(
    density: float,
    area_m2: float,
    inflow_pps: float,
    outflow_pps: float,
    critical_density: float,
) -> Optional[float]:
    """Seconds until the zone reaches ``critical_density`` at current net flow.

    Returns 0 if already critical and ``None`` if the zone is not filling.
    This is the single most useful number for an operator: "Zone B goes
    critical in ~6 minutes unless inflow is reduced".
    """
    if density >= critical_density:
        return 0.0
    net = inflow_pps - outflow_pps
    if net <= 1e-9 or area_m2 <= 0:
        return None
    return (critical_density - density) * area_m2 / net


def weidmann_speed(density: float, v_free: float = 1.34, rho_max: float = 8.0) -> float:
    """Walking speed (m/s) for a density, from Weidmann's fundamental diagram.

    Weidmann's original jam density is 5.4 p/m2; real crush crowds go well
    beyond that, so ``rho_max`` defaults higher and is configurable.
    """
    if density <= 0:
        return v_free
    if density >= rho_max:
        return 0.0
    return max(0.0, v_free * (1.0 - math.exp(-1.913 * (1.0 / density - 1.0 / rho_max))))


# Specific flow through exits (persons per metre width per minute). Green Guide
# uses 82 p/m/min on level ways and 66 on stairways for design.
FLOW_LEVEL_PPM_PER_MIN = 82.0
FLOW_STAIRS_PPM_PER_MIN = 66.0


def exit_flow_pps(width_m: float, stairs: bool = False) -> float:
    """Design flow capacity of an exit in persons per second."""
    rate = FLOW_STAIRS_PPM_PER_MIN if stairs else FLOW_LEVEL_PPM_PER_MIN
    return max(0.0, width_m) * rate / 60.0


def dominant_oscillation(series: Sequence[float], dt: float, min_period: float = 4.0,
                         max_period: float = 60.0) -> Optional[tuple]:
    """Detect stop-and-go waves in a speed time series.

    Returns ``(period_seconds, strength)`` of the strongest autocorrelation peak
    within the period band, or ``None``. ``strength`` is the normalised
    autocorrelation in [0, 1]; values above ~0.4 indicate clear periodic
    stop-and-go motion, which Helbing et al. observed minutes before turbulence.
    """
    n = len(series)
    if n < 8 or dt <= 0:
        return None
    # Remove the linear trend: a crowd that is steadily slowing down is not oscillating.
    ts = range(n)
    mt, mean = (n - 1) / 2.0, sum(series) / n
    stt = sum((t - mt) ** 2 for t in ts)
    slope = sum((t - mt) * (v - mean) for t, v in zip(ts, series)) / stt
    x = [v - mean - slope * (t - mt) for t, v in zip(ts, series)]
    var = sum(v * v for v in x)
    if var <= 1e-12:
        return None
    lo = max(1, int(math.ceil(min_period / dt)))
    hi = min(n // 2, int(max_period / dt))
    best = None
    prev = None
    rising = False
    for lag in range(lo, hi + 1):
        r = sum(x[i] * x[i + lag] for i in range(n - lag)) / var
        r *= n / (n - lag)  # unbias for shorter overlap
        if prev is not None:
            if r < prev and rising and (best is None or prev > best[1]):
                best = (round((lag - 1) * dt, 2), prev)
            rising = r > prev
        prev = r
    if best is None or best[1] <= 0:
        return None
    return best[0], min(1.0, best[1])
