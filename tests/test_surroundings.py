"""The venue's surroundings: street network, crowd dispersal, traffic, emergency access, deployment plan.

Uses a small synthetic town (a 100 m street grid with a main road, a metro station, a bus stop, a car park,
a hospital, police and fire stations), so the tests are fast and need no internet.
"""

import json
import math

import pytest

from roomtobreathe.surroundings import dispersal as D
from roomtobreathe.surroundings import (
    event_overview,
    merge_osm,
    overpass_queries,
    overpass_query,
    parse_modes,
    parse_osm,
    render_html,
)
from roomtobreathe.surroundings import osm as osm_mod
from roomtobreathe.surroundings.network import StreetNetwork, hospital_score, to_latlon
from roomtobreathe.surroundings.traffic import diversion
from roomtobreathe.venue import Venue

ORIGIN = (22.50, 88.30)


def town(main_footpath_width=None):
    """OSM-like JSON: a 9x9 grid of streets 100 m apart (x, y from -400 to 400 m)."""
    els, nid = [], {}

    def node(x, y):
        if (x, y) not in nid:
            nid[(x, y)] = len(nid) + 1
            lat, lon = to_latlon(ORIGIN, x, y)
            els.append({"type": "node", "id": nid[(x, y)], "lat": lat, "lon": lon})
        return nid[(x, y)]

    wid = 1000
    for k in range(-400, 401, 100):
        for horizontal in (True, False):
            pts = [(x, k) if horizontal else (k, x) for x in range(-400, 401, 100)]
            if horizontal and k == 0:
                tags = {"highway": "primary", "name": "Main Road"}
                if main_footpath_width:
                    tags["width"] = str(main_footpath_width)
            elif not horizontal and k == 0:
                tags = {"highway": "secondary", "name": "Station Road"}
            elif horizontal and k == 200:
                tags = {"highway": "residential", "name": "Temple Lane", "oneway": "yes"}
            else:
                tags = {"highway": "residential", "name": f"{'Row' if horizontal else 'Cross'} {k}"}
            wid += 1
            els.append({"type": "way", "id": wid, "nodes": [node(*p) for p in pts], "tags": tags})

    def poi(i, x, y, **tags):
        lat, lon = to_latlon(ORIGIN, x, y)
        els.append({"type": "node", "id": 90000 + i, "lat": lat, "lon": lon, "tags": tags})

    poi(1, 300, 310, railway="station", station="subway", name="Central")
    poi(2, 320, 290, railway="subway_entrance", name="Central Gate 2")
    poi(3, -300, 10, highway="bus_stop", name="Market Stop")
    poi(4, 0, -310, amenity="parking", capacity="300")
    poi(5, 390, -390, amenity="hospital", emergency="yes", name="District Hospital")
    poi(6, -390, -390, amenity="hospital", name="City Eye Hospital")
    poi(7, -390, 390, amenity="police", name="Town Police Station")
    poi(8, 390, 390, amenity="fire_station", name="Fire Station")
    return {"elements": els}


def venue(gate_widths=(6, 6, 4)):
    """A 120 x 120 m ground in the block north-west of the centre, with gates on three sides."""
    zone = [[-170, 30], [-50, 30], [-50, 170], [-170, 170]]
    gates = [
        {"id": "G1", "name": "East gate", "from": "ground", "to": "outside", "width_m": gate_widths[0],
         "line": [[-50, 95], [-50, 105]]},
        {"id": "G2", "name": "South gate", "from": "outside", "to": "ground", "width_m": gate_widths[1],
         "line": [[-115, 30], [-105, 30]]},
        {"id": "G3", "name": "West gate", "from": "ground", "to": "outside", "width_m": gate_widths[2],
         "line": [[-170, 95], [-170, 105]]},
        {"id": "G4", "name": "North emergency gate", "from": "ground", "to": "outside", "width_m": 3, "open": False,
         "line": [[-115, 170], [-105, 170]]},
    ]
    return {"id": "test-ground", "name": "Test ground", "origin": list(ORIGIN),
            "zones": [{"id": "ground", "name": "Ground", "polygon": zone}], "gates": gates}


