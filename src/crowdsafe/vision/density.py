"""Crowd density estimation from images.

Dense crowds hide most heads, so **bounding-box detectors (YOLO, HOG) badly
undercount above ~2-3 p/m2**, which is exactly the range that matters. The
primary path is a *density-map* model (the CSRNet / DM-Count / P2PNet family)
exported to ONNX. Its output map integrates to the head count. The HOG
detector is only a zero-download fallback for sparse scenes and tests.

Bring-your-own-model contract for ``OnnxDensityModel``:
input ``(1, 3, H, W)`` float32 RGB normalised with ImageNet mean/std, and
output ``(1, 1, h, w)`` density map whose sum is the head count. See
docs/vision.md for exporting a pretrained model.
"""

from __future__ import annotations

import time
from typing import Optional, Protocol

import cv2
import numpy as np

_MEAN = np.array([0.485, 0.456, 0.406], dtype=np.float32)
_STD = np.array([0.229, 0.224, 0.225], dtype=np.float32)


class DensityEstimator(Protocol):
    name: str
    #: relative uncertainty of counts, used for sensor fusion
    relative_sigma: float

    def density_map(self, frame_bgr: np.ndarray) -> np.ndarray:
        """Return an (H, W) map of people per pixel aligned with the frame."""


class OnnxDensityModel:
    name = "onnx-density"
    relative_sigma = 0.15

    def __init__(self, path: str, input_width: Optional[int] = 1024, providers: Optional[list] = None):
        import onnxruntime as ort  # optional dependency

        providers = providers or [p for p in ("CUDAExecutionProvider", "CoreMLExecutionProvider",
                                              "CPUExecutionProvider") if p in ort.get_available_providers()]
        self.sess = ort.InferenceSession(path, providers=providers)
        self.input_name = self.sess.get_inputs()[0].name
        #: frames are resized to this width; None keeps the native width (rounded to /16)
        self.input_width = input_width

    def density_map(self, frame_bgr: np.ndarray) -> np.ndarray:
        h, w = frame_bgr.shape[:2]
        iw = self.input_width or max(16, int(round(w / 16)) * 16)
        ih = max(16, int(round(h * iw / w / 16)) * 16)
        rgb = cv2.cvtColor(cv2.resize(frame_bgr, (iw, ih)), cv2.COLOR_BGR2RGB).astype(np.float32) / 255.0
        x = ((rgb - _MEAN) / _STD).transpose(2, 0, 1)[None]
        out = self.sess.run(None, {self.input_name: x})[0][0, 0]
        total = float(out.sum())
        dm = cv2.resize(out, (w, h), interpolation=cv2.INTER_LINEAR)
        s = float(dm.sum())
        return dm * (total / s) if s > 0 else dm  # resizing must preserve the count


    def seconds_per_frame(self, frame_bgr: np.ndarray, runs: int = 2) -> float:
        """Measure inference time on this device, after one warm-up run (the first is slow)."""
        self.density_map(frame_bgr)
        t0 = time.perf_counter()
        for _ in range(runs):
            self.density_map(frame_bgr)
        return (time.perf_counter() - t0) / runs


class HogPeopleDetector:
    """Fallback for sparse crowds only. Puts one person at each detection's feet."""

    name = "hog-fallback"
    relative_sigma = 0.5

    def __init__(self):
        self.hog = cv2.HOGDescriptor()
        self.hog.setSVMDetector(cv2.HOGDescriptor_getDefaultPeopleDetector())

    def density_map(self, frame_bgr: np.ndarray) -> np.ndarray:
        h, w = frame_bgr.shape[:2]
        dm = np.zeros((h, w), dtype=np.float32)
        rects, _ = self.hog.detectMultiScale(frame_bgr, winStride=(8, 8), scale=1.05)
        for (x, y, rw, rh) in rects:
            fx, fy = min(w - 1, x + rw // 2), min(h - 1, y + rh - 1)
            dm[fy, fx] += 1.0
        return dm


def load_estimator(model_path: Optional[str]) -> DensityEstimator:
    if model_path:
        return OnnxDensityModel(model_path)
    return HogPeopleDetector()
