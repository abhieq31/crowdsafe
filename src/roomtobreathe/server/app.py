"""HTTP + WebSocket API and static web app.

Run:  roomtobreathe serve --venue <venue.json>     (or)   roomtobreathe demo
"""

from __future__ import annotations

import asyncio
import contextlib
import hmac
import json
import os
import re
from pathlib import Path
from typing import List, Optional, Set

from fastapi import Depends, FastAPI, Header, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .. import __version__
from ..alerts import TRIGGERS
from ..cap import alert_to_cap
from ..engine import Engine
from ..eventlog import EventLog
from ..planning import EventProfile, Exit, assess_risk, capacity_for_venue, compute_capacity
from ..sim import SCENARIOS, load_scenario
from ..venue import Venue, default_venue  # noqa: F401  (re-exported for convenience)
from ..zones import Observation

WEB_DIR = Path(__file__).resolve().parent.parent / "web"


class ObservationIn(BaseModel):
    zone_id: str
    source: str
    ts: Optional[float] = None
    density: Optional[float] = Field(None, ge=0, le=20)
    density_sigma: Optional[float] = Field(None, gt=0)
    count: Optional[float] = Field(None, ge=0)
    mean_speed: Optional[float] = Field(None, ge=0, le=15)
    velocity_variance: Optional[float] = Field(None, ge=0)
    inflow_pps: Optional[float] = Field(None, ge=0)
    outflow_pps: Optional[float] = Field(None, ge=0)
    #: the sender's clock when it sent this batch; lets the server correct for a wrong device clock
    sent_at: Optional[float] = None


class TriggerIn(BaseModel):
    name: str
    active: bool = True
    zone_id: str = "*"
    by: Optional[str] = None


class ActionIn(BaseModel):
    action: dict
    by: str = "operator"


class DemoIn(BaseModel):
    scenario: str = "pandal_surge"
    speed: int = Field(5, ge=1, le=60)


class CapacityIn(BaseModel):
    usable_area_m2: float = Field(..., gt=0)
    exits: List[dict]
    planning_density: float = Field(2.0, gt=0, le=6)
    evac_target_s: float = Field(480, gt=0)


