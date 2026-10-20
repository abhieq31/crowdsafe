"""Model download and caching. Uses a local stub server, never the internet."""

import hashlib

import pytest

from roomtobreathe.vision import model_store


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("RTB_HOME", str(tmp_path))
    return tmp_path


def _fake_model(monkeypatch, base_url, payload: bytes):
    info = model_store.ModelInfo("dense", "fake.onnx", hashlib.sha256(payload).hexdigest(), len(payload), "test")
    monkeypatch.setitem(model_store.MODELS, "dense", info)
    monkeypatch.setattr(model_store, "RELEASE_URL", base_url + "/")
    return info


def test_fetch_downloads_verifies_and_caches(home, stub_server, monkeypatch):
    url, routes, _ = stub_server
    payload = b"onnx-bytes" * 1000
    _fake_model(monkeypatch, url, payload)
    routes["/fake.onnx"] = (200, payload)
    p = model_store.fetch("dense", log=lambda s: None)
    assert p.read_bytes() == payload and p.parent == home / "models"
    assert model_store.verify("dense")
    routes.clear()  # second call must not touch the network
    assert model_store.resolve("auto", log=lambda s: None) == str(p)


def test_fetch_rejects_tampered_file(home, stub_server, monkeypatch):
    url, routes, _ = stub_server
    _fake_model(monkeypatch, url, b"the real model")
    routes["/fake.onnx"] = (200, b"something else")
    with pytest.raises(model_store.FetchError, match="damaged or has been changed"):
        model_store.fetch("dense", log=lambda s: None)
    assert not model_store.is_installed("dense")
    assert not list((home / "models").glob("*.part"))


def test_fetch_offline_explains_manual_copy(home, stub_server, monkeypatch):
    url, routes, _ = stub_server
    _fake_model(monkeypatch, url, b"x")  # route missing -> HTTP 404
    with pytest.raises(model_store.FetchError, match="copy it to"):
        model_store.fetch("dense", log=lambda s: None)


def test_resolve_specs(home, tmp_path):
    assert model_store.resolve("hog") is None
    own = tmp_path / "mine.onnx"
    own.write_bytes(b"x")
    assert model_store.resolve(str(own)) == str(own)
    with pytest.raises(model_store.FetchError, match="does not exist"):
        model_store.resolve(str(tmp_path / "missing.onnx"))
    with pytest.raises(model_store.FetchError, match="roomtobreathe model fetch sparse"):
        model_store.resolve("sparse", download=False)
