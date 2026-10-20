"""`crowdsafe check`. Uses a local stub server, never the internet."""

import json

import pytest

np = pytest.importorskip("numpy")
cv2 = pytest.importorskip("cv2")

from crowdsafe.check import run_check  # noqa: E402


def _calib(tmp_path, zone_id="hall"):
    p = tmp_path / "cam.json"
    p.write_text(json.dumps({
        "camera_id": "cam-1",
        "image_points": [[0, 0], [320, 0], [320, 240], [0, 240]],
        "ground_points_m": [[0, 0], [16, 0], [16, 12], [0, 12]],
        "zones": [{"zone_id": zone_id, "image_polygon": [[0, 0], [320, 0], [320, 240], [0, 240]]}],
    }))
    return str(p)


def _video(tmp_path):
    p = tmp_path / "cam.avi"
    w = cv2.VideoWriter(str(p), cv2.VideoWriter_fourcc(*"MJPG"), 5, (320, 240))
    for i in range(5):
        w.write(np.full((240, 320, 3), 40 * i, np.uint8))
    w.release()
    return str(p)


def _server_routes(routes, zones=("hall",), obs_status=200):
    routes["/api/health"] = (200, json.dumps({"ok": True, "version": "t", "mode": "live"}).encode())
    routes["/api/venue"] = (200, json.dumps({"zones": [{"id": z} for z in zones]}).encode())
    routes["/api/observations"] = (obs_status, b'{"accepted": 0, "rejected": 0}')


def test_check_ready_with_hog(tmp_path, stub_server):
    url, routes, posts = stub_server
    _server_routes(routes)
    lines = []
    rc = run_check(_video(tmp_path), _calib(tmp_path), url, model="hog", log=lines.append)
    out = "\n".join(lines)
    assert "camera gives 320x240 pictures" in out
    assert "zone 'hall' covers 192 m2" in out
    assert "every camera zone exists on the server" in out
    # hog is allowed for testing, but flagged; missing onnxruntime would be a FAIL instead
    assert "HOG fallback" in out or "model runtime is not installed" in out
    assert posts and posts[0][2] == b"[]"  # only an empty test report is sent
    assert rc == (1 if "model runtime is not installed" in out else 0)


def test_check_catches_zone_mismatch_bad_token_and_camera(tmp_path, stub_server):
    url, routes, _ = stub_server
    _server_routes(routes, zones=("corridor",), obs_status=401)
    lines = []
    rc = run_check(str(tmp_path / "nope.mp4"), _calib(tmp_path, "hall"), url, model="hog", token="x",
                   log=lines.append)
    out = "\n".join(lines)
    assert rc == 1
    assert "cannot read a picture" in out
    assert "this one is wrong" in out
    assert "zone(s) hall are not in the server's venue" in out and "corridor" in out
    assert out.rstrip().splitlines()[-1].startswith("NOT READY")


def test_check_unreachable_server_and_bad_calibration(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"camera_id": "c", "image_points": [[0, 0], [1, 1], [2, 2], [3, 3]],
                               "ground_points_m": [[0, 0], [1, 1], [2, 2], [3, 3]]}))
    lines = []
    rc = run_check(None, str(bad), "http://127.0.0.1:9", model="hog", log=lines.append)
    out = "\n".join(lines)
    assert rc == 1
    assert "calibration file is not valid" in out
    assert "no camera given" in out
    assert "cannot reach" in out
