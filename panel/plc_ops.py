"""Real PLC operations run by the panel in REAL mode (phase 3): manual commands as background jobs with
step-by-step progress, live cover position and abort; probe of the PLC; application of the exit state when
leaving REAL mode.

ProgressPLC is the real PLCController (same write sequences) with recorded steps, abortable waits and no
background alarm thread (the job checks the alarms itself before opening). Only one job runs at a time; the
control loop must be paused (state/control.json) while a job runs, which the web layer enforces.
"""
import os
import re
import subprocess
import threading
import time
from collections import deque
from datetime import datetime

from sc.plc_controller import PLCController
from sc.cycle_log import state_outputs
from panel.dryrun import ACTUATORS, BIT_MEANING, COMMANDS

# DB80 (heat start/end) and DB83 (parada end) offsets per weekday, as decision_none_or_parada() reads them
SCHEDULE_OFFSETS = {"Monday": (62, 66, 62), "Tuesday": (70, 74, 70), "Wednesday": (78, 82, 78),
                    "Thursday": (86, 90, 86), "Friday": (94, 98, 94)}


class Aborted(Exception):
    pass


class Busy(Exception):
    pass


def _now():
    return datetime.now().isoformat(sep=" ", timespec="seconds")


class ProgressPLC(PLCController):
    execution = "real"

    def __init__(self, ip, rack=0, slot=1):
        super().__init__(ip=ip, rack=rack, slot=slot, name="PLC-panel")
        self.steps = []
        self.cover_timeline = []
        self.abort_event = threading.Event()
        self.aborted = False

    def start_alarm_monitor(self, poll_s=1.0):
        pass                      # no background closing sequence while a manual job runs

    def _check_abort(self):
        if self.abort_event.is_set():
            raise Aborted()

    def _record_cover(self, pos=None):
        try:
            pos = pos or self.read_cover_position()
        except Exception:
            return
        if not self.cover_timeline or self.cover_timeline[-1][1] != pos:
            self.cover_timeline.append((_now(), pos))

    def _write_bool_db(self, db_number, byte_index, bit_index, value):
        self._check_abort()
        super()._write_bool_db(db_number, byte_index, bit_index, value)
        meaning = BIT_MEANING.get((byte_index, bit_index), f"bit {byte_index}.{bit_index}")
        self.steps.append({"kind": "write", "ts": _now(), "db": db_number, "byte": byte_index, "bit": bit_index,
                           "value": bool(value),
                           "text": f"{ACTUATORS.get(db_number, f'DB{db_number}')} → {meaning}: {'ON' if value else 'OFF'}"})

    def any_alarm_active(self):
        active = super().any_alarm_active()
        if active:
            self.steps.append({"kind": "blocked", "ts": _now(),
                               "text": "Apertura bloqueada: alarma de lluvia o viento activa en el PLC"})
            self.aborted = True
        return active

    def sleep_with_progress(self, seconds, desc="Esperando"):
        step = {"kind": "wait", "ts": _now(), "seconds": int(seconds), "progress": 0,
                "text": f"Esperar {int(seconds)} s — {desc}"}
        self.steps.append(step)
        for i in range(int(seconds)):
            self._check_abort()
            time.sleep(1)
            step["progress"] = i + 1
            if i % 5 == 4:
                self._record_cover()

    def wait_for_th_position(self, expected, timeout_s=60.0, poll_s=0.5):
        target = "open" if expected == "open" else "closed"
        word = "abierta" if target == "open" else "cerrada"
        step = {"kind": "wait", "ts": _now(), "text": f"Tapa horizontal: esperar a que esté {word} (máx {int(timeout_s)} s)",
                "ok": None, "seconds": None}
        self.steps.append(step)
        t0 = time.monotonic()
        while True:
            self._check_abort()
            pos = self.read_cover_position()
            self._record_cover(pos)
            if pos == target:
                step["ok"] = True
                step["seconds"] = round(time.monotonic() - t0, 1)
                return True
            if time.monotonic() - t0 >= timeout_s:
                break
            time.sleep(poll_s)
        step["ok"] = False
        self.aborted = True
        self.steps.append({"kind": "abort", "ts": _now(),
                           "text": f"Secuencia abortada: la tapa no ha llegado a {word} en {int(timeout_s)} s"})
        return False


# ----------------------------------------------------------------------------------------------- jobs
class Job:
    def __init__(self, command):
        self.id = datetime.now().strftime("%Y%m%d-%H%M%S")
        self.command = command
        self.label = COMMANDS[command][0]
        self.status = "running"
        self.error = None
        self.steps = []
        self.cover_timeline = []
        self.timings = {}
        self.state_before = self.state_after = None
        self.cover_before = self.cover_after = None
        self.started = _now()
        self.finished = None
        self.elapsed_s = None
        self.plc = None
        self.thread = None
        self._t0 = time.monotonic()

    def as_dict(self):
        return {"id": self.id, "command": self.command, "label": self.label, "status": self.status, "error": self.error,
                "steps": list(self.steps), "cover_timeline": list(self.cover_timeline), "timings": dict(self.timings),
                "state_before": self.state_before, "state_after": self.state_after,
                "cover_before": self.cover_before, "cover_after": self.cover_after,
                "started": self.started, "finished": self.finished,
                "elapsed_s": self.elapsed_s if self.elapsed_s is not None else round(time.monotonic() - self._t0, 1)}


