"""Web API and page of the Panel RCE (FastAPI). create_app(ctx) builds the app from a PanelContext, so tests run it
against temporary folders and a fake supervisor. All /api routes except setup, login and me need a session cookie;
commands and settings need the command lock («Tomar el mando»)."""
import socket
import subprocess
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from panel import alerts, dryrun, readers, simulator, wincc
from panel.auth import Lock, People, Sessions
from panel.config import EXIT_STATES
from panel.journal import Actions, Diary, DIARY_KINDS
from sc import state_io
from sc.plc_shadow import DEFAULT_SCHEDULE

STATIC = Path(__file__).resolve().parent / "static"
COOKIE = "panel_session"
LOCAL_HOSTS = {"127.0.0.1", "::1", "localhost"}


class PanelContext:
    def __init__(self, root, state_dir, wincc_dir, dades_path, log_path, heartbeat_path, config, supervisor,
                 mode="shadow", local_hosts=None, service_state=readers.service_state, api_health=readers.api_health,
                 hostname=None, service_runner=None):
        self.root = Path(root)
        self.state_dir = Path(state_dir)
        self.wincc_dir = Path(wincc_dir)
        self.dades_path = Path(dades_path)
        self.log_path = Path(log_path)
        self.heartbeat_path = Path(heartbeat_path)
        self.config = config
        self.supervisor = supervisor
        self.mode = mode
        self.local_hosts = set(local_hosts) if local_hosts is not None else set(LOCAL_HOSTS)
        self.service_state = service_state
        self.api_health = api_health
        self.hostname = hostname or socket.gethostname()
        self.service_runner = service_runner or (lambda args: subprocess.run(args, capture_output=True, text=True, timeout=30).returncode)
        self.people = People(config.path)
        self.sessions = Sessions()
        self.lock = Lock()
        self.diary = Diary(self.state_dir / "diario.csv")
        self.actions = Actions(self.state_dir / "acciones.csv")
        self.set_state_seq = int(state_io.read_control(self.state_dir).get("set_state_seq") or 0)
        self._cache = {}

    # --- cached reading of the WinCC archives (re-parsed only when a file grows)
    def readings(self, hours, max_bytes=2_000_000):
        now = datetime.now()
        out = []
        for pattern in wincc.ARCHIVES:
            for path in sorted(self.wincc_dir.glob(pattern)):
                try:
                    st = path.stat()
                except OSError:
                    continue
                key = (str(path), max_bytes)
                cached = self._cache.get(key)
                if cached and cached[0] == (st.st_size, st.st_mtime):
                    rs = cached[1]
                else:
                    rs = wincc.read_recent(path, since=now - timedelta(days=8), max_bytes=max_bytes)
                    self._cache[key] = ((st.st_size, st.st_mtime), rs)
                since = now - timedelta(hours=hours)
                out.extend(r for r in rs if r.ts >= since)
        return out


# ----------------------------------------------------------------------------------------------- request models
class Credentials(BaseModel):
    name: str = Field(min_length=1, max_length=40)
    pin: str = Field(min_length=4, max_length=12)


class DryRunRequest(BaseModel):
    command: str
    state: Optional[str] = None
    alarm: bool = False
    apply: bool = False


class PauseRequest(BaseModel):
    paused: bool


class SimulateRequest(BaseModel):
    now: str
    state: str
    answer: str
    alarm: bool = False


class DiaryEntry(BaseModel):
    kind: str
    text: str = ""


class Settings(BaseModel):
    exit_state: Optional[str] = None
    autostart: Optional[bool] = None
    refresh_s: Optional[int] = Field(default=None, ge=3, le=120)


class ModeRequest(BaseModel):
    mode: str


# ----------------------------------------------------------------------------------------------- helpers
def _iso(dt):
    return dt.isoformat(sep=" ", timespec="seconds") if isinstance(dt, datetime) else dt


def _lock_dict(h):
    return {"name": h["name"], "host": h["host"], "since": _iso(h["since"]), "last": _iso(h["last"])} if h else None


