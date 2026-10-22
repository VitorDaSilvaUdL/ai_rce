"""Schedule of the panel (state/agenda.json): hold an RCE state from a start to an end, or run a single command at a
time, to simulate a manual campaign.

- In SHADOW mode every entry is applied to the virtual state of the control (the PLC is never touched).
- In REAL mode only the entries created by rce_admin are executed on the PLC; the others wait and are lost if their
  time passes. A state entry executed in REAL mode can open and close a «manual» trial in the diary.
- While a state entry runs, the automatic control keeps deciding and logging but does not apply its decisions (pause).
  At the end: back to the automatic control (default), parada, or the state that there was before.
"""
import json
import threading
from datetime import datetime, timedelta
from pathlib import Path

from panel.auth import role_of
from panel.dryrun import COMMANDS

STATES = {"ColdMode1": "modo frío", "HeatMode1": "modo calor", "Parada": "parada", "AutomaticMode": "automático del PLC"}
AT_END = {"control": "Vuelve el control automático (IA)", "parada": "Parada", "anterior": "El estado de antes"}
STATUS = {"pendiente": "Pendiente", "en_curso": "En curso", "hecha": "Hecha", "cancelada": "Cancelada",
          "fallida": "Fallida", "perdida": "No ejecutada (pasó la hora)"}
FMT = "%Y-%m-%d %H:%M:%S"
MAX_SPAN = timedelta(days=14)


def _ts(dt):
    return dt.strftime(FMT) if dt else None


def _dt(s):
    return datetime.strptime(s, FMT) if s else None


class Agenda:
    """Entries persisted as JSON; safe to use from the API and the scheduler thread."""

    def __init__(self, path):
        self.path = Path(path)
        self._lock = threading.RLock()

    def _load(self):
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {"next_id": 1, "entries": []}

    def _save(self, data):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(self.path)

    def entries(self):
        with self._lock:
            return self._load()["entries"]

    def get(self, entry_id):
        return next((e for e in self.entries() if e["id"] == entry_id), None)

    def update(self, entry_id, **fields):
        with self._lock:
            data = self._load()
            for e in data["entries"]:
                if e["id"] == entry_id:
                    e.update(fields)
            self._save(data)

    def remove(self, entry_id):
        """Only to undo entries just created (a repeated entry that fails half way)."""
        with self._lock:
            data = self._load()
            data["entries"] = [e for e in data["entries"] if e["id"] != entry_id]
            self._save(data)

    def _new(self, entry):
        with self._lock:
            data = self._load()
            entry["id"] = data["next_id"]
            data["next_id"] += 1
            data["entries"].append(entry)
            self._save(data)
            return entry

    def add_state(self, author, state, start, end, at_end="control", trial=True, now=None):
        now = (now or datetime.now()).replace(microsecond=0)
        if state not in STATES:
            raise ValueError("Estado desconocido")
        if at_end not in AT_END:
            raise ValueError("Opción de final desconocida")
        start = max(start or now, now)
        if not end or end <= start:
            raise ValueError("La hora de fin tiene que ser posterior al inicio (y al momento actual)")
        if end - start > MAX_SPAN:
            raise ValueError("Una entrada no puede durar más de 14 días")
        for e in self.entries():
            if e["kind"] == "estado" and e["status"] in ("pendiente", "en_curso") and \
                    start < _dt(e["end"]) and _dt(e["start"]) < end:
                raise ValueError(f"Se solapa con la entrada {e['id']} ({e['label']}, {e['start'][:16]} – {e['end'][:16]})")
        return self._new({"kind": "estado", "target": state, "label": STATES[state], "start": _ts(start), "end": _ts(end),
                          "at_end": at_end, "trial": bool(trial), "author": author, "created": _ts(now),
                          "status": "pendiente", "waiting": False, "message": ""})

    def add_command(self, author, command, at, now=None):
        now = (now or datetime.now()).replace(microsecond=0)
        if command not in COMMANDS:
            raise ValueError("Mando desconocido")
        at = at or now
        if at < now - timedelta(minutes=1):
            raise ValueError("La hora del mando ya ha pasado")
        return self._new({"kind": "mando", "target": command, "label": COMMANDS[command][0], "start": _ts(max(at, now)),
                          "end": None, "at_end": None, "trial": False, "author": author, "created": _ts(now),
                          "status": "pendiente", "waiting": False, "message": ""})