def create_app(venue: Venue, token: Optional[str] = None, tick_s: float = 1.0,
               log_dir: Optional[str] = None, allow_demo: bool = True) -> FastAPI:
    """``log_dir``: where the append-only event record is written (None = not written).

    ``allow_demo=False`` (``roomtobreathe serve``) refuses to start the simulator: on a live server one
    click would replace the real crowd picture with a simulated one.
    """
    event_log = None
    if log_dir:
        try:
            event_log = EventLog(log_dir)
        except OSError as e:  # warnings must still run without the record, but say so loudly
            print(f"[roomtobreathe] WARNING: cannot write the event record in {log_dir} ({e}). "
                  "Approved actions will NOT be saved to disk.")
    engine = Engine(venue, event_log=event_log)
    clients: Set[WebSocket] = set()
    cfg = {"speed": 5}
    token = token if token is not None else os.environ.get("RTB_TOKEN")
    if event_log is not None:
        event_log.record("server_start", version=__version__, venue_id=venue.id, venue_name=venue.name,
                         auth_required=bool(token))

    def require_token(authorization: Optional[str] = Header(None)) -> None:
        if token and not hmac.compare_digest((authorization or "").encode(), f"Bearer {token}".encode()):
            raise HTTPException(401, "missing or invalid bearer token")

    async def loop() -> None:
        while True:
            try:
                engine.tick(sim_steps=cfg["speed"])
                if clients:
                    msg = json.dumps(engine.state(), ensure_ascii=False)
                    for ws in list(clients):
                        try:
                            await ws.send_text(msg)
                        except Exception:  # client went away mid-send
                            clients.discard(ws)
            except Exception as exc:  # never let the safety loop die silently
                print(f"[roomtobreathe] engine loop error: {exc!r}")
            await asyncio.sleep(tick_s)

    @contextlib.asynccontextmanager
    async def lifespan(_app):
        task = asyncio.create_task(loop())
        yield
        task.cancel()

    app = FastAPI(title="Room to Breathe", version=__version__, lifespan=lifespan,
                  description="Open-source crowd-crush early warning and planning API.")
    app.state.engine = engine

    @app.get("/api/health")
    def health():
        return {"ok": True, "version": __version__, "mode": engine.state()["mode"], "auth_required": bool(token),
                "demo_allowed": allow_demo}

    @app.get("/api/venue")
    def get_venue():
        d = engine.venue.to_dict()
        d["capacity"] = capacity_for_venue(engine.venue).to_dict()
        return d

    @app.get("/api/state")
    def get_state():
        return engine.state()

    @app.post("/api/observations", dependencies=[Depends(require_token)])
    def post_observations(obs: List[ObservationIn]):
        sent = [o.sent_at for o in obs if o.sent_at is not None]
        n = engine.ingest([Observation.from_dict(o.model_dump()) for o in obs], sent_at=sent[0] if sent else None)
        return {"accepted": n, "rejected": len(obs) - n}

    @app.get("/api/triggers")
    def list_triggers():
        return TRIGGERS

    @app.post("/api/triggers", dependencies=[Depends(require_token)])
    def set_trigger(t: TriggerIn):
        try:
            engine.set_trigger(t.name, t.active, t.zone_id, by=t.by)
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        return {"ok": True}

    @app.post("/api/actions", dependencies=[Depends(require_token)])
    def execute_action(a: ActionIn):
        return engine.execute(a.action, by=a.by)

    @app.get("/api/log", dependencies=[Depends(require_token)])
    def log_days():
        """Days for which an event record exists (UTC dates)."""
        return {"enabled": event_log is not None, "days": event_log.days() if event_log else []}

    @app.get("/api/log/{day}", dependencies=[Depends(require_token)])
    def log_day(day: str):
        if event_log is None or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", day):
            raise HTTPException(404, "no event record for that day")
        return list(event_log.read(day))

    @app.get("/api/alerts/{alert_id}/cap")
    def alert_cap(alert_id: str):
        for a in engine.alerts.history:
            if a.id == alert_id:
                return Response(alert_to_cap(a, engine.venue), media_type="application/cap+xml")
        raise HTTPException(404, "alert not found")

    @app.get("/api/scenarios")
    def scenarios():
        return {k: load_scenario(k).description for k in SCENARIOS}

    @app.post("/api/demo/start", dependencies=[Depends(require_token)])
    def demo_start(d: DemoIn):
        if not allow_demo:
            raise HTTPException(409, "this is a live server: the demo is disabled. Run  roomtobreathe demo  elsewhere.")
        if d.scenario not in SCENARIOS:
            raise HTTPException(400, f"unknown scenario {d.scenario!r}")
        engine.start_demo(d.scenario)
        cfg["speed"] = d.speed
        return {"ok": True}

    @app.post("/api/demo/stop", dependencies=[Depends(require_token)])
    def demo_stop():
        engine.stop_demo()
        return {"ok": True}

    @app.post("/api/plan/capacity")
    def plan_capacity(c: CapacityIn):
        exits = [Exit(float(e["width_m"]), bool(e.get("stairs", False))) for e in c.exits]
        return compute_capacity(c.usable_area_m2, exits, c.planning_density, c.evac_target_s).to_dict()

    @app.post("/api/plan/risk")
    def plan_risk(p: dict):
        try:
            return assess_risk(EventProfile(**p)).to_dict()
        except TypeError as e:
            raise HTTPException(400, str(e)) from e

    @app.websocket("/ws/live")
    async def ws_live(ws: WebSocket):
        await ws.accept()
        clients.add(ws)
        try:
            await ws.send_text(json.dumps(engine.state(), ensure_ascii=False))
            while True:
                await ws.receive_text()  # keepalive; the client sends nothing meaningful
        except WebSocketDisconnect:
            pass
        finally:
            clients.discard(ws)

    @app.get("/")
    def index():
        return FileResponse(WEB_DIR / "index.html")

    app.mount("/", StaticFiles(directory=WEB_DIR), name="web")

    @app.exception_handler(ValueError)
    async def value_error(_req, exc):
        return JSONResponse({"detail": str(exc)}, status_code=400)

    return app
