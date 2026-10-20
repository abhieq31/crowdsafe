"""Command-line interface.

    crowdsafe demo                      # dashboard + simulated surge at http://localhost:8000
    crowdsafe serve --venue v.json      # live mode: edge devices POST observations
    crowdsafe capacity --area 960 --exit 3 --exit 2.5:stairs
    crowdsafe simulate --scenario pandal_surge [--respond]
    crowdsafe edge --source rtsp://... --calibration cam.json --server http://...
"""

from __future__ import annotations

import argparse
import json
import sys

from .planning import Exit, compute_capacity


def _exit(spec: str) -> Exit:
    width, _, kind = spec.partition(":")
    return Exit(float(width), stairs=(kind == "stairs"))


def cmd_capacity(a) -> int:
    rep = compute_capacity(a.area, [_exit(e) for e in a.exit], a.density, a.evac_minutes * 60)
    if a.json:
        print(json.dumps(rep.to_dict(), indent=2))
        return 0
    print(f"Usable area           {rep.usable_area_m2:,.0f} m2 @ {rep.planning_density} p/m2")
    print(f"Ground holds          {rep.area_capacity:,}")
    print(f"Exits drain (all)     {rep.egress_capacity:,} in {a.evac_minutes:g} min")
    print(f"Exits drain (largest blocked) {rep.egress_capacity_largest_blocked:,}")
    print(f"SAFE CAPACITY         {rep.safe_capacity:,}   (limited by {rep.limiting_factor})")
    if rep.evac_time_at_safe_capacity_s:
        print(f"Evacuation time       {rep.evac_time_at_safe_capacity_s / 60:.1f} min at safe capacity")
    for f in rep.findings:
        print(f"  ! {f}")
    return 0


def cmd_simulate(a) -> int:
    from .engine import Engine
    from .venue import Venue, default_venue

    venue = Venue.load(a.venue) if a.venue else default_venue()
    e = Engine(venue)
    e.start_demo(a.scenario)
    responded = set()
    peak = {z: 0.0 for z in venue.zones}
    for _ in range(int(a.minutes * 60)):
        for al in e.tick():
            print(f"t={e.sim.t:5.0f}s  {al.zone_name:<24} {al.previous:>7} -> {al.level:<7} "
                  f"{'; '.join(al.reasons)}")
            if a.respond and al.level == "RED" and al.zone_id not in responded:
                responded.add(al.zone_id)  # an operator acts on each zone's first RED alert
                for act in al.actions:
                    e.execute(act, by="simulate --respond")
                print(f"           executed {len(al.actions)} playbook actions")
        for z in venue.zones:
            peak[z] = max(peak[z], e.sim.true_density(z))
    print("\npeak true density (p/m2): " + ", ".join(f"{z}={d:.2f}" for z, d in peak.items()))
    return 0


def cmd_serve(a, demo: bool = False) -> int:
    try:
        import uvicorn

        from .server.app import create_app
    except ImportError:
        print("Server extras are missing. Install them with:  pip install 'crowdsafe[server]'", file=sys.stderr)
        return 1
    from .venue import Venue, default_venue

    venue = Venue.load(a.venue) if a.venue else default_venue()
    app = create_app(venue)
    if demo:
        app.state.engine.start_demo(a.scenario)
    print(f"CrowdSafe dashboard: http://{a.host}:{a.port}/")
    uvicorn.run(app, host=a.host, port=a.port, log_level="warning")
    return 0


def cmd_edge(a) -> int:
    try:
        from .vision.pipeline import run_edge
    except ImportError as e:
        print(f"Vision extras are missing ({e}). Install them with:  pip install 'crowdsafe[vision]'",
              file=sys.stderr)
        return 1
    return run_edge(a.source, a.calibration, a.server, model=a.model, every_s=a.every,
                    token=a.token, show=a.show)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="crowdsafe", description="Open-source crowd-crush prevention")
    sub = p.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("capacity", help="safe capacity from area and exits")
    c.add_argument("--area", type=float, required=True, help="usable area in m2")
    c.add_argument("--exit", action="append", default=[], help="exit width in m, e.g. 3 or 2.5:stairs")
    c.add_argument("--density", type=float, default=2.0, help="planning density p/m2 (default 2)")
    c.add_argument("--evac-minutes", type=float, default=8.0)
    c.add_argument("--json", action="store_true")

    s = sub.add_parser("simulate", help="run a scenario headless and print alerts")
    s.add_argument("--scenario", default="pandal_surge")
    s.add_argument("--venue")
    s.add_argument("--minutes", type=float, default=35)
    s.add_argument("--respond", action="store_true", help="execute each zone's RED playbook automatically")

    for name in ("serve", "demo"):
        extra = " with a simulated crowd" if name == "demo" else ""
        sv = sub.add_parser(name, help="run the API + dashboard" + extra)
        sv.add_argument("--venue")
        sv.add_argument("--host", default="127.0.0.1")
        sv.add_argument("--port", type=int, default=8000)
        sv.add_argument("--scenario", default="pandal_surge")

    ed = sub.add_parser("edge", help="run the camera pipeline on an edge device")
    ed.add_argument("--source", required=True, help="RTSP URL, video file, or webcam index")
    ed.add_argument("--calibration", required=True, help="camera calibration JSON")
    ed.add_argument("--server", default="http://127.0.0.1:8000")
    ed.add_argument("--model", help="ONNX density-map model (optional)")
    ed.add_argument("--every", type=float, default=1.0, help="seconds between reports")
    ed.add_argument("--token")
    ed.add_argument("--show", action="store_true", help="show a local debug window")

    a = p.parse_args(argv)
    if a.cmd == "capacity":
        return cmd_capacity(a)
    if a.cmd == "simulate":
        return cmd_simulate(a)
    if a.cmd in ("serve", "demo"):
        return cmd_serve(a, demo=a.cmd == "demo")
    if a.cmd == "edge":
        return cmd_edge(a)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