class Scheduler:
    """tick(now) every few seconds: starts, ends and runs the entries that are due.

    executors: {"shadow": ..., "real": ...}, each with current() -> (state, paused), set_state(state, paused),
    set_paused(paused) and command(cmd), returning (ok, message). audit(author, action, result) logs every execution;
    trial(kind, text, author) writes the diary (only for state entries executed in REAL mode with trial on)."""

    def __init__(self, agenda, executors, mode, audit, trial=None, late=timedelta(minutes=10)):
        self.agenda, self.executors, self.mode, self.audit, self.trial, self.late = agenda, executors, mode, audit, trial, late
        self._first = True
        self._lock = threading.Lock()
        self.last_error = ""

    def allowed(self, entry, mode):
        return mode == "shadow" or role_of(entry["author"]) == "admin"

    def tick(self, now=None):
        now = (now or datetime.now()).replace(microsecond=0)
        with self._lock:
            mode = self.mode()
            entries = self.agenda.entries()
            if self._first:
                self._first = False
                for e in entries:              # the virtual state of the control does not survive a restart
                    if e["kind"] == "estado" and e["status"] == "en_curso" and e.get("mode") == "shadow" and _dt(e["end"]) > now:
                        self.executors["shadow"].set_state(e["target"], True)
            states = [e for e in entries if e["kind"] == "estado"]
            starting = [e for e in states if e["status"] == "pendiente" and _dt(e["start"]) <= now < _dt(e["end"])]
            for e in states:
                if e["status"] == "en_curso" and _dt(e["end"]) <= now:
                    self._finish(e, now, skip_action=bool(starting))
                elif e["status"] == "pendiente" and _dt(e["end"]) <= now:
                    self.agenda.update(e["id"], status="perdida", message="Pasó la hora sin ejecutarse")
                    self.audit(e["author"], f"agenda:perdida:#{e['id']} {e['label']}", "error: no se ejecutó a tiempo")
            for e in starting:
                self._start(e, now, mode)
            for e in entries:
                if e["kind"] == "mando" and e["status"] == "pendiente" and _dt(e["start"]) <= now:
                    if now - _dt(e["start"]) > self.late:
                        self.agenda.update(e["id"], status="perdida", message="Pasó la hora sin ejecutarse")
                        self.audit(e["author"], f"agenda:perdida:#{e['id']} {e['label']}", "error: no se ejecutó a tiempo")
                    elif not self.allowed(e, mode):
                        self.agenda.update(e["id"], waiting=True, message="En modo REAL solo se ejecutan las entradas de rce_admin")
                    else:
                        ok, msg = self.executors[mode].command(e["target"])
                        self.agenda.update(e["id"], status="hecha" if ok else "fallida", message=msg, started_at=_ts(now), mode=mode)
                        self.audit(e["author"], f"agenda:mando:#{e['id']} {e['label']}", "ok" if ok else f"error: {msg}")

    def _start(self, e, now, mode):
        if not self.allowed(e, mode):
            if not e.get("waiting"):
                self.agenda.update(e["id"], waiting=True, message="En modo REAL solo se ejecutan las entradas de rce_admin")
            return
        ex = self.executors[mode]
        prev_state, prev_paused = ex.current()
        ok, msg = ex.set_state(e["target"], True)
        if not ok:
            self.agenda.update(e["id"], status="fallida", message=msg, mode=mode)
            self.audit(e["author"], f"agenda:inicio:#{e['id']} {e['label']}", f"error: {msg}")
            return
        trial_opened = False
        if mode == "real" and e.get("trial") and self.trial:
            self.trial("ensayo_inicio", f"Agenda #{e['id']}: {e['label']} de {e['start'][:16]} a {e['end'][:16]}", e["author"])
            trial_opened = True
        self.agenda.update(e["id"], status="en_curso", started_at=_ts(now), prev_state=prev_state, prev_paused=prev_paused,
                           mode=mode, waiting=False, message="", trial_opened=trial_opened)
        self.audit(e["author"], f"agenda:inicio:#{e['id']} {e['label']}", "ok")

    def _finish(self, e, now, skip_action=False, status="hecha"):
        ex = self.executors[e.get("mode") or "shadow"]
        ok, msg = True, ""
        if skip_action:
            msg = "Empieza otra entrada a la misma hora"
        elif e["at_end"] == "parada":
            ok, msg = ex.set_state("Parada", True)
        elif e["at_end"] == "anterior":
            ok, msg = ex.set_state(e.get("prev_state") or "Parada", bool(e.get("prev_paused")))
        else:
            ok, msg = ex.set_paused(False)
        if e.get("trial_opened") and self.trial:
            self.trial("ensayo_fin", "", e["author"])
        self.agenda.update(e["id"], status=status if ok else "fallida", ended_at=_ts(now), message=msg)
        self.audit(e["author"], f"agenda:fin:#{e['id']} {e['label']}", "ok" if ok else f"error: {msg}")

    def cancel(self, entry_id, who, now=None):
        now = (now or datetime.now()).replace(microsecond=0)
        with self._lock:
            e = self.agenda.get(entry_id)
            if not e or e["status"] not in ("pendiente", "en_curso"):
                raise ValueError("Esa entrada ya no se puede cancelar")
            if e["status"] == "en_curso":
                self._finish(e, now, status="cancelada")
            else:
                self.agenda.update(entry_id, status="cancelada", ended_at=_ts(now))
            self.audit(who, f"agenda:cancelar:#{entry_id} {e['label']}", "ok")

    def run_in_thread(self, interval_s=15):
        stop = threading.Event()

        def loop():
            while not stop.wait(interval_s):
                try:
                    self.tick()
                    self.last_error = ""
                except Exception as exc:          # never die
                    self.last_error = str(exc)
        threading.Thread(target=loop, name="agenda", daemon=True).start()
        return stop


