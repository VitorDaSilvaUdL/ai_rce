"""Supervisor of the ai_rce processes: the prediction API (api.main) and the control loop (sc.main).

The panel runs the supervisor in a thread: it starts both processes when the system is wanted running, starts the
control only once the API answers, and restarts a process that exits (after restart_delay_s, counted from the tick
that noticed it). Logs of each process go to a file. Nothing here touches the PLC.
"""
import os
import subprocess
import threading
from collections import deque
from datetime import datetime
from pathlib import Path


class ManagedProcess:
    def __init__(self, name, cmd, cwd, log_path, env=None):
        self.name = name
        self.cmd = list(cmd)
        self.cwd = str(cwd)
        self.log_path = Path(log_path)
        self.env = env or {}
        self.proc = None
        self._log = None
        self.started_at = None
        self.stopped_at = None
        self.last_exit_code = None
        self.restarts = 0

    # --- state
    @property
    def running(self):
        if self.proc is None:
            return False
        rc = self.proc.poll()
        if rc is None:
            return True
        self._on_exit(rc)
        return False

    @property
    def pid(self):
        return self.proc.pid if self.running else None

    def _on_exit(self, rc):
        self.last_exit_code = rc
        self.stopped_at = datetime.now()
        self.proc = None
        if self._log:
            try:
                self._log.write(f"=== {self.stopped_at:%Y-%m-%d %H:%M:%S} exit code {rc}\n".encode("utf-8"))
                self._log.close()
            except OSError:
                pass
            self._log = None

    # --- control
    def start(self):
        if self.running:
            return False
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self._log = open(self.log_path, "ab")
        self._log.write(f"\n=== {datetime.now():%Y-%m-%d %H:%M:%S} start: {' '.join(self.cmd)}\n".encode("utf-8"))
        self._log.flush()
        env = {**os.environ, **self.env}
        flags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
        self.proc = subprocess.Popen(self.cmd, cwd=self.cwd, stdout=self._log, stderr=subprocess.STDOUT,
                                     stdin=subprocess.DEVNULL, env=env, creationflags=flags)
        self.started_at = datetime.now()
        self.last_exit_code = None
        return True

    def stop(self, timeout=15):
        if not self.running:
            return
        p = self.proc
        if os.name == "nt":
            subprocess.run(["taskkill", "/PID", str(p.pid), "/T", "/F"], capture_output=True)
        else:
            p.terminate()
        try:
            p.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            p.kill()
            p.wait(timeout=5)
        self._on_exit(p.returncode)

    def restart(self):
        self.stop()
        return self.start()

    def info(self, now=None):
        now = now or datetime.now()
        running = self.running
        return {
            "name": self.name, "running": running, "pid": self.pid,
            "started_at": self.started_at.isoformat(sep=" ", timespec="seconds") if self.started_at else None,
            "uptime_s": (now - self.started_at).total_seconds() if running and self.started_at else None,
            "last_exit_code": self.last_exit_code, "restarts": self.restarts, "log": str(self.log_path),
        }


class Supervisor:
    def __init__(self, api, sc, api_ready, restart_delay_s=60):
        self.api = api
        self.sc = sc
        self.api_ready = api_ready
        self.restart_delay = restart_delay_s
        self.wanted = False
        self.wanted_since = None
        self._down_since = {}          # process name -> first tick that saw it down
        self._lock = threading.RLock()
        self.events = deque(maxlen=300)
        self._thread = None
        self._stop_event = threading.Event()

    def _event(self, text, now=None):
        self.events.append({"ts": (now or datetime.now()).isoformat(sep=" ", timespec="seconds"), "text": text})

    # --- commands
    def start(self, now=None):
        with self._lock:
            self.wanted = True
            self.wanted_since = now or datetime.now()
            self._down_since = {}
            self._event("Arranque solicitado", now)
            self.api.start()

    def stop(self):
        with self._lock:
            self.wanted = False
            self._event("Parada solicitada")
            self.sc.stop()
            self.api.stop()
            self._down_since = {}

    def restart(self, now=None):
        self.stop()
        self.start(now)

    def restart_one(self, name):
        with self._lock:
            proc = {"api": self.api, "sc": self.sc}[name]
            proc.stop()
            self._event(f"Reinicio de {name} solicitado")
            if self.wanted:
                proc.start()

    # --- watchdog
    def tick(self, now=None):
        now = now or datetime.now()
        with self._lock:
            if not self.wanted:
                return
            for proc, ready in ((self.api, lambda: True), (self.sc, self.api_ready)):
                if proc.running:
                    self._down_since.pop(proc.name, None)
                    continue
                if proc.started_at is None:                      # never started yet
                    if ready():
                        proc.start()
                        self._event(f"{proc.name} arrancado", now)
                    continue
                first = self._down_since.setdefault(proc.name, now)
                if (now - first).total_seconds() >= self.restart_delay and ready():
                    proc.restarts += 1
                    proc.start()
                    self._down_since.pop(proc.name, None)
                    self._event(f"{proc.name} se había parado (código {proc.last_exit_code}): reiniciado", now)

    def status(self, now=None):
        now = now or datetime.now()
        return {"wanted": self.wanted,
                "wanted_since": self.wanted_since.isoformat(sep=" ", timespec="seconds") if self.wanted_since else None,
                "api": self.api.info(now), "sc": self.sc.info(now), "events": list(self.events)[-20:]}

    # --- background loop
    def run_in_thread(self, interval_s=5):
        def loop():
            while not self._stop_event.wait(interval_s):
                try:
                    self.tick()
                except Exception as e:                      # the watchdog must never die
                    self._event(f"Error en el supervisor: {e}")
        self._thread = threading.Thread(target=loop, name="supervisor", daemon=True)
        self._thread.start()

    def shutdown(self):
        self._stop_event.set()
        self.stop()