class JobRunner:
    def __init__(self):
        self._job = None
        self._lock = threading.Lock()
        self.history = deque(maxlen=20)

    def current(self):
        return self._job

    def running(self):
        return self._job is not None and self._job.status == "running"

    def start(self, command, plc_factory, prepare=None):
        if command not in COMMANDS:
            raise ValueError(f"mando desconocido: {command}")
        with self._lock:
            if self.running():
                raise Busy("Ya hay un mando en ejecución")
            job = Job(command)
            self._job = job
        job.thread = threading.Thread(target=self._run, args=(job, plc_factory, prepare), name=f"job-{command}", daemon=True)
        job.thread.start()
        return job

    def _run(self, job, plc_factory, prepare):
        plc = None
        try:
            plc = plc_factory()
            job.plc = plc
            job.steps = plc.steps
            job.cover_timeline = plc.cover_timeline
            plc.connect()
            if prepare:
                prepare(plc.client)
            job.state_before = plc.get_system_state()[0]
            job.cover_before = plc.read_cover_position()
            plc._record_cover(job.cover_before)
            if job.command == "test_cover":
                t0 = time.monotonic()
                plc._sequence_obrir()
                if not plc.aborted:
                    job.timings["open_s"] = round(time.monotonic() - t0, 1)
                    plc.steps.append({"kind": "note", "ts": _now(), "text": f"Tapa abierta en {job.timings['open_s']} s: ahora se cierra"})
                    t1 = time.monotonic()
                    plc._sequence_tancar()
                    if not plc.aborted:
                        job.timings["close_s"] = round(time.monotonic() - t1, 1)
            else:
                getattr(plc, COMMANDS[job.command][1])()
            job.state_after = plc.get_system_state()[0]
            job.cover_after = plc.read_cover_position()
            job.status = "done"
        except Aborted:
            job.status = "aborted"
            job.steps.append({"kind": "abort", "ts": _now(), "text": "Abortado desde el panel"})
            try:
                job.cover_after = plc.read_cover_position()
            except Exception:
                pass
        except Exception as e:
            job.status = "error"
            job.error = str(e)
            job.steps.append({"kind": "error", "ts": _now(), "text": f"Error: {e}"})
        finally:
            try:
                if plc is not None:
                    plc.disconnect()
            except Exception:
                pass
            job.finished = _now()
            job.elapsed_s = round(time.monotonic() - job._t0, 1)
            self.history.append(job.as_dict())

    def abort(self):
        job = self._job
        if job and job.status == "running" and job.plc is not None:
            job.plc.abort_event.set()
            return True
        return False

    def status(self):
        return self._job.as_dict() if self._job else None


# ----------------------------------------------------------------------------------------------- probe, exit state
def probe(ip, rack=0, slot=1):
    """Connect, read the logical state, cover, alarms and today's schedule, disconnect. Never raises."""
    plc = ProgressPLC(ip, rack, slot)
    try:
        plc.connect()
        state, _ = plc.get_system_state()
        cover = plc.read_cover_position()
        alarms = plc.read_alarms()
        day = datetime.now().strftime("%A")
        schedule = None
        if day in SCHEDULE_OFFSETS:
            a, b, c = SCHEDULE_OFFSETS[day]
            schedule = {"heat_start": plc.read_hour(80, a), "heat_end": plc.read_hour(80, b), "parada_end": plc.read_hour(83, c)}
        out = state_outputs(state, alarm=any(alarms.values()))
        return {"ok": True, "ts": _now(), "ip": ip, "state": state, "plc_mode": out["plc_mode"], "cover": cover,
                "alarms": alarms, "schedule_today": schedule,
                "pumps": {"b1": out["pump_b1"], "b2": out["pump_b2"], "ev1": out["ev1"], "ev2": out["ev2"]}}
    except Exception as e:
        return {"ok": False, "ts": _now(), "ip": ip, "error": str(e)}
    finally:
        try:
            plc.disconnect()
        except Exception:
            pass


def apply_exit_state(ip, rack, slot, exit_state, prepare=None):
    """Leave the PLC in a known state before the control stops writing to it (automatic | parada | as_is)."""
    plc = ProgressPLC(ip, rack, slot)
    try:
        plc.connect()
        if prepare:
            prepare(plc.client)
        before = plc.get_system_state()[0]
        if exit_state == "automatic":
            plc.set_automatic_mode()
        elif exit_state == "parada":
            plc.set_parada_mode()
        after = plc.get_system_state()[0]
        return {"ok": True, "exit_state": exit_state, "state_before": before, "state_after": after, "steps": plc.steps,
                "cover": plc.read_cover_position()}
    except Exception as e:
        return {"ok": False, "exit_state": exit_state, "error": str(e), "steps": plc.steps}
    finally:
        try:
            plc.disconnect()
        except Exception:
            pass


def port_users(port=102):
    """PIDs other than this process with a TCP connection to the PLC port (Windows netstat). [] on error."""
    try:
        out = subprocess.run(["netstat", "-ano"], capture_output=True, text=True, timeout=15).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    pids = set()
    for line in out.splitlines():
        if re.search(rf":{port}\s", line) and "ESTABLISHED" in line.upper():
            pid = line.split()[-1]
            if pid.isdigit() and int(pid) != os.getpid():
                pids.add(int(pid))
    return sorted(pids)
