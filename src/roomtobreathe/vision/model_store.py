"""Download and cache the pretrained density models. Standard library only.

The ONNX files are published as assets of a Room to Breathe GitHub release, and
each one is checked against the SHA-256 pinned here before use. They are
DM-Count (MIT) checkpoints exported with scripts/export_density_onnx.py. The
accuracy is published in docs/vision.md.

    dense   trained on UCF-QNRF. Default: best on cameras it has never seen
    sparse  trained on ShanghaiTech B. Street scenes below ~250 people per frame
"""

from __future__ import annotations

import hashlib
import os
import shutil
import sys
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

RELEASE_TAG = "models-v1"
RELEASE_URL = f"https://github.com/abhieq31/roomtobreathe/releases/download/{RELEASE_TAG}/"


@dataclass(frozen=True)
class ModelInfo:
    name: str
    filename: str
    sha256: str
    size_bytes: int
    about: str

    @property
    def url(self) -> str:
        return RELEASE_URL + self.filename


MODELS = {
    "dense": ModelInfo("dense", "dmcount_qnrf.onnx",
                       "0b34bb334bcc4146b207987a846ada36d9320e5bb349b794757573c82740c224", 86_007_099,
                       "DM-Count trained on UCF-QNRF; default, for dense or unknown scenes"),
    "sparse": ModelInfo("sparse", "dmcount_shb.onnx",
                        "399882320902d584aa541da9c567224532063be0b1a560e1681b4e2723d55e96", 86_007_099,
                        "DM-Count trained on ShanghaiTech B; streets below ~250 people per frame"),
}
DEFAULT_MODEL = "dense"


def models_dir() -> Path:
    """Where models are cached: $RTB_HOME/models, else the user's cache folder."""
    if os.environ.get("RTB_HOME"):
        return Path(os.environ["RTB_HOME"]) / "models"
    if sys.platform == "win32" and os.environ.get("LOCALAPPDATA"):
        return Path(os.environ["LOCALAPPDATA"]) / "roomtobreathe" / "models"
    return Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "roomtobreathe" / "models"


def model_path(name: str) -> Path:
    return models_dir() / MODELS[name].filename


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def is_installed(name: str) -> bool:
    return model_path(name).is_file()


class FetchError(RuntimeError):
    pass


def fetch(name: str = DEFAULT_MODEL, log: Callable[[str], None] = print, timeout: float = 30.0) -> Path:
    """Download a model if it is not cached, verify its checksum and return its path."""
    info = MODELS[name]
    dest = model_path(name)
    if dest.is_file():
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".part")
    log(f"Downloading the '{name}' density model ({info.size_bytes / 1e6:.0f} MB, only once) from {info.url}")
    try:
        with urllib.request.urlopen(info.url, timeout=timeout) as r, open(tmp, "wb") as f:
            total = int(r.headers.get("Content-Length") or 0)
            done, step = 0, 0
            while True:
                chunk = r.read(1 << 20)
                if not chunk:
                    break
                f.write(chunk)
                done += len(chunk)
                if total and done * 10 // total > step:
                    step = done * 10 // total
                    log(f"  {done * 100 // total}%")
    except OSError as e:
        tmp.unlink(missing_ok=True)
        raise FetchError(
            f"Could not download the model ({e}).\n"
            f"If this device has no internet, download {info.url} on another computer\n"
            f"and copy it to {dest}") from e
    got = sha256(tmp)
    if got != info.sha256:
        tmp.unlink(missing_ok=True)
        raise FetchError(f"The downloaded model is damaged or has been changed (sha256 {got}, expected "
                         f"{info.sha256}). It was deleted; try again.")
    shutil.move(str(tmp), dest)
    log(f"Model saved to {dest} (checksum verified)")
    return dest


def verify(name: str) -> bool:
    return is_installed(name) and sha256(model_path(name)) == MODELS[name].sha256


def resolve(spec: Optional[str], download: bool = True, log: Callable[[str], None] = print) -> Optional[str]:
    """Turn the ``--model`` value into an ONNX path, or None for the HOG fallback.

    ``auto`` (default) uses the dense model, downloading it if needed and allowed;
    ``dense`` / ``sparse`` pick a published model; ``hog`` forces the fallback;
    anything else is a path to your own ONNX file.
    """
    spec = (spec or "auto").strip()
    if spec.lower() == "hog":
        return None
    if spec.lower() in ("auto", *MODELS):
        name = DEFAULT_MODEL if spec.lower() == "auto" else spec.lower()
        if is_installed(name):
            return str(model_path(name))
        if not download:
            raise FetchError(f"The '{name}' model is not downloaded yet. Run:  roomtobreathe model fetch {name}")
        return str(fetch(name, log=log))
    p = Path(spec).expanduser()
    if not p.is_file():
        raise FetchError(f"Model file {p} does not exist. Use --model auto to download the default model.")
    return str(p)