@pytest.fixture(scope="module")
def net():
    return StreetNetwork.from_overpass(town(), ORIGIN)


@pytest.fixture(scope="module")
def small():
    return event_overview(venue(), 3000, town(), radius_m=450)


@pytest.fixture(scope="module")
def big():
    return event_overview(venue(), 20000, town(), radius_m=450)


# ------------------------------------------------------------------ network
def test_network_from_osm(net):
    assert len(net.nodes) == 81
    assert len(net.edges) == 18 * 8
    oneway = [e for e in net.edges if e.name == "Temple Lane"]
    assert oneway and all(e.drive_fwd and not e.drive_back for e in oneway)
    assert all(e.walk for e in oneway)  # one-way for cars, two-way on foot
    assert any("estimated" in n for n in net.notes)


def test_station_entrances_are_one_station(net):
    metro = [p for p in net.pois if p.kind == "metro"]
    assert len(metro) == 1 and metro[0].label == "Central" and len(metro[0].access) == 2


def test_recorded_width_overrides_the_estimate():
    n = StreetNetwork.from_overpass(town(main_footpath_width=12), ORIGIN)
    main = [e for e in n.edges if e.name == "Main Road"]
    assert all(e.road_width == 12 and not e.estimated_width for e in main)


def test_hospital_score_prefers_emergency_care(net):
    hs = {p.label: hospital_score(p) for p in net.pois if p.kind == "hospital"}
    assert hs["District Hospital"] > 0 > hs["City Eye Hospital"]


def test_overpass_query_covers_streets_and_services():
    q = overpass_query(22.5, 88.3, 1500)
    assert "around:1500" in q and "around:3000" in q
    assert "hospital" in q and "subway_entrance" in q and "bus_stop" in q


# ------------------------------------------------------------------ crowd flow
def test_walking_slows_as_the_footpath_fills():
    free = D.walking_speed(0, 3)
    busy = D.walking_speed(180, 3)  # 60 p/m/min
    assert free > busy > D.walking_speed(400, 3) == 0.3


def test_level_of_service_bands():
    assert [D.los(v) for v in (10, 30, 45, 60, 80, 100)] == ["A", "B", "C", "D", "E", "F"]


def test_everyone_is_routed(small):
    leaving = sum(g["leaving"] for g in small["gates"])
    arriving = sum(g["arriving"] for g in small["gates"])
    assert leaving == pytest.approx(3000, rel=0.01) and arriving == pytest.approx(3000, rel=0.01)
    assert next(g for g in small["gates"] if g["id"] == "G4")["leaving"] == 0  # closed emergency gate


def test_small_event_fits_and_big_event_needs_measures(small, big):
    assert small["verdict"] in ("pass", "warn")
    assert not small["traffic"]["closures"]
    assert big["verdict"] in ("warn", "fail")
    assert big["choke_points"]


def test_closures_relieve_the_streets(big):
    before = max(s["load"] for s in big["streets"]["departure_before_fixes"])
    after = max(s["load"] for s in big["streets"]["departure"])
    assert big["traffic"]["closures"] and before > 1.0
    assert after < 0.8 * before  # closing roads to traffic gives the crowd the carriageway
    for c in big["traffic"]["closures"]:
        assert c["diversion_via"] or c["diversion_extra_m"] is None


def test_station_gets_the_metro_share(small):
    metro = [t for t in small["transit"] if t["mode"] == "metro" and t["phase"] == "departure"]
    assert metro and metro[0]["people"] == pytest.approx(3000 * 0.25, rel=0.02)


# ------------------------------------------------------------------ traffic
def test_diversion_avoids_the_closed_street(net):
    ways = {e.way for e in net.edges if e.name == "Cross -100"}
    ends = (net.nearest_node(-100, -400, "drive"), net.nearest_node(-100, 400, "drive"))
    net.set_closed_to_traffic(ways)
    try:
        d = diversion(net, ends, "Cross -100", 800)
        assert d["edges"] and all(net.edges[e].way not in ways for e in d["edges"])
        assert "Cross -100" not in d["via"]
    finally:
        net.set_closed_to_traffic(ways, closed=False)


