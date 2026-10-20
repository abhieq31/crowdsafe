"""Per-zone state estimation and sensor fusion.

Many sources report on the same zone: cameras, gate counters, telecom
aggregates and marshals. Each reports with its own uncertainty. The estimator
fuses fresh readings by inverse-variance weighting. When every source has gone
silent it reports the zone as *stale* instead of quietly showing old numbers.
A dead camera must never look "safe".
"""

from __future__ import annotations

import time
from collections import deque
from dataclasses import asdict, dataclass, field
from typing import Deque, Dict, List, Optional, Tuple

from .physics import crowd_pressure, dominant_oscillation, time_to_critical
from .venue import Zone

#: A zone with no report for this long shows UNKNOWN (ranked like ORANGE), so a sensor must report faster.
STALE_AFTER_S = 15.0
OSC_EVERY_S = 5.0
DEFAULT_SIGMA = 0.6  # p/m2, used when a source reports no uncertainty


@dataclass
class Observation:
    zone_id: str
    source: str  # e.g. "cam-12", "gate-2-clicker", "telecom"
    ts: Optional[float] = None
    density: Optional[float] = None  # p/m2
    density_sigma: Optional[float] = None
    count: Optional[float] = None  # people in the zone (converted using zone area)
    mean_speed: Optional[float] = None  # m/s
    velocity_variance: Optional[float] = None  # m^2/s^2
    inflow_pps: Optional[float] = None
    outflow_pps: Optional[float] = None

    @classmethod
    def from_dict(cls, d: dict) -> "Observation":
        known = {k: d[k] for k in cls.__dataclass_fields__ if k in d}
        return cls(**known)


@dataclass
class ZoneState:
    zone_id: str
    ts: float
    stale: bool
    density: Optional[float]
    density_sigma: Optional[float]
    occupancy: Optional[int]
    trend_per_min: Optional[float]
    inflow_pps: Optional[float]
    outflow_pps: Optional[float]
    mean_speed: Optional[float]
    velocity_variance: Optional[float]
    pressure: Optional[float]
    ttc_s: Optional[float]
    stop_go_period_s: Optional[float]
    stop_go_strength: Optional[float]
    sources: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        d = asdict(self)
        for k, v in d.items():
            if isinstance(v, float):
                d[k] = round(v, 4)
        return d


def server_time(ts: Optional[float], sent_at: Optional[float], now: float) -> Optional[float]:
    """Translate a sensor timestamp to server time using the sensor's clock at send time.

    ``ts + (now - sent_at)`` removes any clock offset between the device and the server while
    keeping the age of backlogged observations that were held during a network outage.
    """
    if ts is None:
        return None
    if sent_at is not None:
        ts += now - sent_at
    return min(ts, now)


class ZoneEstimator:
    def __init__(self, zone: Zone, stale_after_s: float = STALE_AFTER_S, window_s: float = 120.0,
                 speed_dt: float = 1.0):
        self.zone = zone
        self.stale_after_s = stale_after_s
        self.window_s = window_s
        self.speed_dt = speed_dt
        self._latest: Dict[str, Tuple[float, Observation]] = {}
        self._density_hist: Deque[Tuple[float, float]] = deque()
        self._speed_hist: Deque[Tuple[float, float]] = deque()
        self._osc_cache: Optional[Tuple[float, Optional[tuple]]] = None

    def ingest(self, obs: Observation, now: Optional[float] = None) -> None:
        now = time.time() if now is None else now
        # Never accept a timestamp from the future: a sensor whose clock runs ahead would otherwise
        # look fresh long after it died, hiding exactly the failure "stale" exists to show.
        ts = min(obs.ts, now) if obs.ts is not None else now
        obs.ts = ts
        if obs.density is None and obs.count is not None and self.zone.usable_area > 0:
            obs.density = obs.count / self.zone.usable_area
        self._latest[obs.source] = (ts, obs)

    def _fresh(self, now: float) -> List[Observation]:
        return [o for ts, o in self._latest.values() if now - ts <= self.stale_after_s]

    @staticmethod
    def _pick(obs: List[Observation], attr: str) -> Optional[float]:
        vals = [(o.ts, getattr(o, attr)) for o in obs if getattr(o, attr) is not None]
        return max(vals)[1] if vals else None

    def _slope_per_s(self) -> Optional[float]:
        pts = list(self._density_hist)
        if len(pts) < 3:
            return None
        t0 = pts[0][0]
        xs = [p[0] - t0 for p in pts]
        ys = [p[1] for p in pts]
        n = len(xs)
        mx, my = sum(xs) / n, sum(ys) / n
        sxx = sum((x - mx) ** 2 for x in xs)
        if sxx <= 1e-9:
            return None
        return sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sxx

    def snapshot(self, now: Optional[float] = None) -> ZoneState:
        now = time.time() if now is None else now
        fresh = self._fresh(now)
        area = self.zone.usable_area
        crit = self.zone.thresholds.critical

        density = sigma = None
        dens_obs = [o for o in fresh if o.density is not None]
        if dens_obs:
            weights = [1.0 / max(1e-3, (o.density_sigma or DEFAULT_SIGMA)) ** 2 for o in dens_obs]
            wsum = sum(weights)
            density = sum(w * o.density for w, o in zip(weights, dens_obs)) / wsum
            sigma = (1.0 / wsum) ** 0.5
            self._density_hist.append((now, density))
        while self._density_hist and now - self._density_hist[0][0] > self.window_s:
            self._density_hist.popleft()

        speed = self._pick(fresh, "mean_speed")
        if speed is not None:
            if not self._speed_hist or now - self._speed_hist[-1][0] >= self.speed_dt * 0.5:
                self._speed_hist.append((now, speed))
        while self._speed_hist and now - self._speed_hist[0][0] > self.window_s:
            self._speed_hist.popleft()

        vvar = self._pick(fresh, "velocity_variance")
        inflow = self._pick(fresh, "inflow_pps")
        outflow = self._pick(fresh, "outflow_pps")
        slope = self._slope_per_s()

        ttc = None
        if density is not None:
            if inflow is not None and outflow is not None:
                ttc = time_to_critical(density, area, inflow, outflow, crit)
            elif slope is not None:
                # No flow measurements, so use the observed density trend.
                ttc = time_to_critical(density, 1.0, max(0.0, slope), 0.0, crit)

        period = strength = None
        if len(self._speed_hist) >= 16:
            # Stop-and-go waves have periods of 4-60 s, so re-detecting every few seconds loses nothing.
            # It is the most expensive step, and this keeps the server and the browser what-if fast.
            if self._osc_cache is None or abs(now - self._osc_cache[0]) >= OSC_EVERY_S:
                self._osc_cache = (now, dominant_oscillation([s for _, s in self._speed_hist], self.speed_dt))
            if self._osc_cache[1]:
                period, strength = self._osc_cache[1]

        return ZoneState(
            zone_id=self.zone.id,
            ts=now,
            stale=not fresh,
            density=density,
            density_sigma=sigma,
            occupancy=int(round(density * area)) if density is not None else None,
            trend_per_min=slope * 60.0 if slope is not None else None,
            inflow_pps=inflow,
            outflow_pps=outflow,
            mean_speed=speed,
            velocity_variance=vvar,
            pressure=crowd_pressure(density, vvar) if (density is not None and vvar is not None) else None,
            ttc_s=ttc,
            stop_go_period_s=period,
            stop_go_strength=strength,
            sources=sorted(o.source for o in fresh),
        )
