"""``roomtobreathe check``: test an edge device's setup before the event, step by step.

Each step prints OK, WARN or FAIL with a plain-language fix. Nothing is sent
except a health request and an empty observation list to the server. Frames
stay on the device.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Callable, List, Optional, Tuple

Result = Tuple[str, str, str]  # (status, what, fix)


class Checker:
    def __init__(self, log: Callable[[str], None] = print):
        self.log = log
        self.results: List[Result] = []

    def add(self, status: str, what: str, fix: str = "") -> None:
        self.results.append((status, what, fix))
        self.log(f"  [{status:<4}] {what}")
        if fix:
            for line in fix.splitlines():
                self.log(f"         {line}")

    @property
    def failed(self) -> bool:
        return any(s == "FAIL" for s, _, _ in self.results)


def run_check(source: Optional[str], calibration: Optional[str], server: Optional[str],
              model: Optional[str] = "auto", token: Optional[str] = None, every_s: float = 1.0,
              log: Callable[[str], None] = print, start_hint: str = "roomtobreathe edge ...") -> int:
    c = Checker(log)
    log("Room to Breathe setup check\n")

    log("1. Software")
    has_ort = check_software(c)
    if has_ort is None:
        return _summary(c, start_hint)

    log("\n2. Camera calibration")
    calib = check_calibration(c, calibration)

    log("\n3. Camera")
    frame = check_camera(c, source, calib)

    log("\n4. Density model")
    if has_ort:
        check_model(c, model, frame, every_s)

    log("\n5. Control-room server")
    check_server(c, server, token, calib)

    return _summary(c, start_hint)


def check_software(c: Checker) -> Optional[bool]:
    """None if camera support is missing, else whether the model runtime is installed."""
    try:
        import cv2  # noqa: F401
        import numpy  # noqa: F401
    except ImportError:
        c.add("FAIL", "camera support is not installed", "Run:  pip install 'roomtobreathe[vision,onnx]'")
        return None
    c.add("OK", "camera support (numpy, OpenCV)")
    try:
        import onnxruntime  # noqa: F401
    except ImportError:
        c.add("FAIL", "model runtime is not installed, so dense crowds cannot be counted",
              "Run:  pip install 'roomtobreathe[onnx]'")
        return False
    c.add("OK", "model runtime (onnxruntime)")
    return True


def check_calibration(c: Checker, calibration: Optional[str]):
    """The loaded Calibration, or None."""
    if not calibration:
        c.add("FAIL", "no calibration file given",
              "Run  roomtobreathe setup  once, or pass --calibration cam.json (see docs/vision.md, step 1)")
        return None
    from .vision.calibration import Calibration

    try:
        calib = Calibration.load(calibration)
    except (FileNotFoundError, IsADirectoryError):
        c.add("FAIL", f"calibration file {calibration} not found", "Check the path.")
        return None
    except (ValueError, KeyError, json.JSONDecodeError) as e:
        c.add("FAIL", f"calibration file is not valid: {e}",
              "Use 4 or more ground points that are not in one line, with distances in metres.")
        return None
    c.add("OK", f"camera '{calib.camera_id}' with {len(calib.zones)} zone(s)")
    if not calib.zones:
        c.add("FAIL", "no zones defined", "Add at least one zone with an image_polygon.")
    for zid, z in calib.zones.items():
        a = z.ground_area_m2
        if a < 1 or a > 200_000:
            c.add("WARN", f"zone '{zid}' covers {a:,.1f} m2 of ground, which looks wrong",
                  "Check that ground_points_m are in metres and match the image points.")
        else:
            c.add("OK", f"zone '{zid}' covers {a:,.0f} m2 of ground")
    return calib


def check_camera(c: Checker, source: Optional[str], calib=None):
    """One picture from the camera, or None."""
    if not source:
        c.add("FAIL", "no camera given",
              "Run  roomtobreathe setup  once, or pass --source rtsp://..., a video file, or a webcam number like 0")
        return None
    import cv2

    cap = cv2.VideoCapture(int(source) if source.isdigit() else source)
    ok, frame = cap.read() if cap.isOpened() else (False, None)
    cap.release()
    if not ok:
        c.add("FAIL", f"cannot read a picture from {display_source(source)!r}",
              "Check the camera address and password, that it is switched on, and that this device\n"
              "is on the same network. Try opening the same address in VLC.")
        return None
    h, w = frame.shape[:2]
    c.add("OK", f"camera gives {w}x{h} pictures")
    if calib is not None:
        for zid, z in calib.zones.items():
            p = z.image_polygon
            if p[:, 0].min() < -1 or p[:, 1].min() < -1 or p[:, 0].max() > w + 1 or p[:, 1].max() > h + 1:
                c.add("WARN", f"zone '{zid}' reaches outside the {w}x{h} picture",
                      "The calibration was probably made at a different resolution. Redo it on a "
                      "snapshot from this camera.")
    return frame


def display_source(source: str) -> str:
    """The camera address with any password replaced by ***."""
    scheme, sep, rest = source.partition("://")
    creds, at, host = rest.rpartition("@")
    if not sep or not at or ":" not in creds:
        return source
    return f"{scheme}://{creds.split(':', 1)[0]}:***@{host}"


def check_model(c: Checker, model: Optional[str], frame=None, every_s: float = 1.0) -> None:
    """Needs onnxruntime (see check_software). Never downloads."""
    from .vision import model_store

    try:
        path = model_store.resolve(model, download=False)
    except model_store.FetchError as e:
        c.add("FAIL", "density model not available", str(e))
        return
    if path is None:
        c.add("WARN", "using the HOG fallback on purpose",
              "It badly undercounts above ~1 person/m2. Use it only for testing.")
        return
    name = next((n for n in model_store.MODELS if str(model_store.model_path(n)) == path), None)
    if name and not model_store.verify(name):
        c.add("FAIL", f"model file {path} is damaged (checksum mismatch)",
              f"Delete it and run:  roomtobreathe model fetch {name}")
        return
    c.add("OK", f"model {path}")
    _check_speed(c, path, frame, every_s)


def _check_speed(c: Checker, path: str, frame, every_s: float) -> None:
    import numpy as np

    from .vision.density import OnnxDensityModel

    try:
        est = OnnxDensityModel(path)
    except Exception as e:  # onnxruntime raises its own exception types
        c.add("FAIL", f"model would not load: {e}", "Delete the file and download it again.")
        return
    if frame is None:
        frame = np.full((720, 1280, 3), 128, np.uint8)
    from .vision.pipeline import report_interval

    secs = est.seconds_per_frame(frame)
    every = report_interval(secs, every_s)
    from .zones import STALE_AFTER_S

    if every >= STALE_AFTER_S:
        c.add("FAIL", f"this device is too slow: {secs:.1f}s per frame, so it could only report every {every:g}s",
              f"The control room shows a zone as UNKNOWN (no data) after {STALE_AFTER_S:g}s without a report,\n"
              "so these zones would keep flickering. Use a faster computer, or one with a GPU (CUDA)\n"
              "or an Apple chip (CoreML).")
    elif every <= every_s:
        c.add("OK", f"this device keeps up: {secs:.2f}s per frame (reports every {every_s:g}s)")
    else:
        c.add("WARN", f"this device is slow: {secs:.1f}s per frame",
              f"It will report every {every:g}s instead of every {every_s:g}s, so warnings arrive later.\n"
              "That is still safe for slow build-ups. A device with a GPU, or a newer computer, is better "
              "for busy zones.")


def check_server(c: Checker, server: Optional[str], token: Optional[str] = None, calib=None) -> None:
    if not server:
        c.add("FAIL", "no server given", "Run  roomtobreathe setup  once, or pass --server http://<control-room-address>:8000")
        return
    if check_reachable(c, server):
        check_token(c, server, token)
        check_zones(c, server, calib)


def check_reachable(c: Checker, server: str) -> bool:
    base = server.rstrip("/")
    try:
        with urllib.request.urlopen(base + "/api/health", timeout=5) as r:
            health = json.loads(r.read())
        if not isinstance(health, dict):
            raise ValueError("not a Room to Breathe server")
    except (OSError, ValueError) as e:
        c.add("FAIL", f"cannot reach {base} ({e})",
              "Check the address, that `roomtobreathe serve` is running there, and that no firewall blocks the port.")
        return False
    c.add("OK", f"server reachable (Room to Breathe {health.get('version', '?')}, mode {health.get('mode', '?')})")
    return True


def check_token(c: Checker, server: str, token: Optional[str]) -> Optional[int]:
    """Send an empty test report. Returns the HTTP error code, or None if it was accepted."""
    req = urllib.request.Request(server.rstrip("/") + "/api/observations", data=b"[]", method="POST",
                                 headers={"Content-Type": "application/json",
                                          **({"Authorization": f"Bearer {token}"} if token else {})})
    try:
        urllib.request.urlopen(req, timeout=5).close()
    except urllib.error.HTTPError as e:
        if e.code == 401:
            c.add("FAIL", "the server needs an access token" + (" and this one is wrong" if token else ""),
                  "Use the value of RTB_TOKEN on the server: save it with  roomtobreathe setup,  or pass --token.")
        else:
            c.add("FAIL", f"the server refused a test report (HTTP {e.code})", "")
        return e.code
    except OSError as e:
        c.add("FAIL", f"sending a test report failed ({e})", "")
        return 0
    c.add("OK", "server accepts reports from this device")
    return None


def check_zones(c: Checker, server: str, calib) -> None:
    if calib is None:
        return
    try:
        with urllib.request.urlopen(server.rstrip("/") + "/api/venue", timeout=5) as r:
            venue_zones = set(_zone_ids(json.loads(r.read())))
    except (OSError, ValueError):
        return
    missing = sorted(set(calib.zones) - venue_zones)
    if missing:
        c.add("FAIL", f"zone(s) {', '.join(missing)} are not in the server's venue, so their counts would be ignored",
              f"Rename them in the calibration file to one of: {', '.join(sorted(venue_zones)) or '(none)'}")
    else:
        c.add("OK", "every camera zone exists on the server")


def _zone_ids(venue: dict) -> List[str]:
    zones = venue.get("zones", [])
    if isinstance(zones, dict):
        return list(zones)
    return [z.get("id") or z.get("zone_id") for z in zones if isinstance(z, dict)]


def _summary(c: Checker, start_hint: str = "roomtobreathe edge ...") -> int:
    fails = sum(s == "FAIL" for s, _, _ in c.results)
    warns = sum(s == "WARN" for s, _, _ in c.results)
    c.log("")
    if fails:
        c.log(f"NOT READY: {fails} problem(s) to fix" + (f", {warns} warning(s)" if warns else "") + ".")
        return 1
    c.log("READY" + (f", with {warns} warning(s) to read" if warns else "") + f". Start with:  {start_hint}")
    return 0