def _reading_dict(r, status=None):
    s = status or {}
    return {"variable": r.variable, "label": r.label, "unit": r.unit, "system": r.system, "tag": r.tag,
            "value": r.value, "valid": r.valid, "ts": _iso(r.ts), "source": r.source,
            "age_s": s.get("age_s"), "stale": s.get("stale", False), "frozen": s.get("frozen", False)}


def _pick(latest, variable):
    for system in ("RCE-a", "RCE-b", "RCE"):
        if (variable, system) in latest:
            return latest[(variable, system)]
    return None


def create_app(ctx: PanelContext) -> FastAPI:
    app = FastAPI(title="Panel RCE", docs_url=None, redoc_url=None)
    if STATIC.exists():
        app.mount("/static", StaticFiles(directory=str(STATIC)), name="static")

    # --- auth
    def user_of(request: Request):
        return ctx.sessions.get(request.cookies.get(COOKIE, ""))

    def require_user(request: Request):
        u = user_of(request)
        if not u:
            raise HTTPException(401, "Hay que entrar con nombre y PIN")
        return u

    def require_lock(user):
        h = ctx.lock.holder()
        if not h or h["name"] != user["name"]:
            raise HTTPException(423, detail={"message": "Hace falta tener el mando", "lock": _lock_dict(h)})
        ctx.lock.touch(user["name"])
        return h

    def log_action(user, action, result="ok", duration_s=0.0):
        ctx.actions.add(author=user["name"], action=action, mode=ctx.mode, result=result, duration_s=duration_s,
                        host=user.get("host", ""))

    @app.exception_handler(HTTPException)
    async def http_exc(request, exc):
        detail = exc.detail if isinstance(exc.detail, dict) else {"message": exc.detail}
        return JSONResponse(status_code=exc.status_code, content={"detail": detail.get("message"), **detail})

    # --- page
    @app.get("/")
    def index():
        return FileResponse(STATIC / "index.html")

    # --- session
    @app.get("/api/me")
    def me(request: Request):
        u = user_of(request)
        return {"name": u["name"] if u else None, "people_exist": bool(ctx.people.names()),
                "lock": _lock_dict(ctx.lock.holder()), "mode": ctx.mode, "host": request.client.host}

    @app.post("/api/setup")
    def setup(body: Credentials, request: Request, response: Response):
        if ctx.people.names():
            raise HTTPException(409, "Ya hay personas dadas de alta: entra con tu PIN")
        if request.client.host not in ctx.local_hosts:
            raise HTTPException(403, "La primera persona solo se crea desde el propio PC del panel")
        if not body.pin.isdigit():
            raise HTTPException(422, "El PIN son solo cifras (4 a 12)")
        ctx.people.add(body.name.strip(), body.pin)
        return {"ok": True, "people": ctx.people.names()}

    @app.post("/api/login")
    def login(body: Credentials, request: Request, response: Response):
        name = body.name.strip()
        if not ctx.people.verify(name, body.pin):
            raise HTTPException(401, "Nombre o PIN incorrectos")
        token = ctx.sessions.create(name, host=request.client.host)
        response.set_cookie(COOKIE, token, httponly=True, samesite="lax", max_age=12 * 3600)
        return {"ok": True, "name": name}

    @app.post("/api/logout")
    def logout(request: Request, response: Response):
        ctx.sessions.drop(request.cookies.get(COOKIE, ""))
        response.delete_cookie(COOKIE)
        return {"ok": True}

    # --- status
    def build_status(user):
        now = datetime.now()
        rs = ctx.readings(hours=ctx.config.get("readings_hours"))
        latest = wincc.latest_values(rs)
        series = wincc.series_status(rs, now)
        wincc_last = max((r.ts for r in rs), default=None)
        sup = ctx.supervisor.status(now)
        status = readers.read_status(ctx.state_dir, now)
        hb = readers.heartbeat_age(ctx.heartbeat_path, now)
        fb = ctx.service_state("filebeat")
        api_running = sup["api"]["running"] or not sup["wanted"]
        api_ok = ctx.api_health() if sup["api"]["running"] else (not sup["wanted"])
        flow = _pick(latest, "flow_rce_l_min")
        active = alerts.evaluate({
            "now": now, "mode": ctx.mode, "wincc_last_write": wincc_last,
            "series": {k: v for k, v in series.items() if k[0] in wincc.MAIN_VARIABLES},
            "heartbeat_age_s": hb, "sc_running": sup["sc"]["running"] or not sup["wanted"],
            "api_running": api_running, "api_ok": api_ok, "status": status or {},
            "filebeat": fb, "disk_free_gb": readers.disk_free_gb(ctx.root),
            "plc_alarm": False, "cover_measured": (status or {}).get("cover_measured", ""),
            "flow_l_min": flow.value if flow else None,
        })
        if not sup["wanted"]:
            active = [a for a in active if a.id in ("sc_stopped", "wincc_stale", "filebeat_down", "filebeat_missing", "disk_low")
                      or a.id.startswith("probe:")]
            if not any(a.id == "sc_stopped" for a in active):
                active.insert(0, alerts.Alert("sc_stopped", "critical", "El control está parado", "Pulsar «Arrancar»"))
        wincc_age = (now - wincc_last).total_seconds() if wincc_last else None
        lights = {
            "wincc": "ok" if wincc_age is not None and wincc_age < alerts.WINCC_STALE.total_seconds() else "bad",
            "api": ("ok" if api_ok else "bad") if sup["wanted"] else "stopped",
            "sc": ("ok" if sup["sc"]["running"] and hb is not None and hb < alerts.SC_DEAD_S else "bad") if sup["wanted"] else "stopped",
            "filebeat": {"running": "ok", "stopped": "bad", "missing": "missing"}.get(fb, "unknown"),
        }
        readings = []
        for var in wincc.MAIN_VARIABLES:
            r = _pick(latest, var)
            if r:
                readings.append(_reading_dict(r, series.get((r.variable, r.system))))
        return {
            "now": _iso(now), "mode": ctx.mode, "me": user["name"], "host": ctx.hostname,
            "lock": _lock_dict(ctx.lock.holder()), "supervisor": sup,
            "wincc": {"last_write": _iso(wincc_last), "age_s": wincc_age, "folder": str(ctx.wincc_dir)},
            "control": status, "heartbeat_age_s": hb, "filebeat": fb, "lights": lights,
            "readings": readings, "alerts": [a.as_dict() for a in active],
            "refresh_s": ctx.config.get("refresh_s"),
        }

    @app.get("/api/status")
    def get_status(request: Request):
        return build_status(require_user(request))

    @app.get("/api/readings")
    def get_readings(request: Request, hours: int = 6):
        require_user(request)
        now = datetime.now()
        rs = ctx.readings(hours=max(1, min(hours, 48)), max_bytes=4_000_000)
        latest = wincc.latest_values(rs)
        series = wincc.series_status(rs, now)
        by_key = {}
        for r in rs:
            by_key.setdefault((r.variable, r.system), []).append(r)
        out = []
        for key in sorted(latest, key=lambda k: (k[0] not in wincc.MAIN_VARIABLES,
                                                 wincc.MAIN_VARIABLES.index(k[0]) if k[0] in wincc.MAIN_VARIABLES else 0, k)):
            d = _reading_dict(latest[key], series.get(key))
            pts = sorted(by_key[key], key=lambda r: r.ts)
            step = max(1, len(pts) // 120)
            d["points"] = [[_iso(p.ts), p.value] for p in pts[::step] if p.value is not None]
            out.append(d)
        return {"now": _iso(now), "hours": hours, "readings": out,
                "used_by_control": (readers.read_status(ctx.state_dir, now) or {}).get("inputs", {})}

    @app.get("/api/history")
    def get_history(request: Request, hours: int = 24):
        require_user(request)
        now = datetime.now()
        hours = max(1, min(hours, 24 * 14))
        rs = ctx.readings(hours=hours, max_bytes=4_000_000 if hours <= 24 else 20_000_000)
        by_key = {}
        for r in rs:
            by_key.setdefault((r.variable, r.system), []).append(r)
        series = {}
        for var in wincc.MAIN_VARIABLES:
            for system, suffix in (("RCE-a", ""), ("RCE-b", "@b")):
                pts = by_key.get((var, system))
                if not pts:
                    continue
                pts.sort(key=lambda r: r.ts)
                step = max(1, len(pts) // 600)
                label, unit = wincc.LABELS.get(var, (var, ""))
                series[var + suffix] = {"label": label + (" (B)" if suffix else ""), "unit": unit,
                                        "points": [[_iso(p.ts), p.value] for p in pts[::step] if p.value is not None]}
        decisions = readers.read_decisions(ctx.dades_path, since=now - timedelta(hours=hours))
        for d in decisions:
            d["ts"] = _iso(d["ts"])
        events = readers.read_events(ctx.log_path, max_lines=300)
        for e in events:
            e["ts"] = _iso(e["ts"])
        events = [e for e in events if not e["ts"] or e["ts"] >= _iso(now - timedelta(hours=hours))]
        return {"now": _iso(now), "hours": hours, "series": series, "decisions": decisions,
                "events": events + ctx.supervisor.status(now).get("events", [])}

    # --- system
    @app.post("/api/system/{action}")
    def system_action(action: str, request: Request):
        u = require_user(request)
        if action not in ("start", "stop", "restart"):
            raise HTTPException(404, "Acción desconocida")
        getattr(ctx.supervisor, action)()
        log_action(u, f"system:{action}")
        return {"ok": True, "supervisor": ctx.supervisor.status()}

    @app.post("/api/system/restart/{name}")
    def system_restart_one(name: str, request: Request):
        u = require_user(request)
        if name not in ("api", "sc"):
            raise HTTPException(404, "Proceso desconocido")
        ctx.supervisor.restart_one(name)
        log_action(u, f"system:restart:{name}")
        return {"ok": True, "supervisor": ctx.supervisor.status()}

    @app.post("/api/filebeat/restart")
    def filebeat_restart(request: Request):
        u = require_user(request)
        ctx.service_runner(["sc", "stop", "filebeat"])
        code = ctx.service_runner(["sc", "start", "filebeat"])
        log_action(u, "filebeat:restart", result="ok" if code == 0 else f"error {code}")
        return {"ok": code == 0, "filebeat": ctx.service_state("filebeat")}

    # --- lock
    @app.post("/api/lock/take")
    def lock_take(request: Request):
        u = require_user(request)
        ok, holder = ctx.lock.take(u["name"], u.get("host", ""))
        if not ok:
            raise HTTPException(423, detail={"message": f"El mando lo tiene {holder}", "lock": _lock_dict(ctx.lock.holder())})
        return {"ok": True, "lock": _lock_dict(ctx.lock.holder())}

    @app.post("/api/lock/release")
    def lock_release(request: Request):
        u = require_user(request)
        ctx.lock.release(u["name"])
        return {"ok": True, "lock": _lock_dict(ctx.lock.holder())}

    # --- commands (dry run) and pause
    @app.post("/api/dryrun")
    def post_dryrun(body: DryRunRequest, request: Request):
        u = require_user(request)
        require_lock(u)
        if body.command not in dryrun.COMMANDS:
            raise HTTPException(422, "Mando desconocido")
        status = readers.read_status(ctx.state_dir, datetime.now()) or {}
        state = body.state or status.get("plc_state") or "Parada"
        t0 = datetime.now()
        result = dryrun.run(body.command, state=state, alarm=body.alarm)
        result["simulated"] = True
        result["applied"] = False
        if body.apply and ctx.mode == "shadow" and result["state_after"]:
            ctx.set_state_seq += 1
            state_io.write_control(ctx.state_dir, set_state=result["state_after"], set_state_seq=ctx.set_state_seq)
            result["applied"] = True
            cover_es = {"open": "abierta", "closed": "cerrada"}.get(result["cover_after"], result["cover_after"])
            ctx.diary.add(author=u["name"], kind="mando", host=u.get("host", ""),
                          text=f"{result['label']} (simulado): estado virtual {state} → {result['state_after']}, tapa {cover_es}")
        log_action(u, f"dryrun:{body.command}", result="aborted" if result["aborted"] else "ok",
                   duration_s=(datetime.now() - t0).total_seconds())
        return result

    @app.post("/api/control/pause")
    def control_pause(body: PauseRequest, request: Request):
        u = require_user(request)
        require_lock(u)
        now = _iso(datetime.now())
        state_io.write_control(ctx.state_dir, paused=body.paused, paused_by=u["name"] if body.paused else "",
                               since=now if body.paused else "")
        log_action(u, "control:pause" if body.paused else "control:resume")
        return {"ok": True, "control": state_io.read_control(ctx.state_dir)}

    # --- simulator
    @app.post("/api/simulate")
    def post_simulate(body: SimulateRequest, request: Request):
        require_user(request)
        when = readers.parse_ts(body.now) or readers.parse_ts(body.now + ":00")
        if not when:
            raise HTTPException(422, "Fecha y hora no válidas (AAAA-MM-DDTHH:MM)")
        status = readers.read_status(ctx.state_dir, datetime.now()) or {}
        schedule = status.get("schedule") or DEFAULT_SCHEDULE
        return simulator.quick(when, body.state, body.answer, body.alarm, schedule)

    # --- diary and actions
    @app.get("/api/diary")
    def get_diary(request: Request, n: int = 50):
        require_user(request)
        rows = ctx.diary.last(n)
        return rows

    @app.post("/api/diary")
    def post_diary(body: DiaryEntry, request: Request):
        u = require_user(request)
        if body.kind not in DIARY_KINDS:
            raise HTTPException(422, "Tipo de anotación desconocido")
        return ctx.diary.add(author=u["name"], kind=body.kind, text=body.text.strip(), host=u.get("host", ""))

    @app.get("/api/actions")
    def get_actions(request: Request, n: int = 50):
        require_user(request)
        return ctx.actions.last(n)

    @app.get("/api/events")
    def get_events(request: Request):
        require_user(request)
        ev = readers.read_events(ctx.log_path, max_lines=100)
        for e in ev:
            e["ts"] = _iso(e["ts"])
        return {"control": ev, "supervisor": ctx.supervisor.status().get("events", [])}

    # --- settings, people, mode
    @app.get("/api/settings")
    def get_settings(request: Request):
        require_user(request)
        return {**ctx.config.all(), "people": ctx.people.names(), "mode": ctx.mode, "wincc_dir": str(ctx.wincc_dir),
                "exit_states": EXIT_STATES, "diary_kinds": DIARY_KINDS, "host": ctx.hostname}

    @app.post("/api/settings")
    def post_settings(body: Settings, request: Request):
        u = require_user(request)
        require_lock(u)
        fields = {k: v for k, v in body.model_dump().items() if v is not None}
        if "exit_state" in fields and fields["exit_state"] not in EXIT_STATES:
            raise HTTPException(422, "Estado de salida desconocido")
        ctx.config.set(**fields)
        log_action(u, "settings:" + ",".join(f"{k}={v}" for k, v in fields.items()))
        return {"ok": True, **ctx.config.all()}

    @app.post("/api/people")
    def post_people(body: Credentials, request: Request):
        u = require_user(request)
        require_lock(u)
        if not body.pin.isdigit():
            raise HTTPException(422, "El PIN son solo cifras (4 a 12)")
        ctx.people.add(body.name.strip(), body.pin)
        log_action(u, f"people:add:{body.name.strip()}")
        return {"ok": True, "people": ctx.people.names()}

    @app.delete("/api/people/{name}")
    def delete_people(name: str, request: Request):
        u = require_user(request)
        require_lock(u)
        if name == u["name"]:
            raise HTTPException(409, "No puedes borrarte a ti mismo")
        ctx.people.remove(name)
        log_action(u, f"people:remove:{name}")
        return {"ok": True, "people": ctx.people.names()}

    @app.post("/api/mode")
    def post_mode(body: ModeRequest, request: Request):
        u = require_user(request)
        require_lock(u)
        raise HTTPException(501, "El cambio de modo (REAL) se activa en la fase 3, con autorización del equipo")

    return app
