import json
import math
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from roomtobreathe.alerts import AlertManager, assess_zone
from roomtobreathe.cap import CAP_NS, alert_to_cap
from roomtobreathe.engine import Engine
from roomtobreathe.physics import (
    DensityThresholds,
    Level,
    crowd_pressure,
    dominant_oscillation,
    exit_flow_pps,
    fruin_los,
    time_to_critical,
    weidmann_speed,
)
from roomtobreathe.planning import EventProfile, Exit, assess_risk, capacity_for_venue, compute_capacity
from roomtobreathe.playbooks import default_playbook
from roomtobreathe.venue import Venue, default_venue, polygon_area
from roomtobreathe.zones import Observation, ZoneEstimator

VECTORS = json.loads((Path(__file__).parent / "vectors" / "planning.json").read_text())


# --------------------------------------------------------------- physics
def test_density_levels():
    th = DensityThresholds()
    assert th.level(0.5) == Level.GREEN
    assert th.level(2.5) == Level.YELLOW
    assert th.level(4.2) == Level.ORANGE
    assert th.level(6.0) == Level.RED


def test_fruin_los():
    assert fruin_los(0.2) == "A"
    assert fruin_los(1.0) == "D"
    assert fruin_los(3.0) == "F"


def test_time_to_critical():
    # 100 m2 at 4 p/m2, net +2.5 p/s -> 2.5 p/m2 to go = 250 people = 100 s
    assert time_to_critical(4.0, 100, 5.0, 2.5, 6.5) == pytest.approx(100.0)
    assert time_to_critical(7.0, 100, 1, 0, 6.5) == 0.0
    assert time_to_critical(3.0, 100, 1, 2, 6.5) is None


def test_pressure_and_speed():
    assert crowd_pressure(5.0, 0.004) == pytest.approx(0.02)
    assert weidmann_speed(0.1) > 1.2
    assert weidmann_speed(8.0) == 0.0
    assert weidmann_speed(2.0) > weidmann_speed(4.0) > weidmann_speed(6.0)


def test_exit_flow():
    assert exit_flow_pps(3) == pytest.approx(3 * 82 / 60)
    assert exit_flow_pps(3, stairs=True) < exit_flow_pps(3)


def test_stop_and_go_detection():
    dt = 1.0
    wave = [0.3 + 0.2 * math.sin(2 * math.pi * t / 20) for t in range(120)]
    period, strength = dominant_oscillation(wave, dt)
    assert period == pytest.approx(20, abs=1.5)
    assert strength > 0.7
    # A crowd that is steadily slowing down must NOT look like stop-and-go.
    ramp = [1.2 - 0.008 * t for t in range(120)]
    res = dominant_oscillation(ramp, dt)
    assert res is None or res[1] < 0.4


# --------------------------------------------------------------- venue / planning
def test_polygon_area():
    assert polygon_area([(0, 0), (10, 0), (10, 5), (0, 5)]) == 50


def test_venue_loads_and_validates():
    v = default_venue()
    assert v.zones["hall"].usable_area == pytest.approx(1280 * 0.75)
    bad = v.to_dict()
    bad["gates"][0]["to"] = "nowhere"
    with pytest.raises(ValueError):
        Venue.from_dict(bad)


@pytest.mark.parametrize("case", VECTORS["capacity"], ids=lambda c: c["name"])
def test_capacity_vectors(case):
    r = compute_capacity(case["area"], [Exit(e["width_m"], e.get("stairs", False)) for e in case["exits"]],
                         case["density"], case["evac_s"]).to_dict()
    r.pop("findings")
    assert r == case["expected"]


@pytest.mark.parametrize("case", VECTORS["risk"], ids=lambda c: c["name"])
def test_risk_vectors(case):
    r = assess_risk(EventProfile(**case["profile"])).to_dict()
    assert r["score"] == case["expected"]["score"]
    assert r["tier"] == case["expected"]["tier"]
    assert r["approve"] == case["expected"]["approve"]
    assert [f["code"] for f in r["factors"]] == case["expected"]["codes"]


