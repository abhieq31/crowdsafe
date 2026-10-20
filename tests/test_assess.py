"""assess_event: one verdict, plain reasons, concrete fixes."""

import json

from roomtobreathe.assess import assess_event, extra_exit_width
from roomtobreathe.venue import Venue, default_venue

PANDAL = default_venue().to_dict()


def levels(r):
    return {c["title"].split(":")[0]: c["level"] for c in r["checks"]}


def test_permitted_crowd_is_safe():
    r = assess_event(PANDAL, 2000, 60)
    assert r["verdict"] == "pass" and r["headline"] == "Safe for 2,000 people"
    assert all(c["level"] == "pass" for c in r["checks"])
    evac = next(c for c in r["checks"] if c["title"].startswith("Everyone can get out"))
    assert "1,563 people" in evac["detail"]  # people really were inside when the evacuation started


def test_overcrowded_event_fails_with_reasons_and_fixes():
    r = assess_event(PANDAL, 9000, 45)
    assert r["verdict"] == "fail" and r["headline"].startswith("Not safe")
    assert r["checks"][0]["level"] == "fail"  # worst first
    titles = " ".join(c["title"] for c in r["checks"])
    assert "safe capacity is 3,224" in titles and "becomes a crush" in titles
    assert any("at most 3,224" in f for f in r["fixes"])


def test_gate_closed_by_one_scenario_does_not_leak_into_the_next():
    # The surge-with-response run closes the entry gate; the evacuation run that follows must still start
    # with people inside. (It once started empty and reported "0 people, 0 minutes".)
    r = assess_event(PANDAL, 9000, 45)
    evac = next(c for c in r["checks"] if "Evacuation" in c["title"] or "get out" in c["title"])
    assert "0 people" not in evac["detail"]


def test_extra_exit_width_respects_the_largest_exit_blocked_rule():
    v = Venue.from_dict({"id": "m", "zones": [{"id": "g", "area_m2": 10000}],
                         "gates": [{"id": "G1", "from": "outside", "to": "g", "width_m": 8}]})
    total, n = extra_exit_width(v, 12000)
    # 12,000 people in 8 min = 25 p/s = 18.3 m of level exit beyond the (blocked) 8 m one, in exits <= 8 m
    assert 18 <= total <= 19 and n == 3
    assert extra_exit_width(v, 100) == (0.0, 0)
    assert extra_exit_width(v, 50000)[0] == float("inf")  # 10,000 m2 holds 20,000: exits are not the fix


def test_two_way_gates_are_reported_once():
    v = {"id": "m", "zones": [{"id": "a", "name": "Ground", "area_m2": 5000}],
         "gates": [{"id": "G1", "from": "outside", "to": "a", "width_m": 6}]}
    r = assess_event(json.loads(json.dumps(v)), 1000, 30)
    assert sum("way they came in" in c["title"] for c in r["checks"]) == 1


def test_api_and_cli(tmp_path, capsys):
    import pytest

    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    from roomtobreathe.cli import main
    from roomtobreathe.server.app import create_app

    with TestClient(create_app(default_venue(), token="t")) as c:
        r = c.post("/api/assess", json={"attendance": 2000, "arrival_minutes": 60})
        assert r.status_code == 200 and r.json()["verdict"] == "pass"  # the server's own venue by default
        assert c.post("/api/assess", json={}).status_code == 400
        assert c.get("/api/state").json()["mode"] == "live"
    p = tmp_path / "v.json"
    p.write_text(json.dumps(PANDAL))
    assert main(["assess", str(p), "--people", "9000", "--arrival-minutes", "45"]) == 1  # not safe -> exit 1
    assert "NOT SAFE FOR 9,000 PEOPLE" in capsys.readouterr().out