def test_car_park_capacity_is_respected(big):
    [cp] = big["traffic"]["car_parks"]
    assert cp["spaces"] == 300 and cp["spaces_known"]
    assert cp["vehicles"] <= 300 * 1.05


# ------------------------------------------------------------------ emergency
def test_hospital_and_ambulance_route(small):
    em = small["emergency"]
    assert em["hospitals"][0]["name"] == "District Hospital"
    cor = em["corridor"]
    assert cor["found"] and cor["gate"] == "North emergency gate" and cor["drive_min"] > 0
    assert em["fire_station"]["name"] == "Fire Station"
    assert any("mbulance" in c["title"] for c in small["checks"])  # always reported, found or not


# ------------------------------------------------------------------ deployment
def test_every_gate_is_staffed_and_health_is_covered(small):
    places = {d["place"] for d in small["deployments"] if d["role"] == "Steward"}
    for g in ("East gate", "South gate", "West gate", "North emergency gate"):
        assert any(g in p for p in places), g
    roles = {d["role"] for d in small["deployments"]}
    assert {"First-aid post", "Ambulance", "Police"} <= roles
    for d in small["deployments"]:
        assert d["why"] and d["when"] and d["count"] >= 1
        lat, lon = d["lat_lon"]
        assert abs(lat - ORIGIN[0]) < 0.02 and abs(lon - ORIGIN[1]) < 0.02


def test_bigger_crowd_needs_more_staff(small, big):
    staff = lambda r: sum(t["count"] for t in r["totals"])  # noqa: E731
    assert staff(big) > staff(small)


def test_geojson_and_printable_plan(small):
    gj = small["geojson"]
    layers = {f["properties"]["layer"] for f in gj["features"]}
    assert {"venue", "deployment", "hospital", "crowd_departure"} <= layers
    for f in gj["features"]:
        assert f["geometry"]["type"] in ("Point", "MultiLineString", "Polygon")
    html = render_html(small)
    assert "District Hospital" in html and "<script" in html and "Steward" in html
    assert json.dumps(small)  # the whole result is JSON-serialisable (API and browser)


def test_deterministic(small):
    again = event_overview(venue(), 3000, town(), radius_m=450)
    assert again["totals"] == small["totals"] and again["checks"] == small["checks"]


# ------------------------------------------------------------------ inputs
def test_modes_are_parsed_and_normalised():
    assert parse_modes("metro=30, bus=20%,walk=50") == {"metro": 30, "bus": 20, "walk": 50}
    with pytest.raises(ValueError):
        parse_modes("teleport=10")
    r = event_overview(venue(), 2000, town(), modes={"walk": 3, "metro": 1}, radius_m=450)
    assert r["modes"] == {"walk": 0.75, "metro": 0.25}


def test_needs_a_location():
    v = venue()
    del v["origin"]
    with pytest.raises(ValueError, match="location"):
        event_overview(v, 1000, town())


def test_gate_position_round_trips():
    v = Venue.from_dict(venue())
    assert v.gates["G1"].line == [(-50, 95), (-50, 105)]
    assert Venue.from_dict(v.to_dict()).gates["G1"].line == [(-50, 95), (-50, 105)]


def test_typed_gate_without_position_is_flagged():
    v = venue()
    del v["gates"][0]["line"]
    r = event_overview(v, 2000, town(), radius_m=450)
    assert any("East gate has no position" in n for n in r["notes"])


