"""Edge pipeline: camera -> density + motion per zone -> POST numbers to server.

Privacy by design: frames never leave the device. Only aggregate numbers per
zone are sent (a few hundred bytes per second instead of megabits of video).
There is no face recognition and no tracking of individuals.
"""

from __future__ import annotations

import json
import math
import time
import urllib.error
import urllib.request
from typing import List, Optional

import cv2
import numpy as np

from ..zones import STALE_AFTER_S
from .calibration import Calibration
from .density import OnnxDensityModel, load_estimator
from .motion import MotionAnalyzer


def zone_observations(calib: Calibration, density_map: np.ndarray, motion: dict,
                      relative_sigma: float, source: str, ts: float) -> List[dict]:
    h, w = density_map.shape
    out = []
    for zid, zm in calib.zones.items():
        mask = calib.mask(zid, h, w)
        count = float(density_map[mask].sum())
        dens = count / zm.ground_area_m2 if zm.ground_area_m2 > 0 else None
        o = {"zone_id": zid, "source": source, "ts": ts, "count": round(count, 1)}
        if dens is not None:
            o["density"] = round(dens, 3)
            o["density_sigma"] = round(max(0.2, relative_sigma * dens), 3)
        o.update(motion.get(zid, {}))
        out.append(o)
    return out


# The server's limits (server/app.py ObservationIn). One value outside them makes it reject the whole batch.
MAX_DENSITY = 20.0  # p/m2: more than this is physically impossible, so the calibration is wrong
MAX_SPEED = 15.0  # m/s: a camera that moved, a scene cut, or a car, not people walking


def sanitize(o: dict) -> dict:
    """Drop values the server would refuse, so one bad zone never blocks the others."""
    o = {k: v for k, v in o.items() if not (isinstance(v, float) and not math.isfinite(v))}
    if o.get("density", 0) > MAX_DENSITY:
        _warn_once(f"density:{o['zone_id']}",
                   f"[edge] WARNING: zone '{o['zone_id']}' reads {o['density']:.0f} people/m2, which is impossible. "
                   "Its calibration is probably wrong (ground points must be in metres). Density not sent.")
        o.pop("density", None)
        o.pop("density_sigma", None)
    if o.get("mean_speed", 0) > MAX_SPEED:
        o.pop("mean_speed", None)  # motion for this one report is meaningless
        o.pop("velocity_variance", None)
    return o


