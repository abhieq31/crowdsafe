"""The saved per-device config used by `crowdsafe setup`, `edge` and `check`. Standard library only."""

import os
import stat
import sys

import pytest

from crowdsafe import cli, config
from crowdsafe.check import display_source

TOKEN = "s3cret-token-value"


# --- config file -------------------------------------------------------------

@pytest.mark.parametrize("platform,env,expected", [
    ("win32", {"APPDATA": "C:/Users/a/AppData/Roaming"}, "C:/Users/a/AppData/Roaming/crowdsafe"),
    ("darwin", {}, "~/Library/Application Support/crowdsafe"),
    ("linux", {"XDG_CONFIG_HOME": "/xdg"}, "/xdg/crowdsafe"),
    ("linux", {}, "~/.config/crowdsafe"),
])
def test_config_dir_per_platform(monkeypatch, platform, env, expected):
    for k in ("CROWDSAFE_HOME", "APPDATA", "XDG_CONFIG_HOME"):
        monkeypatch.delenv(k, raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setattr(sys, "platform", platform)
    assert config.config_dir().as_posix() == os.path.expanduser(expected)


def test_save_is_private_and_round_trips(home):
    p = config.save({"source": "0", "token": TOKEN, "server": "", "junk": 1})
    assert p == home / "edge.json"
    assert config.load() == {"source": "0", "token": TOKEN}
    if os.name == "posix":
        assert stat.S_IMODE(p.stat().st_mode) == 0o600
        p.chmod(0o644)
        config.save({"source": "1"})  # tightens a file someone loosened
        assert stat.S_IMODE(p.stat().st_mode) == 0o600


def test_damaged_config_says_how_to_fix(home):
    home.mkdir()
    (home / "edge.json").write_text("{not json")
    with pytest.raises(config.ConfigError, match="crowdsafe setup"):
        config.load()


def test_display_source_hides_camera_password():
    assert display_source("rtsp://admin:hunter2@10.0.0.5/s") == "rtsp://admin:***@10.0.0.5/s"
    assert display_source("rtsp://10.0.0.5/s") == "rtsp://10.0.0.5/s"
    assert display_source("0") == "0"


def test_edge_without_setup_or_flags_points_to_setup(home, capsys):
    assert cli.main(["edge"]) == 2
    assert "crowdsafe setup" in capsys.readouterr().err
