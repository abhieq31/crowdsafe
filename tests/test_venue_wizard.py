"""`crowdsafe venue new` and `crowdsafe venue check`, driven by scripted answers."""

import json

from crowdsafe.venue import Venue
from crowdsafe.venue_wizard import parse_area, run_venue_new, venue_problems

MELA = ["Ram Navami Mela", "12000", "en, hi",
        "Approach road", "200 x 10", "1", "",
        "Mela ground", "120x80", "0.7", "mud",
        "River ghat", "60 x 15", "0.8", "steps,riverbank",
        "",
        "Main gate", "0", "1", "8", "n", "n",
        "Ground gate", "1", "2", "6", "n", "n",
        "Ghat steps", "2", "3", "4", "y", "n",
        ""]


def run(tmp_path, answers, name="v.json"):
    it, out = iter(answers), []
    rc = run_venue_new(str(tmp_path / name), ask=lambda q: next(it), log=out.append)
    return rc, "\n".join(out), tmp_path / name


def test_mela_becomes_a_valid_venue_with_honest_warnings(tmp_path):
    rc, out, path = run(tmp_path, MELA)
    assert rc == 0
    v = Venue.load(path)
    assert set(v.zones) == {"approach_road", "mela_ground", "river_ghat"}
    assert v.zones["mela_ground"].usable_area == 120 * 80 * 0.7
    assert v.gates["G3"].stairs and v.permitted_capacity == 12000
    assert "2.3x the safe capacity" in out  # 12,000 expected vs 5,248 safe
    assert "Single exit" in out
    assert "approach_road, mela_ground, river_ghat" in out  # names to use in camera calibration


def test_bad_answers_are_asked_again(tmp_path):
    answers = ["Hall", "", "", "Hall", "lots", "-5", "30 x 20", "2", "1", "", "",
               "Front", "9", "0", "0",  # outside -> outside is refused, so the gate is asked again
               "Front", "0", "1", "abc", "4", "n", "n", ""]
    rc, out, path = run(tmp_path, answers)
    assert rc == 0
    assert "positive number" in out and "Type one of the numbers" in out and "two different places" in out
    d = json.loads(path.read_text())
    assert d["zones"][0]["area_m2"] == 600 and d["gates"][0]["width_m"] == 4


def test_existing_file_is_not_overwritten(tmp_path):
    (tmp_path / "v.json").write_text("{}")
    rc, out, _ = run(tmp_path, [])
    assert rc == 1 and "already exists" in out


def test_trapped_zone_and_no_exit_are_reported():
    v = Venue.from_dict({"id": "x", "zones": [{"id": "a", "area_m2": 100}, {"id": "b", "area_m2": 50}],
                         "gates": []})
    probs = venue_problems(v)
    assert any("No gate leads outside" in p for p in probs)
    assert any("No route to outside from: a, b" in p for p in probs)


def test_parse_area():
    assert parse_area("960") == 960
    assert parse_area("40 x 24") == 960 and parse_area("40*24 m") == 960 and parse_area("40×24") == 960
    assert parse_area("abc") is None and parse_area("0") is None
