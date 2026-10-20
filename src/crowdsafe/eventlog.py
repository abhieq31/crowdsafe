"""Append-only event record for after-action review. Standard library only.

ETHICS.md promises that every executed action is logged with who approved it.
That record must survive a crash or a restart, because it is what an inquiry
reads after an incident. Each event is one JSON line, written and flushed to
the operating system immediately, in one file per UTC day::

    crowdsafe-logs/crowdsafe-2026-10-20.jsonl

Events: ``server_start``, ``alert`` (a zone changed level), ``action`` (an
operator approved a playbook action), ``trigger``, ``demo_start``, ``demo_stop``.
Sensor readings are not logged here (they are high volume and contain no decisions).
Operator names are personal data: keep the folder access-controlled and set a
retention period.
"""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Iterator, Optional


class EventLog:
    def __init__(self, directory, fsync: bool = True):
        self.dir = Path(directory)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.fsync = fsync
        self._lock = threading.Lock()

    def path_for(self, ts: Optional[float] = None) -> Path:
        day = time.strftime("%Y-%m-%d", time.gmtime(time.time() if ts is None else ts))
        return self.dir / f"crowdsafe-{day}.jsonl"

    def record(self, kind: str, **data) -> dict:
        # Wall-clock time of recording, plus the engine's own time (simulated in demo mode).
        entry = {"logged_at": time.time(), "event": kind, **data}
        line = json.dumps(entry, ensure_ascii=False, default=str) + "\n"
        with self._lock, open(self.path_for(entry["logged_at"]), "a+b") as f:
            if f.tell() > 0:
                f.seek(-1, os.SEEK_END)
                if f.read(1) != b"\n":
                    line = "\n" + line  # the previous write was cut short: start a fresh line
            f.write(line.encode("utf-8"))
            f.flush()
            if self.fsync:
                os.fsync(f.fileno())  # an action approved one second before a power cut is still on disk
        return entry

    def days(self) -> list:
        return sorted(p.name[len("crowdsafe-"):-len(".jsonl")] for p in self.dir.glob("crowdsafe-*.jsonl"))

    def read(self, day: str) -> Iterator[dict]:
        p = self.dir / f"crowdsafe-{day}.jsonl"
        if not p.is_file():
            return
        with open(p, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        yield json.loads(line)
                    except ValueError:
                        continue  # a line cut short by a crash; the rest of the file is still good
