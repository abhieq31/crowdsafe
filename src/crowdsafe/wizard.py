"""``crowdsafe setup``: answer a few questions once, then run ``crowdsafe edge`` with no options.

Each answer is tested straight away with the same steps as ``crowdsafe check``.
The answers are saved per device (see config.py). The access token is typed
hidden, saved readable by this user only, and never printed.
"""

from __future__ import annotations

import getpass
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Tuple

from . import config
from .check import (
    Checker,
    check_calibration,
    check_camera,
    check_model,
    check_reachable,
    check_software,
    check_token,
    check_zones,
    display_source,
)

Ask = Callable[[str], str]


class _Setup:
    def __init__(self, ask: Ask, ask_secret: Ask, log: Callable[[str], None]):
        self.ask, self.ask_secret, self.log = ask, ask_secret, log
        self.kept_failing = []

    def prompt(self, question: str, default: Optional[str] = None, optional: bool = False) -> str:
        self.log("")
        self.log(question)
        while True:
            ans = self.ask(f"  [{default}] > " if default else "  > ").strip()
            if ans or default:
                return ans or default
            if optional:
                return ""
            self.log("  Please type an answer (Ctrl+C stops without saving).")

    def retry(self, c: Checker, what: str) -> bool:
        """After a failed test: True to ask again, False to keep the answer anyway."""
        if not c.failed:
            return False
        ans = self.ask("  Press Enter to try a different answer, or type k to keep this one anyway: ").strip()
        if ans.lower() in ("k", "keep"):
            self.kept_failing.append(what)
            return False
        return True

    def tested(self, question: str, default: Optional[str], test: Callable[[Checker, str], Any],
               what: str) -> Tuple[str, Any]:
        while True:
            ans = self.prompt(question, default)
            c = Checker(self.log)
            result = test(c, ans)
            if not self.retry(c, what):
                return ans, result
            default = ans


def run_setup(ask: Ask = input, ask_secret: Ask = getpass.getpass, log: Callable[[str], None] = print,
              path: Optional[Path] = None) -> int:
    path = path or config.config_path()
    try:
        return _run(_Setup(ask, ask_secret, log), path)
    except (KeyboardInterrupt, EOFError):
        log("\nStopped. Nothing was saved.")
        return 1


def _run(s: _Setup, path: Path) -> int:
    log = s.log
    try:
        saved = config.load(path)
    except config.ConfigError as e:
        log(f"{e}\nStarting again from empty answers.")
        saved = {}
    log("CrowdSafe setup for this camera device")
    log(f"Your answers are tested as you go and saved in {path}")
    if saved:
        log("You set this device up before: press Enter to keep the answer shown in [brackets].")

    log("\nChecking the software on this device")
    c = Checker(log)
    has_ort = check_software(c)
    if has_ort is None:
        log("\nInstall camera support first, then run  crowdsafe setup  again.")
        return 1
    new: Dict[str, Any] = {"every": saved.get("every")}

    calib_path, calib = s.tested(
        "1. Where is this camera's calibration file? (the .json from docs/vision.md, step 1)",
        saved.get("calibration"), lambda c, a: check_calibration(c, _abspath(a)), "calibration file")
    new["calibration"] = _abspath(calib_path)

    new["source"], frame = s.tested(
        "2. How does this device reach the camera? Type its address (rtsp://user:password@192.168.1.64/stream),\n"
        "   a video file, or a webcam number like 0.",
        saved.get("source"), lambda c, a: check_camera(c, a, calib), "camera")

    new["model"] = _ask_model(s, saved.get("model") or "auto", has_ort, frame, saved.get("every") or 1.0)

    new["server"], new["token"] = _ask_server(s, saved.get("server"), saved.get("token"), calib)

    config.save(new, path)
    log("")
    log(f"Saved to {path} (only your user account can read it).")
    log(f"  camera       {display_source(new['source'])}")
    log(f"  calibration  {new['calibration']}")
    log(f"  model        {new['model']}")
    log(f"  server       {new['server']}")
    log(f"  token        {'saved (hidden)' if new['token'] else 'none'}")
    if s.kept_failing:
        log(f"\nKept although the test failed: {', '.join(s.kept_failing)}. Fix it, then run  crowdsafe check")
        return 1
    log("\nFrom now on, just run:  crowdsafe check   then   crowdsafe edge")
    log("Options you type still win, for example:  crowdsafe edge --source 1")
    return 0


def _abspath(p: str) -> str:
    return str(Path(p).expanduser().resolve())


def _ask_model(s: _Setup, default: str, has_ort: bool, frame, every_s: float) -> str:
    from .vision import model_store

    while True:
        ans = s.prompt("3. Which density model? Press Enter for the recommended one (auto). Other choices: sparse\n"
                       "   (streets, under ~250 people in view), hog (test only), or the path to your own .onnx file.",
                       default)
        c = Checker(s.log)
        if ans.lower() != "hog" and not has_ort:
            c.add("FAIL", "the model runtime is not installed", "Run:  pip install 'crowdsafe[onnx]'")
        else:
            name = model_store.DEFAULT_MODEL if ans.lower() == "auto" else ans.lower()
            if name in model_store.MODELS and not model_store.is_installed(name):
                size = model_store.MODELS[name].size_bytes / 1e6
                yn = s.ask(f"  The model is not on this device yet. Download it now ({size:.0f} MB, once)? [Y/n] ")
                if yn.strip().lower() not in ("n", "no"):
                    try:
                        model_store.fetch(name, log=s.log)
                    except model_store.FetchError as e:
                        s.log(f"  {e}")
            check_model(c, ans, frame, every_s)
        if not s.retry(c, "model"):
            return ans
        default = ans


def _ask_server(s: _Setup, default: Optional[str], saved_token: Optional[str], calib) -> Tuple[str, Optional[str]]:
    while True:
        server = s.prompt("4. What is the control-room address? It is shown when  crowdsafe serve  starts,\n"
                          "   for example http://192.168.1.20:8000", default)
        if "://" not in server:
            server = "http://" + server
        c = Checker(s.log)
        reachable = check_reachable(c, server)
        if not s.retry(c, "server"):
            break
        default = server

    token = saved_token
    if reachable:
        probe = Checker(lambda _line: None)
        if check_token(probe, server, None) is None:
            s.log("  [OK  ] this control room does not use an access token")
            token = None
        else:
            while True:
                token = _ask_token(s, token)
                c = Checker(s.log)
                check_token(c, server, token)
                if not s.retry(c, "token"):
                    break
        check_zones(Checker(s.log), server, calib)
    else:
        token = _ask_token(s, token, optional=True)
    return server, token


def _ask_token(s: _Setup, saved: Optional[str], optional: bool = False) -> Optional[str]:
    s.log("")
    s.log("5. Type the access token (CROWDSAFE_TOKEN on the control-room computer). Typing stays hidden.")
    hint = "Press Enter to keep the saved token" if saved else (
        "Press Enter if the control room has none" if optional else "")
    while True:
        ans = s.ask_secret(f"  {hint}> " if hint else "  > ").strip()
        if ans or saved or optional:
            return ans or saved or None
        s.log("  Please type the token (Ctrl+C stops without saving).")
