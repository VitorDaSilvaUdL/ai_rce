"""Web API and page of the Panel RCE (FastAPI). create_app(ctx) builds the app from a PanelContext, so tests run it
against temporary folders and a fake supervisor. All /api routes except setup, login and me need a session cookie;
commands and settings need the command lock («Tomar el mando»)."""
import socket
import subprocess
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

import json

from panel import agenda as agenda_mod
from panel import alerts, dryrun, plc_ops, readers, simfull, simulator, wincc
from panel.auth import ADMIN, VIEWER, Lock, People, Sessions, role_of, user_id
from panel.journal import ACTIONS_HEADER, CATEGORIES, category_of
from panel.config import EXIT_STATES
from panel.journal import Actions, Diary, DIARY_KINDS, OPERATIONS
from sc import state_io
from sc.plc_shadow import DEFAULT_SCHEDULE

STATIC = Path(__file__).resolve().parent / "static"
COOKIE = "panel_session"
LOCAL_HOSTS = {"127.0.0.1", "::1", "localhost"}
REAL_MAX_FAILURES, REAL_LOCK_S = 5, 600     # wrong REAL passwords tolerated in the lock window, and that window


class PanelContext:
    def __init__(self, root, state_dir, wincc_dir, dades_path, log_path, heartbeat_path, config, supervisor,
                 mode="shadow", local_hosts=None, service_state=readers.service_state, api_health=readers.api_health,
                 hostname=None, service_runner=None, predict=None, plc=None, sc_config_path=None, probe=None,
                 apply_exit_state=None, plc_factory=None, port_users=None):
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
        self.real_failures = []     # monotonic times of the wrong REAL passwords (lockout)
        self.service_state = service_state
        self.api_health = api_health
        self.hostname = hostname or socket.gethostname()
        self.service_runner = service_runner or (lambda args: subprocess.run(args, capture_output=True, text=True, timeout=30).returncode)
        self.predict = predict or self._http_predict
        # --- PLC (phase 3): parameters from sc/config.<env>.json, operations injectable for tests
        self.plc = plc or {"ip": "127.0.0.1", "rack": 0, "slot": 1}
        self.sc_config_path = Path(sc_config_path) if sc_config_path else None
        ip, rack, slot = self.plc["ip"], int(self.plc.get("rack", 0)), int(self.plc.get("slot", 1))
        self.probe = probe or (lambda: plc_ops.probe(ip, rack, slot))
        self.apply_exit_state = apply_exit_state or (lambda st: plc_ops.apply_exit_state(ip, rack, slot, st))
        self.plc_factory = plc_factory or (lambda: plc_ops.ProgressPLC(ip, rack, slot))
        self.port_users = port_users or plc_ops.port_users
        self.jobs = plc_ops.JobRunner()
        self._probe_cache = (None, None)
        self.collector = None            # set by panel.main (K1/K2/K3 writers)
        self.people = People(config.path)
        self.sessions = Sessions()
        self.lock = Lock()
        self.diary = Diary(self.state_dir / "diario.csv")
        self.actions = Actions(self.state_dir / "acciones.csv")
        self.set_state_seq = int(state_io.read_control(self.state_dir).get("set_state_seq") or 0)
        self._cache = {}
        # schedule (agenda): shadow entries change the virtual state; real ones (rce_admin) run PLC jobs
        self.agenda = agenda_mod.Agenda(self.state_dir / "agenda.json")
        self.scheduler = agenda_mod.Scheduler(
            self.agenda,
            executors={"shadow": agenda_mod.ShadowExecutor(self.state_dir, next_seq=self.next_seq),
                       "real": agenda_mod.RealExecutor(self.state_dir, jobs=self.jobs, plc_factory=lambda: self.plc_factory(),
                                                       probe=lambda: self.probe())},
            mode=lambda: self.mode,
            audit=lambda author, action, result: self.actions.add(author=author, action=action, mode=self.mode,
                                                                  result=result, host="agenda"),
            trial=self._agenda_trial)

    def next_seq(self):
        self.set_state_seq += 1
        return self.set_state_seq

    def _agenda_trial(self, kind, text, author):
        self.diary.add(author=author, kind=kind, text=text, host="agenda", operation="manual" if kind == "ensayo_inicio" else "")
        if self.collector is not None:
            try:
                self.collector.run_once()
            except Exception:
                pass

    def _http_predict(self, samples):
        """POST the 24-sample window to the prediction API (the same call the control makes)."""
        import requests
        url = self.config.get("api_url").rstrip("/") + "/predict"
        r = requests.post(url, json=samples, headers={"Content-Type": "application/json"}, timeout=90)
        r.raise_for_status()
        return r.json()

    def probe_cached(self, max_age_s=60):
        ts, result = self._probe_cache
        if ts is None or (datetime.now() - ts).total_seconds() > max_age_s:
            result = self.probe()
            self._probe_cache = (datetime.now(), result)
        return result

    def set_plc_mode(self, mode):
        """plc.mode in the control's config file (the control reads it when it starts)."""
        if self.sc_config_path:
            cfg = json.loads(self.sc_config_path.read_text(encoding="utf-8"))
            cfg.setdefault("plc", {})["mode"] = mode
            self.sc_config_path.write_text(json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8")
        self.mode = mode
        self._probe_cache = (None, None)

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
    pin: str = Field(min_length=4, max_length=32)   # PIN or password: letters, digits and symbols


def check_pin(pin):
    if not pin.strip():
        raise HTTPException(422, "El PIN o contraseña no puede ser solo espacios (4 a 32 caracteres)")


class AgendaRequest(BaseModel):
    kind: str                         # "estado" | "mando"
    target: str                       # state (ColdMode1...) or command (open_cover...)
    start: Optional[str] = None       # AAAA-MM-DDTHH:MM; empty = now (state)
    end: Optional[str] = None         # state only
    at_end: str = "control"
    trial: bool = True


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
    frame: str = "cold"      # mode of the planned frame when the answer is «sí»


class SimulateFullRequest(BaseModel):
    now: Optional[str] = None
    state: str = "Parada"
    alarm: bool = False
    overrides: dict = Field(default_factory=dict)
    demand_kwh: Optional[float] = None


class ReplayRequest(BaseModel):
    date: str
    state: str = "Parada"


class DiaryEntry(BaseModel):
    kind: str
    text: str = ""
    operation: str = ""


class Settings(BaseModel):
    exit_state: Optional[str] = None
    autostart: Optional[bool] = None
    refresh_s: Optional[int] = Field(default=None, ge=3, le=120)
    plc_read_allowed: Optional[bool] = None


class ModeRequest(BaseModel):
    mode: str
    confirm: str = ""
    password: str = ""


class PasswordChange(BaseModel):
    current: str
    new: str = Field(min_length=4, max_length=32)


class RealPasswordRequest(BaseModel):
    password: str
    current: str = ""


class CommandRequest(BaseModel):
    command: str
    confirm: bool = False


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

    def require_user(request: Request, allow_must_change=False):
        u = user_of(request)
        if not u:
            raise HTTPException(401, "Hay que entrar con nombre y PIN")
        if not allow_must_change and ctx.people.must_change(u["name"]):
            raise HTTPException(403, detail={"message": "Cambia tu contraseña antes de seguir", "must_change": True})
        return u

    def require_writer(user):
        if role_of(user["name"]) == "viewer":
            raise HTTPException(403, "La cuenta de visualización solo puede mirar")
        return user

    def require_admin(user):
        if role_of(user["name"]) != "admin":
            raise HTTPException(403, f"Solo {ADMIN} puede hacer esto")
        return user

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
                "admin_exists": ADMIN in ctx.people.names(), "role": role_of(u["name"]) if u else None,
                "must_change": ctx.people.must_change(u["name"]) if u else False,
                "lock": _lock_dict(ctx.lock.holder()), "mode": ctx.mode, "host": request.client.host}

    @app.post("/api/setup")
    def setup(body: Credentials, request: Request, response: Response):
        if user_id(body.name) != ADMIN:
            raise HTTPException(422, f"La primera cuenta es la de superadministración: {ADMIN}")
        if ADMIN in ctx.people.names():
            raise HTTPException(409, f"{ADMIN} ya existe: entra con tu usuario")
        if request.client.host not in ctx.local_hosts:
            raise HTTPException(403, f"{ADMIN} solo se crea desde el propio PC del panel")
        check_pin(body.pin)
        ctx.people.add(user_id(body.name), body.pin)
        return {"ok": True, "people": ctx.people.names()}

    @app.post("/api/login")
    def login(body: Credentials, request: Request, response: Response):
        name = user_id(body.name)
        if not ctx.people.verify(name, body.pin):
            ctx.actions.add(author=name, action="login", mode=ctx.mode, result="error: usuario o contraseña incorrectos",
                            host=request.client.host)
            raise HTTPException(401, "Nombre o PIN incorrectos")
        token = ctx.sessions.create(name, host=request.client.host)
        ctx.actions.add(author=name, action="login", mode=ctx.mode, result="ok", host=request.client.host)
        response.set_cookie(COOKIE, token, httponly=True, samesite="lax", max_age=12 * 3600)
        return {"ok": True, "name": name, "role": role_of(name), "must_change": ctx.people.must_change(name)}

    @app.post("/api/password")
    def change_password(body: PasswordChange, request: Request):
        """Each person changes their own PIN or password (mandatory at the first login of imported users)."""
        u = require_user(request, allow_must_change=True)
        require_writer(u)
        if not ctx.people.verify(u["name"], body.current):
            raise HTTPException(403, "La contraseña actual no es correcta")
        check_pin(body.new)
        if user_id(body.new) == u["name"] or body.new == body.current:
            raise HTTPException(422, "La nueva contraseña no puede ser tu usuario ni la actual")
        ctx.people.add(u["name"], body.new, must_change=False)
        log_action(u, "password:change")
        return {"ok": True}

    @app.post("/api/logout")
    def logout(request: Request, response: Response):
        u = user_of(request)
        if u:
            log_action(u, "logout")
        ctx.sessions.drop(request.cookies.get(COOKIE, ""))
        response.delete_cookie(COOKIE)
        return {"ok": True}

    # --- status
    def _agenda_summary():
        entries = ctx.agenda.entries()
        running = next((e for e in entries if e["kind"] == "estado" and e["status"] == "en_curso"), None)
        pending = sorted((e for e in entries if e["status"] == "pendiente"), key=lambda e: e["start"])
        return {"running": running, "next": pending[0] if pending else None, "pending": len(pending)}

    def _agenda_alerts(now):
        out = []
        for e in ctx.agenda.entries():
            when = readers.parse_ts(e.get("started_at") or e.get("ended_at") or e["start"])
            if e["status"] == "fallida" and when and now - when < timedelta(hours=12):
                out.append({"id": f"agenda:{e['id']}", "level": "warning",
                            "title": f"Agenda: no se pudo ejecutar «{e['label']}» ({e['start'][11:16]})",
                            "action": "Revisar la pestaña Agenda", "detail": e.get("message", "")})
        return out

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
        plc = ctx.probe_cached(60) if ctx.mode == "real" else None
        plc_alarm = bool(plc and plc.get("ok") and any((plc.get("alarms") or {}).values()))
        active = alerts.evaluate({
            "now": now, "mode": ctx.mode, "wincc_last_write": wincc_last,
            "series": {k: v for k, v in series.items() if k[0] in wincc.MAIN_VARIABLES},
            "heartbeat_age_s": hb, "sc_running": sup["sc"]["running"] or not sup["wanted"],
            "api_running": api_running, "api_ok": api_ok, "status": status or {},
            "filebeat": fb, "disk_free_gb": readers.disk_free_gb(ctx.root),
            "plc_alarm": plc_alarm,
            "cover_measured": (plc or {}).get("cover") or (status or {}).get("cover_measured", ""),
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
        if plc is not None:
            lights["plc"] = "ok" if plc.get("ok") else "bad"
            if plc.get("ok") and not plc_alarm and ctx.mode == "real":
                pass
            elif plc.get("ok") and plc_alarm:
                lights["plc"] = "alarm"
        if ctx.mode == "real" and plc and not plc.get("ok"):
            active.insert(0, alerts.Alert("plc_down", "critical", "El PLC no responde", "Comprobar la red con el PLC; el control no puede actuar",
                                          plc.get("error", "")))
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
            "readings": readings, "alerts": [a.as_dict() for a in active] + _agenda_alerts(now),
            "agenda": _agenda_summary(),
            "refresh_s": ctx.config.get("refresh_s"), "plc": plc, "job": ctx.jobs.status(),
            "plc_ip": ctx.plc["ip"], "trial": ctx.diary.open_trial_info(),
            "collector": ({"last_run": _iso(ctx.collector.last_run), "last_error": ctx.collector.last_error}
                          if ctx.collector else None),
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
        require_writer(u)
        if action not in ("start", "stop", "restart"):
            raise HTTPException(404, "Acción desconocida")
        getattr(ctx.supervisor, action)()
        log_action(u, f"system:{action}")
        return {"ok": True, "supervisor": ctx.supervisor.status()}

    @app.post("/api/system/restart/{name}")
    def system_restart_one(name: str, request: Request):
        u = require_user(request)
        require_writer(u)
        if name not in ("api", "sc"):
            raise HTTPException(404, "Proceso desconocido")
        ctx.supervisor.restart_one(name)
        log_action(u, f"system:restart:{name}")
        return {"ok": True, "supervisor": ctx.supervisor.status()}

    @app.post("/api/filebeat/restart")
    def filebeat_restart(request: Request):
        u = require_user(request)
        require_writer(u)
        ctx.service_runner(["sc", "stop", "filebeat"])
        code = ctx.service_runner(["sc", "start", "filebeat"])
        log_action(u, "filebeat:restart", result="ok" if code == 0 else f"error {code}")
        return {"ok": code == 0, "filebeat": ctx.service_state("filebeat")}

    # --- lock
    @app.post("/api/lock/take")
    def lock_take(request: Request):
        u = require_user(request)
        require_writer(u)
        had = (ctx.lock.holder() or {}).get("name") == u["name"]
        ok, holder = ctx.lock.take(u["name"], u.get("host", ""))
        if not ok:
            log_action(u, "lock:take", result=f"error: lo tiene {holder}")
            raise HTTPException(423, detail={"message": f"El mando lo tiene {holder}", "lock": _lock_dict(ctx.lock.holder())})
        if not had:
            log_action(u, "lock:take")
        return {"ok": True, "lock": _lock_dict(ctx.lock.holder())}

    @app.post("/api/lock/release")
    def lock_release(request: Request):
        u = require_user(request)
        require_writer(u)
        if ctx.lock.release(u["name"]):
            log_action(u, "lock:release")
        return {"ok": True, "lock": _lock_dict(ctx.lock.holder())}

    # --- commands (dry run) and pause
    @app.post("/api/dryrun")
    def post_dryrun(body: DryRunRequest, request: Request):
        u = require_user(request)
        require_writer(u)
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
            state_io.write_control(ctx.state_dir, set_state=result["state_after"], set_state_seq=ctx.next_seq())
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
        require_writer(u)
        require_lock(u)
        now = _iso(datetime.now())
        state_io.write_control(ctx.state_dir, paused=body.paused, paused_by=u["name"] if body.paused else "",
                               since=now if body.paused else "")
        log_action(u, "control:pause" if body.paused else "control:resume")
        return {"ok": True, "control": state_io.read_control(ctx.state_dir)}

    # --- simulator
    def _control_policy():
        try:
            cfg = json.loads(Path(ctx.sc_config_path).read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            cfg = {}
        return (cfg.get("control") or {}).get("next_state_policy", "frame_mode")

    @app.post("/api/simulate")
    def post_simulate(body: SimulateRequest, request: Request):
        require_user(request)
        when = readers.parse_ts(body.now) or readers.parse_ts(body.now + ":00")
        if not when:
            raise HTTPException(422, "Fecha y hora no válidas (AAAA-MM-DDTHH:MM)")
        status = readers.read_status(ctx.state_dir, datetime.now()) or {}
        schedule = status.get("schedule") or DEFAULT_SCHEDULE
        return simulator.quick(when, body.state, body.answer, body.alarm, schedule, frame=body.frame, policy=_control_policy())

    @app.post("/api/simulate_full")
    def post_simulate_full(body: SimulateFullRequest, request: Request):
        u = require_user(request)
        now = readers.parse_ts(body.now) or readers.parse_ts((body.now or "") + ":00") if body.now else datetime.now().replace(second=0, microsecond=0)
        if not now:
            raise HTTPException(422, "Fecha y hora no válidas (AAAA-MM-DDTHH:MM)")
        status = readers.read_status(ctx.state_dir, datetime.now()) or {}
        schedule = status.get("schedule") or DEFAULT_SCHEDULE
        overrides = {k: float(v) for k, v in body.overrides.items() if k in simfull.SAMPLE_VARS and v not in (None, "")}
        rs = ctx.readings(hours=24 * 8, max_bytes=20_000_000)
        t0 = datetime.now()
        try:
            result = simfull.simulate(now, rs, overrides, body.state, body.alarm, ctx.predict, schedule,
                                      demand_j=body.demand_kwh * 3.6e6 if body.demand_kwh is not None else None)
        except Exception as e:
            raise HTTPException(503, f"No se ha podido obtener la previsión del modelo: {e}")
        log_action(u, "simulate_full", duration_s=(datetime.now() - t0).total_seconds())
        return result

    @app.post("/api/replay")
    def post_replay(body: ReplayRequest, request: Request):
        u = require_user(request)
        try:
            day = datetime.strptime(body.date, "%Y-%m-%d").date()
        except ValueError:
            raise HTTPException(422, "Fecha no válida (AAAA-MM-DD)")
        status = readers.read_status(ctx.state_dir, datetime.now()) or {}
        schedule = status.get("schedule") or DEFAULT_SCHEDULE
        rs = ctx.readings(hours=24 * 8, max_bytes=20_000_000)
        t0 = datetime.now()
        try:
            result = simfull.replay_day(day, rs, body.state, ctx.predict, schedule)
        except ValueError as e:
            raise HTTPException(422, str(e))
        except Exception as e:
            raise HTTPException(503, f"No se ha podido obtener la previsión del modelo: {e}")
        log_action(u, f"replay:{body.date}", duration_s=(datetime.now() - t0).total_seconds())
        return result

    # --- diary and actions
    @app.get("/api/diary")
    def get_diary(request: Request, n: int = 50):
        require_user(request)
        rows = ctx.diary.last(n)
        return rows

    @app.post("/api/diary")
    def post_diary(body: DiaryEntry, request: Request):
        u = require_user(request)
        require_writer(u)
        if body.kind not in DIARY_KINDS:
            raise HTTPException(422, "Tipo de anotación desconocido")
        if body.operation and body.operation not in OPERATIONS:
            raise HTTPException(422, "Tipo de operación desconocido")
        row = ctx.diary.add(author=u["name"], kind=body.kind, text=body.text.strip(), host=u.get("host", ""),
                            operation=body.operation)
        log_action(u, f"diario:{body.kind}")
        if body.kind in ("ensayo_inicio", "ensayo_fin") and ctx.collector is not None:
            try:
                ctx.collector.run_once()          # write the trial summary right away
            except Exception:
                pass
        return row

    # --- schedule (agenda)
    def _parse_local(s, what):
        if not s:
            return None
        t = readers.parse_ts(s) or readers.parse_ts(s + ":00")
        if not t:
            raise HTTPException(422, f"Fecha y hora de {what} no válidas")
        return t

    @app.get("/api/agenda")
    def get_agenda(request: Request):
        require_user(request)
        entries = sorted(ctx.agenda.entries(), key=lambda e: (e["status"] not in ("en_curso", "pendiente"), e["start"]))
        return {"entries": entries, "states": agenda_mod.STATES, "at_end": agenda_mod.AT_END, "status_labels": agenda_mod.STATUS,
                "commands": {k: v[0] for k, v in dryrun.COMMANDS.items()}, "mode": ctx.mode, "now": _iso(datetime.now())}

    @app.post("/api/agenda")
    def post_agenda(body: AgendaRequest, request: Request):
        u = require_user(request)
        require_writer(u)
        require_lock(u)
        start, end = _parse_local(body.start, "inicio"), _parse_local(body.end, "fin")
        try:
            if body.kind == "estado":
                e = ctx.agenda.add_state(u["name"], body.target, start, end, at_end=body.at_end, trial=body.trial)
            elif body.kind == "mando":
                e = ctx.agenda.add_command(u["name"], body.target, start)
            else:
                raise ValueError("Tipo de entrada desconocido")
        except ValueError as exc:
            raise HTTPException(422, str(exc))
        span = f"{e['start'][:16]}" + (f" – {e['end'][:16]}" if e.get("end") else "")
        log_action(u, f"agenda:crear:#{e['id']} {e['label']} {span}")
        # an entry that starts now begins right away; in REAL mode the PLC job can take a minute: do not block the request
        if ctx.mode == "real":
            threading.Thread(target=ctx.scheduler.tick, name="agenda-now", daemon=True).start()
        else:
            ctx.scheduler.tick()
        return ctx.agenda.get(e["id"])

    @app.post("/api/agenda/{entry_id}/cancel")
    def cancel_agenda(entry_id: int, request: Request):
        u = require_user(request)
        require_writer(u)
        require_lock(u)
        e = ctx.agenda.get(entry_id)
        if not e:
            raise HTTPException(404, "No existe esa entrada")
        if e["author"] != u["name"] and role_of(u["name"]) != "admin":
            raise HTTPException(403, f"Solo quien la creó ({e['author']}) o {ADMIN} pueden cancelarla")
        try:
            ctx.scheduler.cancel(entry_id, u["name"])
        except ValueError as exc:
            raise HTTPException(409, str(exc))
        return ctx.agenda.get(entry_id)

    @app.get("/api/actions")
    def get_actions(request: Request, n: int = 50):
        require_user(request)
        return ctx.actions.last(n)

    def _audit_rows(user="", category="", since="", until="", n=500):
        t0 = readers.parse_ts(since) or readers.parse_ts((since or "") + ":00") if since else None
        t1 = readers.parse_ts(until) or readers.parse_ts((until or "") + ":00") if until else None
        out = []
        for r in reversed(ctx.actions.rows()):
            ts = readers.parse_ts(r["timestamp"])
            if user and r["autor"] != user_id(user):
                continue
            if category and category_of(r["accion"]) != category:
                continue
            if (t0 and ts and ts < t0) or (t1 and ts and ts > t1):
                continue
            out.append({**r, "categoria": category_of(r["accion"])})
            if len(out) >= n:
                break
        return out

    @app.get("/api/audit")
    def get_audit(request: Request, user: str = "", category: str = "", since: str = "", until: str = "", n: int = 500):
        require_user(request)
        rows = _audit_rows(user, category, since, until, max(1, min(n, 5000)))
        return {"rows": rows, "users": sorted({r["autor"] for r in ctx.actions.rows()} | set(ctx.people.names())),
                "categories": sorted(set(CATEGORIES.values()) | {"otros"})}

    @app.get("/api/audit.csv")
    def get_audit_csv(request: Request, user: str = "", category: str = "", since: str = "", until: str = ""):
        require_user(request)
        import csv as _csv
        import io
        buf = io.StringIO()
        w = _csv.DictWriter(buf, fieldnames=ACTIONS_HEADER + ["categoria"], lineterminator="\n")
        w.writeheader()
        for r in _audit_rows(user, category, since, until, 100000):
            w.writerow({k: r.get(k, "") for k in ACTIONS_HEADER + ["categoria"]})
        name = f"auditoria_panel_rce_{datetime.now():%Y%m%d-%H%M}.csv"
        return Response(buf.getvalue(), media_type="text/csv; charset=utf-8",
                        headers={"Content-Disposition": f'attachment; filename="{name}"'})

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
                "real_password_set": ctx.config.real_password_set(),
                "exit_states": EXIT_STATES, "diary_kinds": DIARY_KINDS, "operations": OPERATIONS, "host": ctx.hostname}

    @app.post("/api/settings")
    def post_settings(body: Settings, request: Request):
        u = require_user(request)
        require_admin(u)
        require_lock(u)
        fields = {k: v for k, v in body.model_dump().items() if v is not None}
        if "exit_state" in fields and fields["exit_state"] not in EXIT_STATES:
            raise HTTPException(422, "Estado de salida desconocido")
        ctx.config.set(**fields)
        log_action(u, "settings:" + ",".join(f"{k}={v}" for k, v in fields.items()))
        return {"ok": True, **ctx.config.all()}

    @app.post("/api/real-password")
    def post_real_password(body: RealPasswordRequest, request: Request):
        u = require_user(request)
        require_admin(u)
        require_lock(u)
        if request.client.host not in ctx.local_hosts:
            raise HTTPException(403, "La contraseña del modo REAL solo se define desde el propio PC del laboratorio")
        if len(body.password) < 8:
            raise HTTPException(422, "La contraseña del modo REAL tiene al menos 8 caracteres")
        if ctx.config.real_password_set() and not ctx.config.verify_real_password(body.current):
            raise HTTPException(403, "La contraseña actual no es correcta")
        changed = ctx.config.real_password_set()
        ctx.config.set_real_password(body.password)
        ctx.diary.add(author=u["name"], kind="modo", host=u.get("host", ""),
                      text="Contraseña del modo REAL " + ("cambiada" if changed else "definida") + " desde el PC del laboratorio")
        log_action(u, "real-password:" + ("change" if changed else "set"))
        return {"ok": True, "real_password_set": True}

    @app.post("/api/people")
    def post_people(body: Credentials, request: Request):
        u = require_user(request)
        require_admin(u)
        require_lock(u)
        check_pin(body.pin)
        name = user_id(body.name)
        # a PIN set by the admin for someone else must be changed by that person (not the shared viewer account)
        ctx.people.add(name, body.pin, must_change=name != u["name"] and role_of(name) != "viewer")
        log_action(u, f"people:add:{name}")
        return {"ok": True, "people": ctx.people.names()}

    @app.delete("/api/people/{name}")
    def delete_people(name: str, request: Request):
        u = require_user(request)
        require_admin(u)
        require_lock(u)
        name = user_id(name)
        if name == u["name"]:
            raise HTTPException(409, "No puedes borrarte a ti mismo")
        ctx.people.remove(name)
        log_action(u, f"people:remove:{name}")
        return {"ok": True, "people": ctx.people.names()}

    @app.post("/api/mode")
    def post_mode(body: ModeRequest, request: Request):
        u = require_user(request)
        require_admin(u)
        require_lock(u)
        target = body.mode.strip().lower()
        if target not in ("real", "shadow"):
            raise HTTPException(422, "Modo desconocido")
        if target == ctx.mode:
            raise HTTPException(409, f"Ya está en modo {'REAL' if target == 'real' else 'SOMBRA'}")
        if ctx.jobs.running():
            raise HTTPException(409, "Hay un mando en ejecución: espera a que termine")
        host = u.get("host", "")
        if target == "real":
            if body.confirm.strip() != "REAL":
                raise HTTPException(422, "Escribe REAL para confirmar la decisión")
            if not ctx.config.real_password_set():
                raise HTTPException(403, "No hay contraseña del modo REAL: se define en Ajustes desde el propio PC del laboratorio")
            now_m = time.monotonic()
            ctx.real_failures[:] = [t for t in ctx.real_failures if now_m - t < REAL_LOCK_S]
            if len(ctx.real_failures) >= REAL_MAX_FAILURES:
                raise HTTPException(429, f"Demasiados intentos fallidos: el modo REAL queda bloqueado {REAL_LOCK_S // 60} min")
            if not ctx.config.verify_real_password(body.password):
                ctx.real_failures.append(now_m)
                log_action(u, "mode:real:contraseña-incorrecta", "error")
                raise HTTPException(403, "Contraseña del modo REAL incorrecta")
            p = ctx.probe()
            if not p.get("ok"):
                raise HTTPException(503, f"El PLC {ctx.plc['ip']} no responde: {p.get('error', '')}")
            others = ctx.port_users()
            if others:
                raise HTTPException(409, f"Otro programa de este PC está conectado al PLC (PID {', '.join(map(str, others))}): ciérralo antes")
            ctx.set_plc_mode("real")
            ctx.supervisor.restart_one("sc")
            ctx.diary.add(author=u["name"], kind="modo", host=host,
                          text=f"Modo REAL activado desde {host}: el control escribe en el PLC {ctx.plc['ip']} (estado {p.get('state')}, tapa {p.get('cover')})")
            log_action(u, "mode:real")
            return {"ok": True, "mode": "real", "plc": p}
        exit_state = ctx.config.get("exit_state")
        ctx.supervisor.stop_one("sc")
        r = ctx.apply_exit_state(exit_state)
        ctx.set_plc_mode("shadow")
        ctx.supervisor.start_one("sc")
        ctx.diary.add(author=u["name"], kind="modo", host=host,
                      text=f"Modo SOMBRA activado; PLC dejado en «{EXIT_STATES.get(exit_state, exit_state)}»: "
                           f"{'estado ' + str(r.get('state_after')) if r.get('ok') else 'ERROR ' + str(r.get('error'))}")
        log_action(u, "mode:shadow", result="ok" if r.get("ok") else "exit_state_error")
        return {"ok": bool(r.get("ok")), "mode": "shadow", "exit": r}

    @app.post("/api/command")
    def post_command(body: CommandRequest, request: Request):
        u = require_user(request)
        require_admin(u)
        require_lock(u)
        if ctx.mode != "real":
            raise HTTPException(409, "En modo SOMBRA los mandos se ejecutan en seco (/api/dryrun)")
        if body.command not in dryrun.COMMANDS:
            raise HTTPException(422, "Mando desconocido")
        if not body.confirm:
            raise HTTPException(422, "Confirmación requerida")
        if not state_io.read_control(ctx.state_dir).get("paused"):
            raise HTTPException(409, "Pon el control automático en pausa antes de usar los mandos")
        if ctx.jobs.running():
            raise HTTPException(409, "Ya hay un mando en ejecución")
        try:
            job = ctx.jobs.start(body.command, plc_factory=ctx.plc_factory)
        except plc_ops.Busy as e:
            raise HTTPException(409, str(e))
        ctx.diary.add(author=u["name"], kind="mando", host=u.get("host", ""), text=f"{job.label} (REAL) iniciado en el PLC {ctx.plc['ip']}")
        log_action(u, f"command:{body.command}")
        return job.as_dict()

    @app.get("/api/job")
    def get_job(request: Request):
        require_user(request)
        return ctx.jobs.status() or {"status": "none"}

    @app.post("/api/job/abort")
    def post_job_abort(request: Request):
        u = require_user(request)
        require_writer(u)
        require_lock(u)
        ok = ctx.jobs.abort()
        log_action(u, "job:abort", result="ok" if ok else "nothing")
        return {"ok": ok}

    @app.get("/api/plc")
    def get_plc(request: Request):
        require_user(request)
        if ctx.mode != "real" and not ctx.config.get("plc_read_allowed"):
            raise HTTPException(403, "La lectura del PLC en modo SOMBRA está desactivada (Ajustes)")
        ctx._probe_cache = (None, None)
        return ctx.probe_cached(0)

    return app