def test_hathras_like_is_refused():
    r = assess_risk(EventProfile(event_type="religious", expected_attendance=250_000,
                                 safe_capacity=80_000, police=80, exits=1))
    assert r.approve == "do_not_approve"


def test_capacity_for_demo_venue():
    rep = capacity_for_venue(default_venue())
    assert rep.safe_capacity > 0
    assert rep.findings  # the demo venue has only one open exit to the street


# --------------------------------------------------------------- fusion
def test_fusion_weights_precise_sources_more():
    v = default_venue()
    est = ZoneEstimator(v.zones["hall"])
    est.ingest(Observation("hall", "cam", ts=100, density=4.0, density_sigma=0.2))
    est.ingest(Observation("hall", "counter", ts=100, density=2.0, density_sigma=1.0))
    s = est.snapshot(now=101)
    assert 3.8 < s.density < 4.0


def test_count_converts_to_density_and_staleness():
    v = default_venue()
    est = ZoneEstimator(v.zones["hall"], stale_after_s=10)
    est.ingest(Observation("hall", "counter", ts=0, count=960))
    assert est.snapshot(now=1).density == pytest.approx(1.0)
    stale = est.snapshot(now=60)
    assert stale.stale and stale.density is None
    a = assess_zone(stale, v.zones["hall"])
    assert a.level is None and a.label == "UNKNOWN"  # a dead camera never looks "safe"


# --------------------------------------------------------------- alerts / playbooks / CAP
def _state(est, now):
    return {est.zone.id: est.snapshot(now)}


def test_alert_escalates_immediately_deescalates_after_cooldown():
    v = default_venue()
    v.zones = {"hall": v.zones["hall"]}
    am = AlertManager(v, cooldown_s=30, playbook=default_playbook())
    est = ZoneEstimator(v.zones["hall"], stale_after_s=1000)
    est.ingest(Observation("hall", "cam", ts=0, density=5.8))
    [a] = am.update(_state(est, 0), now=0)
    assert a.level == "RED" and a.actions
    # Hold people back upstream: close the perimeter, slow (don't seal) the zone's own entrance.
    assert any(x["type"] == "close_gate" and x["gate"] == "G1" for x in a.actions)
    assert any(x["type"] == "meter_gate" and x["gate"] == "G3" for x in a.actions)
    assert not any(x["type"] == "close_gate" and x["gate"] == "G3" for x in a.actions)
    est.ingest(Observation("hall", "cam", ts=1, density=1.0))
    assert am.update(_state(est, 1), now=1) == []  # still RED during the cooldown
    assert am.update(_state(est, 20), now=20) == []
    [b] = am.update(_state(est, 40), now=40)
    assert b.level == "GREEN"


def test_trigger_escalates_and_adds_rumour_message():
    v = default_venue()
    am = AlertManager(v, playbook=default_playbook())
    am.set_trigger("rumour", True)
    est = ZoneEstimator(v.zones["hall"])
    est.ingest(Observation("hall", "cam", ts=0, density=2.5))
    alerts = am.update(_state(est, 0), now=0)
    hall = [a for a in alerts if a.zone_id == "hall"][0]
    assert hall.level == "ORANGE"  # YELLOW by density, +1 for the rumour
    msgs = [x for x in hall.actions if x["type"] == "announce"]
    assert any(m["message"] == "no_danger" for m in msgs)
    assert set(msgs[0]["text"]) == {"en", "hi", "bn"}
    with pytest.raises(ValueError):
        am.set_trigger("aliens", True)


def test_gate_selectors_walk_the_venue_graph():
    from roomtobreathe.playbooks import _gates
    v = default_venue()
    ids = lambda sel: sorted(g.id for g in _gates(v, "hall", sel))  # noqa: E731
    assert ids("inflow") == ["G3"]
    assert ids("outflow") == ["G4", "G6"]
    assert ids("entry") == ["G1"]
    assert ids("upstream") == ["G2"]


