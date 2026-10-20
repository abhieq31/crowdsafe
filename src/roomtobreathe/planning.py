"""Before-the-event planning: safe capacity and permit risk assessment.

This is the highest-impact, lowest-cost part of Room to Breathe. It needs no
cameras. It encodes the structural lessons of past disasters, for example
Hathras 2024: about 250,000 people attended a site permitted for 80,000, with
roughly 80 police and a single congested exit.

The risk assessment follows the five-stage framework of Singh & Kishore (2025),
IJFMTS 10(1):3-8: decision -> approval -> risk assessment -> integrated plan
-> debrief. The numeric weights are transparent *heuristics*, not a legal
standard. Every authority should tune them, and every finding explains itself.

Keep in sync with ``web/js/planning.js``. Both implementations are checked
against ``tests/vectors/planning.json``.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import List, Optional, Sequence

from .physics import FLOW_LEVEL_PPM_PER_MIN, FLOW_STAIRS_PPM_PER_MIN

DEFAULT_PLANNING_DENSITY = 2.0  # p/m2, standing crowd with room to move
DEFAULT_EVAC_TARGET_S = 480.0  # 8 minutes, a common egress design target


@dataclass
class Exit:
    width_m: float
    stairs: bool = False

    @property
    def flow_pps(self) -> float:
        rate = FLOW_STAIRS_PPM_PER_MIN if self.stairs else FLOW_LEVEL_PPM_PER_MIN
        return max(0.0, self.width_m) * rate / 60.0


@dataclass
class CapacityReport:
    usable_area_m2: float
    planning_density: float
    area_capacity: int
    egress_capacity: int
    egress_capacity_largest_blocked: int
    safe_capacity: int
    limiting_factor: str
    evac_time_at_safe_capacity_s: Optional[float]
    findings: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def compute_capacity(
    usable_area_m2: float,
    exits: Sequence[Exit],
    planning_density: float = DEFAULT_PLANNING_DENSITY,
    evac_target_s: float = DEFAULT_EVAC_TARGET_S,
) -> CapacityReport:
    """Safe capacity is the lower of what the ground holds and what the exits drain.

    Like the Green Guide, we assume the largest exit may be unavailable (blocked
    by fire, a vehicle, or the incident itself), so the egress limit uses the
    remaining exits.
    """
    findings: List[str] = []
    area_cap = int(max(0.0, usable_area_m2) * planning_density)
    flows = sorted((e.flow_pps for e in exits), reverse=True)
    total_flow = sum(flows)
    egress_cap = int(total_flow * evac_target_s)
    reduced_flow = sum(flows[1:]) if len(flows) > 1 else 0.0
    egress_blocked_cap = int(reduced_flow * evac_target_s)

    if not exits:
        findings.append("No exits defined: the venue cannot be evacuated.")
    elif len(exits) == 1:
        findings.append(
            "Single exit: if it is blocked nobody can leave. Add at least one more "
            "independent exit, on a different side of the venue."
        )

    candidates = [(area_cap, "area"), (egress_blocked_cap, "exits (largest blocked)")]
    safe_cap, limiting = min(candidates, key=lambda c: c[0])
    if len(exits) == 1:
        # With a single exit the "largest blocked" rule gives zero. Report the
        # unblocked egress figure but flag it loudly instead of returning 0.
        safe_cap, limiting = min(
            [(area_cap, "area"), (egress_cap, "single exit (no redundancy)")],
            key=lambda c: c[0],
        )

    if limiting.startswith("exits") or limiting.startswith("single"):
        findings.append(
            f"Exits limit capacity to {safe_cap:,} although the ground holds "
            f"{area_cap:,}. Widening exits raises the safe capacity."
        )
    evac = (safe_cap / total_flow) if total_flow > 0 else None
    return CapacityReport(
        usable_area_m2=round(usable_area_m2, 2),
        planning_density=planning_density,
        area_capacity=area_cap,
        egress_capacity=egress_cap,
        egress_capacity_largest_blocked=egress_blocked_cap,
        safe_capacity=safe_cap,
        limiting_factor=limiting,
        evac_time_at_safe_capacity_s=round(evac, 1) if evac is not None else None,
        findings=findings,
    )


def capacity_for_venue(venue, **kw) -> CapacityReport:
    from .venue import OUTSIDE

    exits = [
        Exit(g.width_m, g.stairs)
        for g in venue.gates.values()
        if OUTSIDE in (g.from_zone, g.to_zone)
    ]
    return compute_capacity(venue.total_area, exits, **kw)


# ---------------------------------------------------------------------------
# Permit risk assessment
# ---------------------------------------------------------------------------

EVENT_TYPES = ("religious", "entertainment", "political", "sports", "other")


@dataclass
class EventProfile:
    event_type: str
    expected_attendance: int
    safe_capacity: int
    police: int = 0
    marshals: int = 0
    medical_posts: int = 0
    ambulances: int = 0
    exits: int = 1
    night: bool = False
    weather: str = "clear"  # clear | rain | heat | storm
    terrain: List[str] = field(default_factory=list)  # mud, slope, riverbank, steps, dead_end
    free_distribution: bool = False  # prasad, food, gifts, cash - a top trigger
    vip_or_celebrity: bool = False
    fixed_start_or_end: bool = True  # everyone arrives/leaves at once
    past_incidents_at_site: int = 0
    elderly_or_children_share: float = 0.0  # 0..1
    live_monitoring: bool = False


@dataclass
class Factor:
    code: str
    points: int
    finding: str
    recommendation: str


@dataclass
class RiskAssessment:
    score: int
    tier: str
    approve: str  # "approve" | "approve_with_conditions" | "do_not_approve"
    occupancy_ratio: float
    factors: List[Factor]

    def to_dict(self) -> dict:
        return {
            "score": self.score,
            "tier": self.tier,
            "approve": self.approve,
            "occupancy_ratio": self.occupancy_ratio,
            "factors": [asdict(f) for f in self.factors],
        }


def _per_thousand(n: int, attendance: int) -> float:
    return (n * 1000.0 / attendance) if attendance > 0 else float("inf")


def assess_risk(p: EventProfile) -> RiskAssessment:
    f: List[Factor] = []
    add = lambda code, pts, finding, rec: f.append(Factor(code, pts, finding, rec))  # noqa: E731

    ratio = (p.expected_attendance / p.safe_capacity) if p.safe_capacity > 0 else float("inf")
    blocker = False
    if ratio > 1.0:
        blocker = True
        add(
            "OVER_CAPACITY",
            40,
            f"Expected attendance is {ratio:.1f}x the safe capacity "
            f"({p.expected_attendance:,} vs {p.safe_capacity:,}).",
            "Reduce attendance with timed or ticketed entry, add a second venue or day, "
            "or enlarge the site and its exits. Do not approve at this size.",
        )
    elif ratio > 0.85:
        add(
            "NEAR_CAPACITY",
            15,
            f"Expected attendance is {ratio:.0%} of safe capacity.",
            "Use entry counting with a hard stop at 100% and have overflow holding areas ready.",
        )

    if p.event_type == "religious":
        add("RELIGIOUS", 8,
            "Religious gatherings account for ~79% of stampede deaths in India.",
            "Plan for long dwell times, mixed ages and sacred focal points.")
    elif p.event_type not in EVENT_TYPES:
        add("UNKNOWN_TYPE", 3, f"Unknown event type {p.event_type!r}.", "Classify the event.")

    police_pk = _per_thousand(p.police, p.expected_attendance)
    if police_pk < 1.0:
        add("LOW_POLICE", 12,
            f"Only {police_pk:.2f} police per 1,000 attendees.",
            "Increase deployment, or reduce attendance to match available personnel.")
    marshal_pk = _per_thousand(p.marshals, p.expected_attendance)
    if marshal_pk < 2.0:
        add("LOW_MARSHALS", 8,
            f"Only {marshal_pk:.2f} trained marshals/volunteers per 1,000 attendees.",
            "Recruit and brief volunteers, with at least one at every choke point and gate.")
    if p.medical_posts == 0:
        add("NO_MEDICAL", 8, "No medical post planned.",
            "Set up first-aid posts with triage tags and a casualty register.")
    if p.ambulances == 0 and p.expected_attendance >= 5000:
        add("NO_AMBULANCE", 5, "No ambulance on standby for a large crowd.",
            "Stage ambulances with clear, protected access routes.")

    if p.exits <= 1:
        add("SINGLE_EXIT", 15, "Only one exit.",
            "Add independent exits on different sides, and keep them unlocked and staffed.")
    if p.free_distribution:
        add("FREE_DISTRIBUTION", 12,
            "Free items (prasad, food, gifts) cause rushes and sudden surges.",
            "Distribute at several points, in queues, away from the main flow and away from exits.")
    if p.vip_or_celebrity:
        add("VIP", 8, "VIP or celebrity arrival concentrates the crowd.",
            "Announce the schedule in advance, use barriers, and never change the route at short notice.")
    if p.fixed_start_or_end:
        add("SURGE_TIMES", 5, "Everyone arrives or leaves at the same time.",
            "Stagger entry and exit, open all gates before the end, and hold back flow at entrances.")
    if p.night:
        add("NIGHT", 4, "Night-time event.", "Provide lighting with backup power at all paths and exits.")
    if p.weather in ("rain", "storm"):
        add("RAIN", 8, f"Weather: {p.weather}. Slips on mud and people crowding for shelter.",
            "Lay matting or gravel, and plan for sheltering and slower walking speeds.")
    elif p.weather == "heat":
        add("HEAT", 5, "Heat stress lowers tolerance to crowding.", "Provide water points and shade.")
    for t in p.terrain:
        if t in ("mud", "slope", "riverbank", "steps", "dead_end"):
            add(f"TERRAIN_{t.upper()}", 5, f"Hazardous terrain: {t}.",
                "Fix the ground, or remove that area from the usable area.")
    if p.past_incidents_at_site > 0:
        add("HISTORY", min(20, 10 * p.past_incidents_at_site),
            f"{p.past_incidents_at_site} previous crowd incident(s) at this site.",
            "Repeat incidents happen at the same sites. Review the earlier after-action reports.")
    if p.elderly_or_children_share >= 0.3:
        add("VULNERABLE", 5, "Large share of elderly people or children.",
            "Use lower planning densities and give vulnerable groups separate queues.")
    if not p.live_monitoring and p.expected_attendance >= 10000:
        add("NO_MONITORING", 6, "No live density monitoring for a large crowd.",
            "Deploy Room to Breathe live monitoring (Tier 1+) or at least gate counting (Tier 0).")

    score = min(100, sum(x.points for x in f))
    if score >= 60:
        tier = "SEVERE"
    elif score >= 40:
        tier = "HIGH"
    elif score >= 20:
        tier = "MODERATE"
    else:
        tier = "LOW"
    if blocker:
        approve = "do_not_approve"
    elif tier in ("HIGH", "SEVERE"):
        approve = "approve_with_conditions"
    else:
        approve = "approve"
    f.sort(key=lambda x: -x.points)
    return RiskAssessment(score, tier, approve, round(ratio, 3) if ratio != float("inf") else -1.0, f)