# ----------------------------------------------------------------------------------------------- executors
STATE_COMMAND = {"ColdMode1": "cold", "HeatMode1": "heat", "Parada": "parada", "AutomaticMode": "automatic"}


def _pause_fields(paused):
    return {"paused": bool(paused), "paused_by": "agenda" if paused else "",
            "since": datetime.now().replace(microsecond=0).isoformat(sep=" ") if paused else ""}


class ShadowExecutor:
    """SHADOW mode: the entries change the virtual state of the control through state/control.json (applied by the
    control within seconds). The PLC is never touched."""

    def __init__(self, state_dir, next_seq):
        self.state_dir, self.next_seq = Path(state_dir), next_seq

    def current(self):
        from sc import state_io
        status = state_io.read_status(self.state_dir) or {}
        return status.get("plc_state") or "Parada", bool(state_io.read_control(self.state_dir).get("paused"))

    def set_state(self, state, paused):
        from sc import state_io
        state_io.write_control(self.state_dir, set_state=state, set_state_seq=self.next_seq(), **_pause_fields(paused))
        return True, f"Estado simulado: {STATES.get(state, state)}"

    def set_paused(self, paused):
        from sc import state_io
        state_io.write_control(self.state_dir, **_pause_fields(paused))
        return True, "Control automático " + ("en pausa" if paused else "activo")

    def command(self, cmd):
        from sc import state_io
        from panel import dryrun
        state, _ = self.current()
        r = dryrun.run(cmd, state=state)
        after = r.get("state_after")
        if after and after != state:
            state_io.write_control(self.state_dir, set_state=after, set_state_seq=self.next_seq())
        return True, f"Simulado: {STATES.get(state, state)} → {STATES.get(after, after or state)}"


class RealExecutor:
    """REAL mode (entries of rce_admin): the same PLC jobs as the real commands of the panel, waiting for each to end.
    A state entry puts the automatic control in pause first, so that it does not undo the scheduled state."""

    def __init__(self, state_dir, jobs, plc_factory, probe, sleep=None, wait_free_s=120, wait_job_s=240):
        import time as _time
        self.state_dir, self.jobs, self.plc_factory, self.probe = Path(state_dir), jobs, plc_factory, probe
        self.sleep = sleep or _time.sleep
        self.wait_free_s, self.wait_job_s = wait_free_s, wait_job_s

    def current(self):
        from sc import state_io
        p = self.probe() or {}
        return (p.get("state") if p.get("ok") else None) or "Parada", bool(state_io.read_control(self.state_dir).get("paused"))

    def _run(self, cmd):
        waited = 0
        while self.jobs.running():
            if waited >= self.wait_free_s:
                return False, "Hay otro mando en ejecución en el PLC"
            self.sleep(1)
            waited += 1
        try:
            job = self.jobs.start(cmd, plc_factory=self.plc_factory)
        except Exception as e:                      # Busy or connection problems
            return False, str(e)
        waited = 0
        while job.status == "running" and waited < self.wait_job_s:
            self.sleep(1)
            waited += 1
        if job.status == "done":
            return True, f"{COMMANDS[cmd][0]}: hecho en el PLC"
        return False, job.error or ("El mando no terminó a tiempo" if job.status == "running" else f"Mando {job.status}")

    def set_state(self, state, paused):
        from sc import state_io
        state_io.write_control(self.state_dir, **_pause_fields(True))
        ok, msg = self._run(STATE_COMMAND[state])
        if ok and not paused:
            state_io.write_control(self.state_dir, **_pause_fields(False))
        return ok, msg

    def set_paused(self, paused):
        from sc import state_io
        state_io.write_control(self.state_dir, **_pause_fields(paused))
        return True, "Control automático " + ("en pausa" if paused else "activo")

    def command(self, cmd):
        return self._run(cmd)
