import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from crowdsafe.server.app import create_app  # noqa: E402
from crowdsafe.venue import default_venue  # noqa: E402


@pytest.fixture
def client():
    with TestClient(create_app(default_venue(), token="")) as c:
        yield c


def test_health_and_venue(client):
    assert client.get("/api/health").json()["ok"]
    v = client.get("/api/venue").json()
    assert {z["id"] for z in v["zones"]} == {"approach", "corridor", "hall", "exit_lane"}
    assert v["capacity"]["safe_capacity"] > 0


def test_ingest_alert_and_cap(client):
    r = client.post("/api/observations", json=[{"zone_id": "hall", "source": "cam-1", "density": 6.2},
                                               {"zone_id": "nope", "source": "cam-1", "density": 1}])
    assert r.json() == {"accepted": 1, "rejected": 1}
    app_engine = client.app.state.engine
    app_engine.tick()
    st = client.get("/api/state").json()
    hall = next(z for z in st["zones"] if z["zone_id"] == "hall")
    assert hall["level"] == "RED"
    alert = next(a for a in st["alerts"] if a["zone_id"] == "hall")
    cap = client.get(f"/api/alerts/{alert['id']}/cap")
    assert cap.status_code == 200 and "Crowd crush risk" in cap.text


def test_validation_rejects_nonsense(client):
    assert client.post("/api/observations", json=[{"zone_id": "hall", "source": "x", "density": -3}]).status_code == 422
    assert client.post("/api/triggers", json={"name": "aliens"}).status_code == 400


def test_planning_endpoints(client):
    c = client.post("/api/plan/capacity", json={"usable_area_m2": 960, "exits": [{"width_m": 3}, {"width_m": 2.5}]})
    assert c.json()["safe_capacity"] == 1640
    r = client.post("/api/plan/risk", json={"event_type": "religious", "expected_attendance": 250000,
                                            "safe_capacity": 80000, "police": 80})
    assert r.json()["approve"] == "do_not_approve"


def test_token_required_when_configured():
    with TestClient(create_app(default_venue(), token="s3cret")) as c:
        body = [{"zone_id": "hall", "source": "cam", "density": 1}]
        assert c.post("/api/observations", json=body).status_code == 401
        ok = c.post("/api/observations", json=body, headers={"Authorization": "Bearer s3cret"})
        assert ok.status_code == 200


def test_demo_and_websocket(client):
    assert client.post("/api/demo/start", json={"scenario": "pandal_surge", "speed": 10}).json()["ok"]
    with client.websocket_connect("/ws/live") as ws:
        msg = ws.receive_json()
        assert msg["mode"] == "demo" and len(msg["zones"]) == 4
    assert client.get("/").status_code == 200
    assert "planning.js" in client.get("/js/app.js").text
