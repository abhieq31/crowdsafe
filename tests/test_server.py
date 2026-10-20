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


def test_event_record_survives_restart_and_names_the_operator(tmp_path):
    auth = {"Authorization": "Bearer s3cret"}
    with TestClient(create_app(default_venue(), token="s3cret", log_dir=str(tmp_path))) as c:
        assert c.get("/api/health").json()["auth_required"] is True
        act = {"type": "close_gate", "gate": "G1"}
        assert c.post("/api/actions", json={"action": act, "by": "Asha (DM office)"}).status_code == 401
        assert c.post("/api/actions", json={"action": act, "by": "Asha (DM office)"}, headers=auth).status_code == 200
        c.post("/api/observations", json=[{"zone_id": "hall", "source": "cam-1", "density": 6.2}], headers=auth)
        c.app.state.engine.tick()
        assert c.get("/api/log").status_code == 401
    # A new server process (a restart) reads the same record.
    with TestClient(create_app(default_venue(), token="s3cret", log_dir=str(tmp_path))) as c:
        [day] = c.get("/api/log", headers=auth).json()["days"]
        events = c.get(f"/api/log/{day}", headers=auth).json()
    kinds = [e["event"] for e in events]
    assert kinds.count("server_start") == 2
    [action] = [e for e in events if e["event"] == "action"]
    assert action["by"] == "Asha (DM office)" and action["action"]["gate"] == "G1"
    assert any(e["event"] == "alert" and e["zone_id"] == "hall" and e["level"] == "RED" for e in events)


def test_event_log_skips_a_line_cut_short_by_a_crash(tmp_path):
    from crowdsafe.eventlog import EventLog

    log = EventLog(tmp_path, fsync=False)
    log.record("action", by="a")
    with open(log.path_for(), "a", encoding="utf-8") as f:
        f.write('{"event": "act')  # power cut mid-write
    log.record("action", by="b")
    [day] = log.days()
    assert [e.get("by") for e in log.read(day)] == ["a", "b"]


def test_sent_at_is_accepted_by_the_api(client):
    import time

    t = time.time()
    r = client.post("/api/observations", json=[{"zone_id": "hall", "source": "cam-1", "density": 2.0,
                                                "ts": t - 3600, "sent_at": t - 3600}])
    assert r.json()["accepted"] == 1
    client.app.state.engine.tick()
    hall = next(z for z in client.get("/api/state").json()["zones"] if z["zone_id"] == "hall")
    assert hall["stale"] is False  # device clock one hour slow, but the reading is fresh
