"""Command-line interface.

    crowdsafe demo                      # dashboard + simulated surge at http://localhost:8000
    crowdsafe serve --venue v.json      # live mode: edge devices POST observations
    crowdsafe venue new                 # describe the venue in plain questions -> venue.json
    crowdsafe capacity --area 960 --exit 3 --exit 2.5:stairs
    crowdsafe simulate --scenario pandal_surge [--respond]
    crowdsafe model fetch               # download the default density model (once, checksum verified)
    crowdsafe setup                     # once per camera device: questions, each answer tested, then saved
    crowdsafe check                     # test the saved setup (flags override it)
    crowdsafe edge                      # run the camera pipeline with the saved setup
    crowdsafe edge --source rtsp://... --calibration cam.json --server http://...
"""

from __future__ import annotations

import argparse
import json
import os
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
    if not demo:
        from .venue_wizard import venue_problems

        if not a.venue:
            print("No --venue given, so the fictional demo venue is used. Create yours with:  crowdsafe venue new")
        for prob in venue_problems(venue):
            print(f"  ! {prob}")
    log_dir = a.log_dir or os.environ.get("CROWDSAFE_LOG_DIR") or (None if demo else "crowdsafe-logs")
    app = create_app(venue, log_dir=log_dir)
    if log_dir:
        print(f"Event record (alerts, approved actions): {os.path.abspath(log_dir)}")
    if demo:
        app.state.engine.start_demo(a.scenario)
    print(f"CrowdSafe dashboard: http://{a.host}:{a.port}/")
    if a.host in ("0.0.0.0", "::"):
        ip = _lan_ip()
        if ip:
            print(f"On other devices (phones, camera devices) use:  http://{ip}:{a.port}/")
    elif not demo:
        print("Only this computer can connect. To let camera devices and phones in, add  --host 0.0.0.0")
    uvicorn.run(app, host=a.host, port=a.port, log_level="warning")
    return 0


EDGE_DEFAULTS = {"server": "http://127.0.0.1:8000", "model": "auto", "every": 1.0}


def _settings(a):
    """Flags, else the setup saved by ``crowdsafe setup``, else defaults. None if the saved file is damaged."""
    from . import config

    try:
        saved = config.load()
    except config.ConfigError as e:
        print(e, file=sys.stderr)
        return None
    if saved:
        print(f"Using the saved setup from {config.config_path()} (options you type override it)",
              file=sys.stderr)
    return config.merge(a, saved, EDGE_DEFAULTS)


def _lan_ip():
    """This computer's address on the local network (no packet is sent)."""
    import socket

    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.255.255.255", 1))
            return s.getsockname()[0]
    except OSError:
        return None


def cmd_edge(a) -> int:
    s = _settings(a)
    if s is None:
        return 1
    if not s["source"] or not s["calibration"]:
        print("No camera or calibration file given. Run  crowdsafe setup  once, or pass --source and "
              "--calibration.", file=sys.stderr)
        return 2
    try:
        from .vision.pipeline import run_edge
    except ImportError as e:
        print(f"Vision extras are missing ({e}). Install them with:  pip install 'crowdsafe[vision,onnx]'",
              file=sys.stderr)
        return 1
    from .vision import model_store

    try:
        import onnxruntime  # noqa: F401
    except ImportError:
        if (s["model"] or "auto").lower() != "hog":
            print("The model runtime is missing, so dense crowds cannot be counted. Install it with:\n"
                  "  pip install 'crowdsafe[onnx]'\nor pass --model hog to test with the inaccurate fallback.",
                  file=sys.stderr)
            return 1
    try:
        model = model_store.resolve(s["model"])
    except model_store.FetchError as e:
        print(e, file=sys.stderr)
        return 1
    return run_edge(s["source"], s["calibration"], s["server"], model=model, every_s=float(s["every"]),
                    token=s["token"], show=a.show, input_width=a.input_width or None)


