"""Saved per-device setup for ``roomtobreathe edge`` and ``roomtobreathe check``. Standard library only.

``roomtobreathe setup`` writes it; command-line flags always override it. The file
holds the access token, so it is written readable by this user only (0600).

    $RTB_HOME/edge.json, if RTB_HOME is set, else
    Windows  %APPDATA%\\roomtobreathe\\edge.json
    macOS    ~/Library/Application Support/roomtobreathe/edge.json
    Linux    $XDG_CONFIG_HOME/roomtobreathe/edge.json (default ~/.config)
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, Optional

KEYS = ("source", "calibration", "server", "token", "model", "every")


class ConfigError(RuntimeError):
    pass


def config_dir() -> Path:
    if os.environ.get("RTB_HOME"):
        return Path(os.environ["RTB_HOME"])
    if sys.platform == "win32" and os.environ.get("APPDATA"):
        return Path(os.environ["APPDATA"]) / "roomtobreathe"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "roomtobreathe"
    return Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "roomtobreathe"


def config_path() -> Path:
    return config_dir() / "edge.json"


def load(path: Optional[Path] = None) -> Dict[str, Any]:
    """The saved setup, or {} if there is none."""
    path = path or config_path()
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise ConfigError(f"The saved setup in {path} cannot be read ({e}). Run:  roomtobreathe setup") from e
    if not isinstance(data, dict):
        raise ConfigError(f"The saved setup in {path} is damaged. Run:  roomtobreathe setup")
    return {k: v for k, v in data.items() if k in KEYS and v not in (None, "")}


def save(settings: Dict[str, Any], path: Optional[Path] = None) -> Path:
    """Write the setup atomically, readable and writable by this user only."""
    path = path or config_path()
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    data = {k: settings[k] for k in KEYS if settings.get(k) not in (None, "")}
    tmp = path.with_name(path.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
        f.write("\n")
    os.chmod(tmp, 0o600)  # in case the file already existed with wider permissions
    os.replace(tmp, path)
    return path


def merge(args: Any, saved: Dict[str, Any], defaults: Dict[str, Any]) -> Dict[str, Any]:
    """Flag if given, else the saved value, else the built-in default."""
    out = {}
    for k in KEYS:
        v = getattr(args, k, None)
        out[k] = v if v is not None else saved.get(k, defaults.get(k))
    return out