def post(server: str, observations: List[dict], token: Optional[str] = None, timeout: float = 3.0) -> str:
    """Send observations. Returns "ok", "retry" (network or server down: keep them) or "drop"."""
    req = urllib.request.Request(
        server.rstrip("/") + "/api/observations",
        data=json.dumps([{**o, "sent_at": time.time()} for o in observations]).encode(),
        headers={"Content-Type": "application/json",
                 **({"Authorization": f"Bearer {token}"} if token else {})},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = r.read()
    except urllib.error.HTTPError as e:
        if e.code == 401:
            _warn_once("401", "[edge] the server refused the access token. Check --token; reports are kept.")
            return "retry"
        if 400 <= e.code < 500:
            # The server will never accept this batch, so retrying it would block every later report.
            print(f"[edge] the server rejected {len(observations)} observations (HTTP {e.code}); dropping them")
            return "drop"
        print(f"[edge] post failed: {e}")
        return "retry"
    except OSError as e:
        print(f"[edge] post failed: {e}")
        return "retry"
    try:
        rejected = json.loads(body or b"{}").get("rejected", 0)
    except (ValueError, AttributeError):
        rejected = 0
    if rejected:
        # The server drops observations for zones it does not know, so a typo here means silence.
        zones = sorted({o["zone_id"] for o in observations})
        _warn_once("rejected", f"[edge] WARNING: the server ignored {rejected} observations. Every zone_id in the "
                   f"camera calibration ({', '.join(zones)}) must match a zone in the server's venue file.")
    return "ok"


def _warn_once(key: str, msg: str) -> None:
    if key not in _warned:
        _warned.add(key)
        print(msg)


_warned: set = set()


def report_interval(seconds_per_frame: float, requested_s: float) -> float:
    """Seconds between reports: the requested interval, or longer if inference would not fit.

    Inference may use 60% of the interval, leaving time for motion analysis and sending.
    """
    return max(requested_s, math.ceil(seconds_per_frame / 0.6 * 10) / 10)


def _open(source: str):
    return cv2.VideoCapture(int(source) if source.isdigit() else source)


def run_edge(source: str, calibration: str, server: str, model: Optional[str] = None,
             every_s: float = 1.0, token: Optional[str] = None, show: bool = False,
             input_width: Optional[int] = None) -> int:
    """Run the camera loop. ``model`` is an ONNX path (None = HOG fallback).

    The model input width is never lowered automatically: on ShanghaiTech A, 768 px instead
    of 1024 px doubles the undercount of the densest scenes, from -8% to -16% (docs/vision.md). If the device is too
    slow for ``every_s``, it reports less often instead, and says so.
    """
    requested_every = every_s
    calib = Calibration.load(calibration)
    est = load_estimator(model)
    motion = MotionAnalyzer(calib)
    cap = _open(source)
    ok, frame = cap.read() if cap.isOpened() else (False, None)
    if not ok:
        print(f"[edge] cannot read from camera {source!r}. Check the address, that the camera is on, "
              "and that this device is on the same network. `roomtobreathe check` tests this.")
        return 1
    if isinstance(est, OnnxDensityModel):
        if input_width:
            est.input_width = input_width
            if input_width < 1024:
                print(f"[edge] WARNING: input width {input_width}px undercounts dense crowds (docs/vision.md)")
        print("[edge] measuring this device's speed ...")
        secs = est.seconds_per_frame(frame)
        every_s = report_interval(secs, every_s)
        print(f"[edge] model input width {est.input_width}px, {secs:.2f}s per frame")
        if every_s > requested_every:
            print(f"[edge] this device is too slow to report every {requested_every:g}s, so it will report "
                  f"every {every_s:g}s. Warnings arrive a little later; a GPU or a faster device fixes this.")
        if every_s >= STALE_AFTER_S:
            print(f"[edge] WARNING: reports every {every_s:g}s are slower than the control room's {STALE_AFTER_S:g}s "
                  "no-data limit, so these zones will flicker to UNKNOWN (no data). "
                  "Use a faster device or one with a GPU.")
    else:
        print("[edge] WARNING: no density model, using the HOG fallback. It badly undercounts above "
              "~1 person/m2 and must not be relied on for dense crowds. Run:  roomtobreathe model fetch")
    print(f"[edge] {calib.camera_id}: estimator={est.name}, zones={list(calib.zones)}, every {every_s:g}s")
    backlog: List[dict] = []
    last = 0.0
    while True:
        ok, frame = cap.read()
        if not ok:
            print("[edge] stream ended or dropped; retrying in 2s")
            time.sleep(2)
            cap.release()
            cap = _open(source)
            continue
        now = time.time()
        mot = motion.update(frame, now)
        if now - last < every_s:
            continue
        last = now
        dm = est.density_map(frame)
        obs = [sanitize(o) for o in zone_observations(calib, dm, mot, est.relative_sigma, calib.camera_id, now)]
        # Store and forward: keep a small backlog if the network drops (networks fail at crowds).
        backlog = (backlog + obs)[-600:]
        if post(server, backlog, token) != "retry":
            backlog = []
        if show:
            vis = frame.copy()
            heat = cv2.applyColorMap(np.uint8(255 * dm / (dm.max() + 1e-6)), cv2.COLORMAP_JET)
            vis = cv2.addWeighted(vis, 0.6, heat, 0.4, 0)
            for o in obs:
                poly = calib.zones[o["zone_id"]].image_polygon.astype(np.int32)
                cv2.polylines(vis, [poly], True, (255, 255, 255), 2)
                cv2.putText(vis, f"{o['zone_id']}: {o.get('density', 0):.1f}/m2", tuple(poly[0]),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 255, 255), 2)
            try:
                cv2.imshow("roomtobreathe-edge (local only)", vis)
                if cv2.waitKey(1) == 27:
                    break
            except cv2.error:
                print("[edge] --show needs opencv-python (with GUI), not the headless build")
                show = False
    return 0
