"""Emergency access: which hospitals, how fast, and an ambulance route that stays clear of the crowd.

Crush injuries need emergency care within minutes. The plan therefore names the receiving hospitals,
their drive times *with event traffic*, and an **emergency corridor**: a route from the best hospital
to an ambulance gate that avoids the streets the departing crowd fills. Where the corridor still has to
share a street with the crowd, the plan says where to keep a lane coned off.

OSM rarely records which hospitals take trauma cases, so hospitals are ranked by a transparent guess
(``network.hospital_score``) and the plan asks the health department to confirm.
"""

from __future__ import annotations

import math
from typing import Dict, List, Optional, Tuple

from .dispersal import Access, Phase, los
from .network import StreetNetwork, hospital_score
from .traffic import TrafficPhase, background_vph, bpr_time

CROWD_PENALTY = 4.0  # how strongly the corridor avoids crowded streets


AMBULANCE_MAX_VC = 1.3  # with sirens, an ambulance gets through queues faster than ordinary traffic


def _drive_time_cost(tp: Optional[TrafficPhase]):
    return lambda e: bpr_time(e, background_vph(e) + (tp.event_vph(e.id) if tp else 0.0), AMBULANCE_MAX_VC)


def hospitals(net: StreetNetwork, access_node: int, tp: Optional[TrafficPhase], limit: int = 3) -> List[dict]:
    """Best receiving hospitals, by suitability then drive time from the venue (with event traffic)."""
    dist, _ = net.shortest_paths(access_node, _drive_time_cost(tp), "drive")
    cands = []
    for p in net.pois:
        if p.kind not in ("hospital", "clinic"):
            continue
        node = net.nearest_node(p.x, p.y, "drive", 400)
        t = dist.get(node, math.inf) if node is not None else math.inf
        if t == math.inf:
            continue
        cands.append((hospital_score(p), t, p, node))
    cands.sort(key=lambda c: (-(c[0] > 0), c[1] if c[0] > 0 else c[1] + 3600))
    out: List[dict] = []
    for score, t, p, node in cands:
        if any(math.hypot(p.x - q["x"], p.y - q["y"]) < 400 for q in out):  # one campus, many buildings
            continue
        out.append(
            {
                "name": p.label,
                "x": p.x,
                "y": p.y,
                "node": node,
                "drive_min": round(t / 60, 1),
                "suitability": "likely emergency care" if score > 0 else "unknown or specialist",
                "tags_emergency": p.tags.get("emergency"),
            }
        )
        if len(out) == limit:
            break
    return out


def ambulance_gate(net: StreetNetwork, accesses: List[Access], ped: Phase) -> Optional[Tuple[Access, int]]:
    """The venue gate ambulances should use: one the departing crowd does not use (emergency or quiet)."""
    best = None
    for a in accesses:
        node = net.nearest_node(a.x, a.y, "drive", 200)
        if node is None:
            continue
        crowd = ped.by_gate.get(a.gate_id, 0.0)
        score = (
            0 if a.kind == "emergency" else 1,
            crowd,
            math.hypot(net.nodes[node][0] - a.x, net.nodes[node][1] - a.y),
        )
        if best is None or score < best[0]:
            best = (score, a, node)
    return (best[1], best[2]) if best else None


def corridor(
    net: StreetNetwork,
    hospital: dict,
    gate: Access,
    gate_node: int,
    ped: Phase,
    tp: Optional[TrafficPhase],
    public_level: float = 1.0,
) -> dict:
    """Hospital -> ambulance gate, avoiding crowded streets; and how it shares streets with the crowd."""
    base = _drive_time_cost(tp)

    def crowd_load(eid: int) -> float:
        e = net.edges[eid]
        return ped.peak_event_ppm(eid) / max(1.0, e.ped_capacity_ppm())

    def cost(e) -> float:
        return base(e) * (1 + CROWD_PENALTY * min(1.5, crowd_load(e.id)))

    dist, prev = net.shortest_paths(hospital["node"], cost, "drive")
    if dist.get(gate_node, math.inf) == math.inf:
        return {"found": False, "hospital": hospital["name"], "gate": gate.gate_name}
    path = net.path_edges(prev, gate_node)
    shared = []
    for eid in path:
        e = net.edges[eid]
        per_m = ped.peak_event_ppm(eid) / max(0.3, e.effective_walk_width())
        if per_m >= 23:  # beyond level of service A: the crowd will spill into the road
            x, y = net.edge_mid(eid)
            shared.append(
                {"edge": eid, "street": net.describe(eid), "x": x, "y": y, "los": los(per_m), "length": e.length}
            )
    by_street: Dict[str, dict] = {}
    for s in shared:
        cur = by_street.setdefault(
            s["street"], {"street": s["street"], "x": s["x"], "y": s["y"], "length_m": 0.0, "los": s["los"]}
        )
        cur["length_m"] += s["length"]
        cur["los"] = max(cur["los"], s["los"])
    via = net.via(path)
    return {
        "found": True,
        "hospital": hospital["name"],
        "gate": gate.gate_name,
        "gate_id": gate.gate_id,
        "edges": path,
        "via": via,
        "length_m": round(sum(net.edges[e].length for e in path)),
        "drive_min": round(sum(base(net.edges[e]) for e in path) / 60, 1),
        "shared_with_crowd": [dict(v, length_m=round(v["length_m"])) for v in by_street.values()],
    }


def nearest_service(net: StreetNetwork, kind: str, access_node: int, tp: Optional[TrafficPhase]) -> Optional[dict]:
    dist, _ = net.shortest_paths(access_node, _drive_time_cost(tp), "drive", reverse=True)  # service -> venue
    best = None
    for p in net.pois:
        if p.kind != kind:
            continue
        node = net.nearest_node(p.x, p.y, "drive", 400)
        t = dist.get(node, math.inf) if node is not None else math.inf
        if t < math.inf and (best is None or t < best[0]):
            best = (t, p)
    if not best:
        return None
    return {"name": best[1].label, "x": best[1].x, "y": best[1].y, "drive_min": round(best[0] / 60, 1)}
