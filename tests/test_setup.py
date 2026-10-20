"""`roomtobreathe setup` and the saved per-device config. Uses a local stub server, never the internet."""

import json
import os
import stat

import pytest

np = pytest.importorskip("numpy")
cv2 = pytest.importorskip("cv2")

from test_check import _calib, _server_routes, _video  # noqa: E402
from test_config import TOKEN  # noqa: E402

from roomtobreathe import cli, config  # noqa: E402
from roomtobreathe.wizard import run_setup  # noqa: E402


def _scripted(answers):
    it = iter(answers)
    prompts = []

    def ask(prompt):
        prompts.append(prompt)
        a = next(it)
        return a() if callable(a) else a

    return ask, prompts


def test_setup_tests_each_answer_and_saves(tmp_path, home, stub_server):
    url, routes, posts = stub_server
    _server_routes(routes, obs_status=401)
    calib, video = _calib(tmp_path), _video(tmp_path)

    def give_token():  # the operator types the right token, so the server now accepts reports
        routes["/api/observations"] = (200, b'{"accepted": 0}')
        return TOKEN

    ask, prompts = _scripted([str(tmp_path / "missing.json"), "", calib, video, "hog", url.replace("http://", "")])
    ask_secret, _ = _scripted([give_token])
    lines = []
    assert run_setup(ask, ask_secret, lines.append) == 0
    out = "\n".join(lines)

    assert "calibration file" in out and "not found" in out  # the wrong path was caught and asked again
    assert "camera gives 320x240 pictures" in out
    assert "HOG fallback" in out
    assert "server accepts reports from this device" in out
    assert "every camera zone exists on the server" in out
    assert "token        saved (hidden)" in out
    assert TOKEN not in out and not any(TOKEN in p for p in prompts)
    assert posts[-1][1] == f"Bearer {TOKEN}"

    saved = json.loads((home / "edge.json").read_text())
    assert saved == {"source": video, "calibration": calib, "server": url, "token": TOKEN, "model": "hog"}
    if os.name == "posix":
        assert stat.S_IMODE((home / "edge.json").stat().st_mode) == 0o600

    # A returning user presses Enter to keep every answer; the token is kept without being shown.
    routes["/api/observations"] = (401, b"")

    def keep_token():
        routes["/api/observations"] = (200, b'{"accepted": 0}')
        return ""

    ask, prompts = _scripted(["", "", "", ""])
    ask_secret, secret_prompts = _scripted([keep_token])
    lines = []
    assert run_setup(ask, ask_secret, lines.append) == 0
    assert json.loads((home / "edge.json").read_text()) == saved
    assert any(f"[{video}]" in p for p in prompts)
    assert "keep the saved token" in secret_prompts[0]
    assert TOKEN not in "\n".join(lines + prompts + secret_prompts)


def test_setup_can_keep_a_failing_answer(tmp_path, home):
    ask, _ = _scripted([_calib(tmp_path), str(tmp_path / "no-camera.mp4"), "k", "hog", "http://127.0.0.1:9", "k"])
    ask_secret, _ = _scripted([""])
    lines = []
    assert run_setup(ask, ask_secret, lines.append) == 1
    out = "\n".join(lines)
    assert "Kept although the test failed: camera, server" in out
    assert config.load()["source"].endswith("no-camera.mp4")
    assert "token" not in config.load()


def test_setup_ctrl_c_saves_nothing(tmp_path, home):
    def interrupt(_prompt):
        raise KeyboardInterrupt

    lines = []
    assert run_setup(interrupt, interrupt, lines.append) == 1
    assert "Nothing was saved" in lines[-1]
    assert not (home / "edge.json").exists()


# --- edge and check use the saved setup ----------------------------------------

def test_check_uses_saved_setup_and_flags_override(tmp_path, home, stub_server, capsys):
    url, routes, posts = stub_server
    _server_routes(routes)
    config.save({"source": _video(tmp_path), "calibration": _calib(tmp_path), "server": url,
                 "token": TOKEN, "model": "hog"})

    cli.main(["check"])
    out, err = capsys.readouterr()
    assert "Using the saved setup" in err
    assert "camera gives 320x240 pictures" in out
    assert "server accepts reports" in out
    assert posts[-1][1] == f"Bearer {TOKEN}"
    if "NOT READY" not in out:  # without onnxruntime the model step fails, which is fine here
        assert out.rstrip().endswith("Start with:  roomtobreathe edge")

    cli.main(["check", "--server", "http://127.0.0.1:9", "--token", "other"])
    out, err = capsys.readouterr()
    assert "cannot reach http://127.0.0.1:9" in out
    assert TOKEN not in out + err


def test_edge_merges_saved_setup_with_flags(tmp_path, home, monkeypatch):
    import roomtobreathe.vision.pipeline as pipeline

    calls = []
    monkeypatch.setattr(pipeline, "run_edge", lambda *a, **k: calls.append((a, k)) or 0)
    config.save({"source": "rtsp://cam/1", "calibration": "/c.json", "server": "http://cr:8000",
                 "token": TOKEN, "model": "hog", "every": 2.0})
    assert cli.main(["edge", "--source", "1"]) == 0
    (args, kw), = calls
    assert args == ("1", "/c.json", "http://cr:8000")
    assert kw["token"] == TOKEN and kw["model"] is None and kw["every_s"] == 2.0
