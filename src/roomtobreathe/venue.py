"""Venue model: zones, gates and the links between them.

A venue is a small graph. Zones are areas where people stand or walk (nodes),
and gates are openings between zones, or between a zone and the outside
(edges). Geometry is kept in local metres so area can be computed with no GIS
dependency. An optional ``origin`` (lat, lon) lets alerts carry real
coordinates for CAP.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .physics import DensityThresholds, exit_flow_pps

Point = Tuple[float, float]
OUTSIDE = "outside"


def polygon_area(points: List[Point]) -> float:
    """Shoelace formula, absolute area in the polygon's units squared."""
    n = len(points)
    if n < 3:
        return 0.0
    s = 0.0
    for i in range(n):
        x1, y1 = points[i]
        x2, y2 = points[(i + 1) % n]
        s += x1 * y2 - x2 * y1
    return abs(s) / 2.0


@dataclass
class Zone:
    id: str
    name: str
    polygon: List[Point] = field(default_factory=list)
    area_m2: Optional[float] = None
    usable_fraction: float = 1.0  # fraction not blocked by stage, stalls, idols...
    terrain: List[str] = field(default_factory=list)  # e.g. ["mud", "slope", "steps"]
    thresholds: DensityThresholds = field(default_factory=DensityThresholds)

    @property
    def usable_area(self) -> float:
        base = self.area_m2 if self.area_m2 is not None else polygon_area(self.polygon)
        return base * self.usable_fraction


@dataclass
class Gate:
    id: str
    name: str
    from_zone: str
    to_zone: str
    width_m: float
    stairs: bool = False
    bidirectional: bool = True
    open: bool = True
    line: List[Point] = field(default_factory=list)  # where it is: two points in local metres (map-drawn venues)

    @property
    def capacity_pps(self) -> float:
        return exit_flow_pps(self.width_m, self.stairs) if self.open else 0.0


@dataclass
class Venue:
    id: str
    name: str
    zones: Dict[str, Zone]
    gates: Dict[str, Gate]
    permitted_capacity: Optional[int] = None
    origin: Optional[Point] = None  # (lat, lon) of local (0, 0)
    languages: List[str] = field(default_factory=lambda: ["en"])
    incident_history: List[dict] = field(default_factory=list)

    def gates_of(self, zone_id: str) -> List[Gate]:
        return [g for g in self.gates.values() if zone_id in (g.from_zone, g.to_zone)]

    def exits_to_outside(self) -> List[Gate]:
        return [g for g in self.gates.values() if OUTSIDE in (g.from_zone, g.to_zone)]

    @property
    def total_area(self) -> float:
        return sum(z.usable_area for z in self.zones.values())

    def local_to_latlon(self, p: Point) -> Optional[Point]:
        """Convert local metres to (lat, lon) using an equirectangular approximation."""
        if not self.origin:
            return None
        lat0, lon0 = self.origin
        lat = lat0 + p[1] / 111_320.0
        lon = lon0 + p[0] / (111_320.0 * math.cos(math.radians(lat0)))
        return (round(lat, 7), round(lon, 7))

    # --- serialisation -------------------------------------------------
    @classmethod
    def from_dict(cls, d: dict) -> "Venue":
        zones = {}
        for z in d["zones"]:
            th = z.get("thresholds") or {}
            zones[z["id"]] = Zone(
                id=z["id"],
                name=z.get("name", z["id"]),
                polygon=[tuple(p) for p in z.get("polygon", [])],
                area_m2=z.get("area_m2"),
                usable_fraction=z.get("usable_fraction", 1.0),
                terrain=list(z.get("terrain", [])),
                thresholds=DensityThresholds(**th),
            )
        gates = {}
        for g in d.get("gates", []):
            gates[g["id"]] = Gate(
                id=g["id"],
                name=g.get("name", g["id"]),
                from_zone=g["from"],
                to_zone=g["to"],
                width_m=float(g["width_m"]),
                stairs=bool(g.get("stairs", False)),
                bidirectional=bool(g.get("bidirectional", True)),
                open=bool(g.get("open", True)),
                line=[tuple(p) for p in g.get("line", [])],
            )
        for g in gates.values():
            for zid in (g.from_zone, g.to_zone):
                if zid != OUTSIDE and zid not in zones:
                    raise ValueError(f"gate {g.id} references unknown zone {zid!r}")
        origin = d.get("origin")
        return cls(
            id=d["id"],
            name=d.get("name", d["id"]),
            zones=zones,
            gates=gates,
            permitted_capacity=d.get("permitted_capacity"),
            origin=tuple(origin) if origin else None,
            languages=d.get("languages", ["en"]),
            incident_history=d.get("incident_history", []),
        )

    @classmethod
    def load(cls, path) -> "Venue":
        return cls.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "permitted_capacity": self.permitted_capacity,
            "origin": list(self.origin) if self.origin else None,
            "languages": self.languages,
            "incident_history": self.incident_history,
            "zones": [
                {
                    "id": z.id,
                    "name": z.name,
                    "polygon": [list(p) for p in z.polygon],
                    # The raw area: usable_fraction is applied on load, so writing the usable area
                    # here would apply it twice on every save/load round trip.
                    "area_m2": round(z.area_m2 if z.area_m2 is not None else polygon_area(z.polygon), 2),
                    "usable_area_m2": round(z.usable_area, 2),
                    "usable_fraction": z.usable_fraction,
                    "terrain": z.terrain,
                    "thresholds": z.thresholds.__dict__,
                }
                for z in self.zones.values()
            ],
            "gates": [
                {
                    "id": g.id,
                    "name": g.name,
                    "from": g.from_zone,
                    "to": g.to_zone,
                    "width_m": g.width_m,
                    "stairs": g.stairs,
                    "bidirectional": g.bidirectional,
                    "open": g.open,
                    "capacity_pps": round(g.capacity_pps, 3),
                    **({"line": [list(p) for p in g.line]} if g.line else {}),
                }
                for g in self.gates.values()
            ],
        }


DATA_DIR = Path(__file__).resolve().parent / "data"


def default_venue() -> Venue:
    """The fictional demo pandal used by `roomtobreathe demo` and the tests."""
    return Venue.load(DATA_DIR / "venues" / "pandal.json")
