import pytest

np = pytest.importorskip("numpy")
cv2 = pytest.importorskip("cv2")

from crowdsafe.vision.calibration import Calibration  # noqa: E402
from crowdsafe.vision.motion import MotionAnalyzer  # noqa: E402
from crowdsafe.vision.pipeline import zone_observations  # noqa: E402

# A top-down camera where 20 px = 1 m makes the expected numbers easy to check.
CAL = dict(camera_id="cam-test",
           image_points=[[0, 0], [400, 0], [400, 300], [0, 300]],
           ground_points_m=[[0, 0], [20, 0], [20, 15], [0, 15]],
           zones=[{"zone_id": "z", "image_polygon": [[0, 0], [400, 0], [400, 300], [0, 300]]}])


def test_calibration_area_and_pixel_area():
    c = Calibration(**CAL)
    assert c.zones["z"].ground_area_m2 == pytest.approx(300, rel=1e-4)
    pam = c.pixel_area_map(300, 400)
    assert pam.sum() == pytest.approx(300, rel=1e-3)


def test_perspective_calibration_area():
    # Trapezoid in the image (far edge looks narrower) maps to a 10x10 m square.
    c = Calibration("cam", [[100, 50], [300, 50], [400, 300], [0, 300]],
                    [[0, 10], [10, 10], [10, 0], [0, 0]],
                    [{"zone_id": "z", "image_polygon": [[100, 50], [300, 50], [400, 300], [0, 300]]}])
    assert c.zones["z"].ground_area_m2 == pytest.approx(100, rel=1e-3)


def test_degenerate_calibration_rejected():
    with pytest.raises(ValueError):
        Calibration("c", [[0, 0], [1, 1], [2, 2], [3, 3]], [[0, 0], [1, 1], [2, 2], [3, 3]], [])


def test_density_from_density_map():
    c = Calibration(**CAL)
    dm = np.zeros((300, 400), np.float32)
    dm[10:290:10, 10:390:10] = 1.0  # 28*38 = 1064 people over 300 m2
    [o] = zone_observations(c, dm, {}, 0.15, "cam-test", 0.0)
    assert o["count"] == pytest.approx(1064)
    assert o["density"] == pytest.approx(1064 / 300, rel=1e-3)


def test_motion_measures_ground_speed():
    c = Calibration(**CAL)
    rng = np.random.default_rng(0)
    tex = cv2.GaussianBlur((rng.random((300, 460)) * 255).astype(np.uint8), (0, 0), 2)
    f0 = cv2.cvtColor(tex[:, 0:400], cv2.COLOR_GRAY2BGR)
    f1 = cv2.cvtColor(tex[:, 4:404], cv2.COLOR_GRAY2BGR)  # scene moved 4 px = 0.2 m
    m = MotionAnalyzer(c, max_width=400)
    m.update(f0, 0.0)
    out = m.update(f1, 0.5)  # 0.2 m in 0.5 s = 0.4 m/s
    assert out["z"]["mean_speed"] == pytest.approx(0.4, abs=0.05)
    assert out["z"]["velocity_variance"] < 0.01  # uniform motion means low turbulence


def test_report_interval_slows_down_instead_of_shrinking_the_model():
    from crowdsafe.vision.pipeline import report_interval

    assert report_interval(0.3, 1.0) == 1.0  # fast device keeps the requested interval
    assert report_interval(4.0, 1.0) == pytest.approx(6.7)  # 4 s inference -> report every 6.7 s


def test_sanitize_drops_impossible_values_but_keeps_the_rest():
    from crowdsafe.vision.pipeline import sanitize

    o = sanitize({"zone_id": "z", "source": "c", "count": 50.0, "density": 45.0, "density_sigma": 6.0,
                  "mean_speed": 30.0, "velocity_variance": 9.0})
    assert o == {"zone_id": "z", "source": "c", "count": 50.0}
    o = sanitize({"zone_id": "z", "source": "c", "density": 3.0, "mean_speed": float("nan")})
    assert o == {"zone_id": "z", "source": "c", "density": 3.0}


def test_post_drops_batches_the_server_will_never_accept(stub_server):
    from crowdsafe.vision.pipeline import post

    url, routes, _ = stub_server
    obs = [{"zone_id": "z", "source": "c", "count": 1.0}]
    routes["/api/observations"] = (200, b'{"accepted": 1, "rejected": 0}')
    assert post(url, obs) == "ok"
    routes["/api/observations"] = (422, b"{}")
    assert post(url, obs) == "drop"  # retrying would block every later report
    routes["/api/observations"] = (503, b"{}")
    assert post(url, obs) == "retry"  # server down: keep for store-and-forward
    assert post("http://127.0.0.1:9", obs, timeout=1) == "retry"


def test_too_slow_devices_are_caught_before_zones_flicker_to_no_data():
    from crowdsafe.vision.pipeline import report_interval
    from crowdsafe.zones import STALE_AFTER_S

    assert report_interval(8.0, 1.0) < STALE_AFTER_S  # 13.4 s: slow but every report lands in time
    assert report_interval(9.5, 1.0) >= STALE_AFTER_S  # crowdsafe check fails this device