def test_cap_xml_is_valid():
    v = default_venue()
    am = AlertManager(v, playbook=default_playbook())
    est = ZoneEstimator(v.zones["hall"])
    est.ingest(Observation("hall", "cam", ts=1_700_000_000, density=6.0))
    [a] = [x for x in am.update(_state(est, 1_700_000_000), now=1_700_000_000)]
    xml = alert_to_cap(a, v)
    root = ET.fromstring(xml.split("?>", 1)[1])
    ns = {"c": CAP_NS}
    assert root.find("c:info/c:severity", ns).text == "Extreme"
    poly = root.find("c:info/c:area/c:polygon", ns).text.split()
    assert poly[0] == poly[-1] and len(poly) == 5  # closed ring of lat,lon pairs
    lat, lon = map(float, poly[0].split(","))
    assert 22 < lat < 23 and 88 < lon < 89


# --------------------------------------------------------------- end-to-end simulation
def _run(scenario, respond, minutes=35):
    e = Engine(default_venue())
    e.start_demo(scenario)
    peaks = {z: 0.0 for z in e.venue.zones}
    first_red, responded = None, set()
    for _ in range(minutes * 60):
        for al in e.tick():
            if al.level == "RED" and first_red is None:
                first_red = e.sim.t
            if respond and al.level == "RED" and al.zone_id not in responded:
                responded.add(al.zone_id)
                for act in al.actions:
                    e.execute(act)
        for z in peaks:
            peaks[z] = max(peaks[z], e.sim.true_density(z))
    return e, peaks, first_red


def test_surge_without_response_becomes_critical_and_is_warned_early():
    e, peaks, _ = _run("pandal_surge", respond=False)
    assert peaks["hall"] > 6.5
    # The hall must reach RED (with time to act) before the true density hits critical.
    hall_red = next(a for a in e.alerts.history if a.zone_id == "hall" and a.level == "RED")
    sim = Engine(default_venue())
    sim.start_demo("pandal_surge")
    t_crit = None
    while t_crit is None and sim.sim.t < 3600:
        sim.sim.step()
        if sim.sim.true_density("hall") >= 6.5:
            t_crit = sim.sim.t
    lead = t_crit - (hall_red.ts - e.sim_start)
    assert lead >= 300, f"only {lead:.0f}s of warning"


def test_playbook_response_keeps_every_zone_safe():
    # The crush must not simply move into the queue behind the hall.
    _, peaks, _ = _run("pandal_surge", respond=True)
    assert max(peaks.values()) < 4.5, peaks


def test_steady_scenario_has_no_false_alarms():
    e, peaks, _ = _run("steady", respond=False, minutes=20)
    assert max(peaks.values()) < 2.0
    assert not [a for a in e.alerts.history if a.level in ("ORANGE", "RED")]


def test_device_clock_ahead_cannot_hide_a_dead_camera():
    from roomtobreathe.venue import default_venue
    from roomtobreathe.zones import Observation

    e = Engine(default_venue())
    t = 1_000_000.0
    # A camera whose clock runs 10 minutes fast sends one report, then dies.
    e.ingest([Observation("hall", "cam-1", ts=t + 600, density=2.0)], now=t)
    assert not e.estimators["hall"].snapshot(t + 1).stale
    assert e.estimators["hall"].snapshot(t + 20).stale  # stale after 15 s, not after 10 min


def test_sent_at_corrects_device_clock_but_keeps_backlog_age():
    from roomtobreathe.zones import server_time

    now, offset = 1_000_000.0, -300.0  # device clock 5 minutes slow
    sent_at = now + offset
    assert server_time(sent_at, sent_at, now) == now  # a fresh reading is fresh, not 5 minutes old
    assert server_time(sent_at - 60, sent_at, now) == now - 60  # held 60 s in the backlog: 60 s old
    assert server_time(None, sent_at, now) is None


def test_venue_round_trip_keeps_usable_area():
    from roomtobreathe.venue import Venue, default_venue

    v = default_venue()
    again = Venue.from_dict(json.loads(json.dumps(v.to_dict())))
    for zid, z in v.zones.items():
        assert again.zones[zid].usable_area == pytest.approx(z.usable_area)  # usable_fraction applied once
