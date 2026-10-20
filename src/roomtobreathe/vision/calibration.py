"""Camera-to-ground calibration (homography).

Without this, a camera only gives "people in the picture". With it, it gives
**people per square metre**, which is the quantity that kills. The operator
clicks 4+ points on the ground in the image (tile corners, a measured rope
square, road markings) and enters their real positions in metres. This is
done once per camera.

Calibration JSON::

    {
      "camera_id": "cam-hall-1",
      "image_points": [[412, 690], [1510, 702], [1288, 380], [640, 372]],
      "ground_points_m": [[0, 0], [12, 0], [12, 15], [0, 15]],
      "zones": [{"zone_id": "hall", "image_polygon": [[0, 300], [1920, 300], [1920, 1080], [0, 1080]]}]
    }
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List

import cv2
import numpy as np

from ..venue import polygon_area


@dataclass
class ZoneMask:
    zone_id: str
    image_polygon: np.ndarray  # (N, 2) float32 pixels
    ground_area_m2: float


class Calibration:
    def __init__(self, camera_id: str, image_points, ground_points_m, zones: List[dict]):
        self.camera_id = camera_id
        src = np.asarray(image_points, dtype=np.float32)
        dst = np.asarray(ground_points_m, dtype=np.float32)
        if len(src) < 4 or len(src) != len(dst):
            raise ValueError("need >=4 matching image/ground point pairs")
        if len(src) == 4:
            self.H = cv2.getPerspectiveTransform(src, dst)
        else:
            self.H, _ = cv2.findHomography(src, dst, cv2.RANSAC, 0.5)
        if self.H is None or abs(np.linalg.det(self.H)) < 1e-12:
            raise ValueError("degenerate calibration: points must not be collinear")
        self.zones: Dict[str, ZoneMask] = {}
        for z in zones:
            poly = np.asarray(z["image_polygon"], dtype=np.float32)
            ground = self.to_ground(poly)
            self.zones[z["zone_id"]] = ZoneMask(z["zone_id"], poly, polygon_area(ground.tolist()))

    @classmethod
    def load(cls, path) -> "Calibration":
        d = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls(d["camera_id"], d["image_points"], d["ground_points_m"], d.get("zones", []))

    def to_ground(self, pts: np.ndarray) -> np.ndarray:
        pts = np.asarray(pts, dtype=np.float32).reshape(-1, 1, 2)
        return cv2.perspectiveTransform(pts, self.H).reshape(-1, 2)

    def pixel_area_map(self, height: int, width: int) -> np.ndarray:
        """Ground area (m2) covered by each pixel: |det J| = |det H| / w^3."""
        ys, xs = np.mgrid[0:height, 0:width].astype(np.float64)
        h = self.H
        w = h[2, 0] * xs + h[2, 1] * ys + h[2, 2]
        return np.abs(np.linalg.det(h) / (w ** 3))

    def mask(self, zone_id: str, height: int, width: int) -> np.ndarray:
        m = np.zeros((height, width), dtype=np.uint8)
        cv2.fillPoly(m, [self.zones[zone_id].image_polygon.astype(np.int32)], 1)
        return m.astype(bool)
