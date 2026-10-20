"""Edge pipeline: camera -> density + motion per zone -> POST numbers to server.

Privacy by design: frames never leave the device. Only aggregate numbers per
zone are sent (a few hundred bytes per second instead of megabits of video).
There is no face recognition and no tracking of individuals.
"""

from __future__ import annotations

import json
import time
import urllib.request
from typing import List, Optional

import cv2
import numpy as np

from .calibration import Calibration
from .density import load_estimator
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


def post(server: str, observations: List[dict], token: Optional[str] = None, timeout: float = 3.0) -> bool:
    req = urllib.request.Request(
        server.rstrip("/") + "/api/observations",
        data=json.dumps(observations).encode(),
        headers={"Content-Type": "application/json",
                 **({"Authorization": f"Bearer {token}"} if token else {})},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return 200 <= r.status < 300
    except OSError as e:
        print(f"[edge] post failed: {e}")
        return False


def run_edge(source: str, calibration: str, server: str, model: Optional[str] = None,
             every_s: float = 1.0, token: Optional[str] = None, show: bool = False) -> int:
    calib = Calibration.load(calibration)
    est = load_estimator(model)
    motion = MotionAnalyzer(calib)
    cap = cv2.VideoCapture(int(source) if source.isdigit() else source)
    if not cap.isOpened():
        print(f"[edge] cannot open source {source!r}")
        return 1
    print(f"[edge] {calib.camera_id}: estimator={est.name}, zones={list(calib.zones)}")
    backlog: List[dict] = []
    last = 0.0
    while True:
        ok, frame = cap.read()
        if not ok:
            print("[edge] stream ended or dropped; retrying in 2s")
            time.sleep(2)
            cap.release()
            cap = cv2.VideoCapture(int(source) if source.isdigit() else source)
            continue
        now = time.time()
        mot = motion.update(frame, now)
        if now - last < every_s:
            continue
        last = now
        dm = est.density_map(frame)
        obs = zone_observations(calib, dm, mot, est.relative_sigma, calib.camera_id, now)
        # Store and forward: keep a small backlog if the network drops (networks fail at crowds).
        backlog = (backlog + obs)[-600:]
        if post(server, backlog, token):
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
                cv2.imshow("crowdsafe-edge (local only)", vis)
                if cv2.waitKey(1) == 27:
                    break
            except cv2.error:
                print("[edge] --show needs opencv-python (with GUI), not the headless build")
                show = False
    return 0