def test_api_and_cli(tmp_path):
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    from roomtobreathe.cli import main
    from roomtobreathe.server.app import create_app
    from roomtobreathe.venue import default_venue

    with TestClient(create_app(default_venue(), token="")) as c:
        r = c.post("/api/overview", json={"venue": venue(), "attendance": 3000, "osm": town(),
                                          "options": {"radius_m": 450, "modes": "metro=50,walk=50"}})
        assert r.status_code == 200, r.text
        assert r.json()["modes"] == {"metro": 0.5, "walk": 0.5}
        h = c.post("/api/overview/html", json=r.json())
        assert h.status_code == 200 and "text/html" in h.headers["content-type"]
        bad = c.post("/api/overview", json={"venue": {**venue(), "origin": None}, "attendance": 10, "osm": town()})
        assert bad.status_code == 400
    vf, of, hf = tmp_path / "v.json", tmp_path / "osm.json", tmp_path / "plan.html"
    vf.write_text(json.dumps(venue()))
    of.write_text(json.dumps(town()))
    code = main(["overview", str(vf), "--people", "3000", "--osm", str(of), "--radius", "450", "--html", str(hf)])
    assert code in (0, 1) and "District Hospital" in hf.read_text()


def test_runs_fast_enough_for_a_browser():
    import time

    t = time.perf_counter()
    event_overview(venue(), 20000, town(), radius_m=450)
    assert time.perf_counter() - t < 5
    assert math.isfinite(t)


# ------------------------------------------------------------------ map data
def to_xml(data):
    out = ['<?xml version="1.0"?><osm version="0.6">']
    for e in data["elements"]:
        tags = "".join(f'<tag k="{k}" v="{v}"/>' for k, v in e.get("tags", {}).items())
        if e["type"] == "node":
            out.append(f'<node id="{e["id"]}" lat="{e["lat"]}" lon="{e["lon"]}">{tags}</node>')
        else:
            nds = "".join(f'<nd ref="{n}"/>' for n in e["nodes"])
            out.append(f'<way id="{e["id"]}">{nds}{tags}</way>')
    return "".join(out) + "</osm>"


def test_osm_xml_export_gives_the_same_plan(small):
    data = parse_osm(to_xml(town()))
    r = event_overview(venue(), 3000, data, radius_m=450)
    assert r["totals"] == small["totals"]
    with pytest.raises(ValueError):
        parse_osm("hello")


def test_query_parts_and_merge():
    qs = overpass_queries(22.5, 88.3, 1500)
    assert len(qs) == 3 and all(q.startswith("[out:json]") for q in qs)
    a = {"elements": [{"type": "node", "id": 1, "lat": 0, "lon": 0}]}
    b = {"elements": [{"type": "node", "id": 1, "lat": 0, "lon": 0, "tags": {"amenity": "police"}},
                      {"type": "node", "id": 2, "lat": 1, "lon": 1}]}
    m = merge_osm([a, b])
    assert len(m["elements"]) == 2 and any(e.get("tags") for e in m["elements"])


def test_download_survives_a_missing_part(monkeypatch):
    full = town()
    streets = {"elements": [e for e in full["elements"] if e["id"] < 90000]}

    def fake(query, timeout=0, mirrors=None, hedge_s=0):
        if "amenity" in query:
            raise RuntimeError("all servers busy")
        return streets

    monkeypatch.setattr(osm_mod, "fetch_part", fake)
    data = osm_mod.fetch_osm(*ORIGIN, 450)
    assert data["missing"] == [osm_mod.PART_NAMES[2]]
    r = event_overview(venue(), 3000, data, radius_m=450)
    assert any("did not send" in n for n in r["notes"])
    assert not r["emergency"]["hospitals"]


def test_download_needs_the_streets(monkeypatch):
    def fake(query, timeout=0, mirrors=None, hedge_s=0):
        raise RuntimeError("down")

    monkeypatch.setattr(osm_mod, "fetch_part", fake)
    with pytest.raises(RuntimeError):
        osm_mod.fetch_osm(*ORIGIN, 450)


def test_hedged_request_takes_the_first_good_answer(monkeypatch):
    import time as _t

    calls = []

    def post(url, query, timeout):
        calls.append(url)
        if url == "a":
            _t.sleep(0.5)  # slow server
            return {"elements": ["slow"]}
        if url == "b":
            raise OSError("502")
        return {"elements": ["fast"]}

    monkeypatch.setattr(osm_mod, "_post", post)
    out = osm_mod.fetch_part("q", mirrors=["a", "b", "c"], hedge_s=0.05)
    assert out == {"elements": ["fast"]} and calls[:3] == ["a", "b", "c"]
