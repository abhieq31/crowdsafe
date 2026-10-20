"""Crowd motion from dense optical flow, measured in metres on the ground.

Outputs per zone:
- ``mean_speed``: average walking speed (m/s). When it collapses at high
  density, the crowd is jammed.
- ``velocity_variance``: trace of the velocity covariance (m^2/s^2). Combined
  with density this gives Helbing's *crowd pressure*, the early signature of
  turbulence. Stop-and-go waves show up in the ``mean_speed`` time series,
  which the server analyses.
"""

from __future__ import annotations

from typing import Dict, Optional

import cv2
import numpy as np

from .calibration import Calibration


class MotionAnalyzer:
    def __init__(self, calib: Calibration, stride: int = 8, max_width: int = 640):
        self.calib = calib
        self.stride = stride
        self.max_width = max_width
        self.prev: Optional[np.ndarray] = None
        self.prev_t: Optional[float] = None
        self._masks: Dict[str, np.ndarray] = {}

    def update(self, frame_bgr: np.ndarray, t: float) -> Dict[str, dict]:
        h, w = frame_bgr.shape[:2]
        scale = min(1.0, self.max_width / w)
        gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)
        if scale < 1.0:
            gray = cv2.resize(gray, (int(w * scale), int(h * scale)))
        out: Dict[str, dict] = {}
        if self.prev is not None and self.prev.shape == gray.shape and t > self.prev_t:
            dt = t - self.prev_t
            flow = cv2.calcOpticalFlowFarneback(self.prev, gray, None, 0.5, 3, 15, 3, 5, 1.2, 0)
            gh, gw = gray.shape
            ys, xs = np.mgrid[0:gh:self.stride, 0:gw:self.stride]
            fx = flow[ys, xs, 0]
            fy = flow[ys, xs, 1]
            p0 = np.stack([xs, ys], -1).reshape(-1, 2).astype(np.float32) / scale
            p1 = (np.stack([xs + fx, ys + fy], -1).reshape(-1, 2)).astype(np.float32) / scale
            g0 = self.calib.to_ground(p0)
            g1 = self.calib.to_ground(p1)
            vel = (g1 - g0) / dt  # m/s, shape (N, 2)
            px = np.clip(p0[:, 0].astype(int), 0, w - 1)
            py = np.clip(p0[:, 1].astype(int), 0, h - 1)
            for zid in self.calib.zones:
                if zid not in self._masks:
                    self._masks[zid] = self.calib.mask(zid, h, w)
                sel = self._masks[zid][py, px]
                if sel.sum() < 4:
                    continue
                v = vel[sel]
                speed = np.linalg.norm(v, axis=1)
                out[zid] = {
                    "mean_speed": float(np.median(speed)),
                    "velocity_variance": float(np.var(v[:, 0]) + np.var(v[:, 1])),
                }
        self.prev, self.prev_t = gray, t
        return out
