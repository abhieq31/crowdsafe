"""What-if studies: the same engine as the control room, on the user's own venue."""

import json

from roomtobreathe.venue import default_venue
from roomtobreathe.whatif import describe, prepare_venue, run_whatif

PANDAL = default_venue().to_dict()


def test_overcrowded_surge_goes_critical_and_the_playbook_prevents_it():
    r = run_whatif(PANDAL, {"attendance": 9000, "arrival_minutes": 45, "minutes": 60,
                            "surge_at_min": 10, "blocked_gate": "G4", "blocked_at_min": 15})
    a, b = r["no_response"], r["with_response"]
    assert a["reached_critical"], "9,000 people is ~3x the safe capacity: something must fail"
    worst = min((s for s in a["summary"] if s["critical_at_min"]), key=lambda s: s["critical_at_min"])
    assert worst["first_red_min"] < worst["critical_at_min"]  # the RED alert comes first
    assert worst["warning_lead_min"] > 1
    assert not b["reached_critical"] and b["actions"]
    text = " ".join(describe(r))
    assert "reaches critical density" in text and "stays below critical" in text


def test_permitted_crowd_stays_safe():
    r = run_whatif(PANDAL, {"attendance": 2000, "arrival_minutes": 60, "minutes": 60})
    assert not r["no_response"]["reached_critical"]
    assert max(s["peak_density"] for s in r["no_response"]["summary"]) < 4.0


def test_evacuation_reports_time_to_clear():
    r = run_whatif(PANDAL, {"attendance": 3000, "arrival_minutes": 30, "minutes": 60, "evacuate_at_min": 30})
    ev = r["no_response"]["evacuation"]
    assert ev["people_inside"] > 1000
    assert 3 < ev["minutes_to_clear_99pct"] < 30
    assert describe(r)[0].startswith("Evacuation:")


def test_venue_with_one_way_entry_only_gets_a_way_out_and_a_warning():
    from roomtobreathe.venue import Venue

    v = Venue.from_dict({"id": "m", "zones": [{"id": "a", "area_m2": 1000}, {"id": "b", "area_m2": 500}],
                         "gates": [{"id": "G1", "from": "outside", "to": "a", "width_m": 6},
                                   {"id": "G2", "from": "a", "to": "b", "width_m": 4}]})
    sim, notes = prepare_venue(v)
    assert {"G1-out", "G2-out"} <= set(sim.gates)
    assert sim.gates["G1"].width_m == 3 and sim.gates["G1-out"].to_zone == "outside"
    assert len(notes) == 2 and "Two-way flow" in notes[0]
    r = run_whatif(json.loads(json.dumps(v.to_dict())), {"attendance": 1000, "arrival_minutes": 20, "minutes": 60,
                                                          "evacuate_at_min": 30})
    assert r["no_response"]["evacuation"]["minutes_to_clear_99pct"] is not None  # people can get out


def test_api_and_cli(tmp_path, capsys):
    import pytest

    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    from roomtobreathe.cli import main
    from roomtobreathe.server.app import create_app

    with TestClient(create_app(default_venue(), token="t")) as c:
        r = c.post("/api/whatif", json={"params": {"attendance": 2000, "minutes": 20}})
        assert r.status_code == 200 and r.json()["conclusions"]
        assert c.get("/api/state").json()["mode"] == "live"  # never touches the live engine
        assert c.post("/api/whatif", json={"params": {"attendance": "lots"}}).status_code == 400
    p = tmp_path / "v.json"
    p.write_text(json.dumps(PANDAL))
    assert main(["whatif", str(p), "--attendance", "2000", "--minutes", "20"]) == 0
    assert "peak p/m2" in capsys.readouterr().out