def cmd_venue(a) -> int:
    from .venue import Venue
    from .venue_wizard import run_venue_new, summarise

    if a.action == "new":
        return run_venue_new(a.file or "venue.json", force=a.force)
    if not a.file:
        print("Give the venue file:  crowdsafe venue check my_venue.json", file=sys.stderr)
        return 2
    try:
        venue = Venue.load(a.file)
    except (OSError, ValueError, KeyError) as e:
        print(f"Cannot read {a.file}: {e}", file=sys.stderr)
        return 1
    print(venue.name)
    return 1 if summarise(venue) else 0


def cmd_check(a) -> int:
    from .check import run_check

    s = _settings(a)
    if s is None:
        return 1
    flags = any(getattr(a, k) is not None for k in ("source", "calibration", "server", "model", "every", "token"))
    hint = "crowdsafe edge with the same options" if flags else "crowdsafe edge"
    return run_check(s["source"], s["calibration"], s["server"], model=s["model"], token=s["token"],
                     every_s=float(s["every"]), start_hint=hint)


def cmd_setup(a) -> int:
    from .wizard import run_setup

    return run_setup()


def cmd_model(a) -> int:
    from .vision import model_store

    if a.action == "fetch":
        names = list(model_store.MODELS) if a.name == "all" else [a.name]
        try:
            for n in names:
                print(model_store.fetch(n))
        except model_store.FetchError as e:
            print(e, file=sys.stderr)
            return 1
        return 0
    print(f"Models folder: {model_store.models_dir()}")
    for n, info in model_store.MODELS.items():
        state = "downloaded" if model_store.is_installed(n) else "not downloaded"
        print(f"  {n:<7} {state:<15} {info.about}")
    print("Licence: MIT (DM-Count code and weights). Accuracy: docs/vision.md")
    return 0


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
        sv.add_argument("--log-dir", help="folder for the event record (serve default: ./crowdsafe-logs)")

    model_help = ("density model: auto (default: the dense model, downloaded on first use), dense, sparse, "
                  "hog (inaccurate test fallback), or a path to your own .onnx")
    sub.add_parser("setup", help="answer a few questions once per camera device; edge and check then need no options")
    saved = " (default: the answer saved by crowdsafe setup)"
    for name in ("edge", "check"):
        is_edge = name == "edge"
        ed = sub.add_parser(name, help="run the camera pipeline on an edge device" if is_edge
                            else "test camera, calibration, model, speed and server before an event")
        ed.add_argument("--source", help="RTSP URL, video file, or webcam index" + saved)
        ed.add_argument("--calibration", help="camera calibration JSON" + saved)
        ed.add_argument("--server", help="control-room address" + saved + ", else http://127.0.0.1:8000")
        ed.add_argument("--model", help=model_help)
        ed.add_argument("--every", type=float, help="seconds between reports (default 1)")
        ed.add_argument("--token", help="access token" + saved)
        if is_edge:
            ed.add_argument("--input-width", type=int, default=0,
                            help="model input width in px (default 1024); smaller is faster but undercounts")
            ed.add_argument("--show", action="store_true", help="show a local debug window")

    vn = sub.add_parser("venue", help="create a venue file by answering questions, or check one")
    vn.add_argument("action", choices=["new", "check"])
    vn.add_argument("file", nargs="?", help="venue JSON (new: default venue.json)")
    vn.add_argument("--force", action="store_true", help="replace an existing file")

    m = sub.add_parser("model", help="download or list the density models")
    m.add_argument("action", choices=["fetch", "list"])
    m.add_argument("name", nargs="?", default="dense", choices=["dense", "sparse", "all"])

    a = p.parse_args(argv)
    if a.cmd == "capacity":
        return cmd_capacity(a)
    if a.cmd == "simulate":
        return cmd_simulate(a)
    if a.cmd in ("serve", "demo"):
        return cmd_serve(a, demo=a.cmd == "demo")
    if a.cmd == "edge":
        return cmd_edge(a)
    if a.cmd == "check":
        return cmd_check(a)
    if a.cmd == "model":
        return cmd_model(a)
    if a.cmd == "venue":
        return cmd_venue(a)
    if a.cmd == "setup":
        return cmd_setup(a)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
