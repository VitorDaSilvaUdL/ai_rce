"""Supervisor of the ai_rce processes: the prediction API (api.main) and the control loop (sc.main).

The panel runs the supervisor in a thread: it starts both processes when the system is wanted running, starts the
control only once the API answers, and restarts a process that exits. The wait before a restart is counted from the
tick that noticed the exit and doubles after each crash in a row (restart_delay_s, 2x, 4x... up to
max_restart_delay_s); a run of at least stable_run_s starts the count again. Logs of each process go to a file.
Nothing here touches the PLC.
"""
import os
import subprocess
import threading
from collections import deque
from datetime import datetime, timedelta
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
    def __init__(self, api, sc, api_ready, restart_delay_s=60, max_restart_delay_s=900, stable_run_s=600):
        self.api = api
        self.sc = sc
        self.api_ready = api_ready
        self.restart_delay = restart_delay_s
        self.max_restart_delay = max_restart_delay_s
        self.stable_run = stable_run_s
        self.wanted = False
        self.wanted_since = None
        self._next_at = {}             # process name -> time of its next automatic restart (set by the tick that saw it down)
        self._failures = {}            # process name -> crashes in a row without a stable run in between
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
            self._next_at, self._failures = {}, {}
            self._event("Arranque solicitado", now)
            self.api.start()

    def stop(self):
        with self._lock:
            self.wanted = False
            self._event("Parada solicitada")
            self.sc.stop()
            self.api.stop()
            self._next_at, self._failures = {}, {}

    def restart(self, now=None):
        self.stop()
        self.start(now)

    def restart_one(self, name):
        with self._lock:
            proc = {"api": self.api, "sc": self.sc}[name]
            proc.stop()
            self._clear_backoff(name)
            self._event(f"Reinicio de {name} solicitado")
            if self.wanted:
                proc.start()

    def stop_one(self, name):
        """Stop one process and keep it stopped until start_one() (the watchdog will not restart it)."""
        with self._lock:
            proc = {"api": self.api, "sc": self.sc}[name]
            proc.stop()
            self._held = getattr(self, "_held", set()) | {name}
            self._event(f"{name} detenido por el panel")

    def start_one(self, name):
        with self._lock:
            proc = {"api": self.api, "sc": self.sc}[name]
            self._held = getattr(self, "_held", set()) - {name}
            self._clear_backoff(name)
            if self.wanted and not proc.running:
                proc.start()
                self._event(f"{name} arrancado por el panel")

    # --- watchdog
    def _clear_backoff(self, name):
        self._next_at.pop(name, None)
        self._failures.pop(name, None)

    def _delay_after(self, proc):
        """Seconds to wait before restarting proc, which has just been seen down: doubles with each crash in a row."""
        ran_s = (proc.stopped_at - proc.started_at).total_seconds() if proc.stopped_at and proc.started_at else 0
        n = 1 if ran_s >= self.stable_run else self._failures.get(proc.name, 0) + 1
        self._failures[proc.name] = n
        return min(self.restart_delay * 2 ** (n - 1), self.max_restart_delay)

    def tick(self, now=None):
        now = now or datetime.now()
        with self._lock:
            if not self.wanted:
                return
            for proc, ready in ((self.api, lambda: True), (self.sc, self.api_ready)):
                if proc.running or proc.name in getattr(self, "_held", set()):
                    self._next_at.pop(proc.name, None)
                    continue
                if proc.started_at is None:                      # never started yet
                    if ready():
                        proc.start()
                        self._event(f"{proc.name} arrancado", now)
                    continue
                if proc.name not in self._next_at:              # first tick that sees it down
                    delay = self._delay_after(proc)
                    self._next_at[proc.name] = now + timedelta(seconds=delay)
                    self._event(f"{proc.name} se ha parado (código {proc.last_exit_code}); reintento en {delay} s", now)
                if now >= self._next_at[proc.name] and ready():
                    proc.restarts += 1
                    proc.start()
                    self._next_at.pop(proc.name, None)
                    self._event(f"{proc.name} reiniciado (intento {self._failures.get(proc.name, 1)} seguido)", now)

    def status(self, now=None):
        now = now or datetime.now()
        out = {"wanted": self.wanted,
               "wanted_since": self.wanted_since.isoformat(sep=" ", timespec="seconds") if self.wanted_since else None,
               "api": self.api.info(now), "sc": self.sc.info(now), "events": list(self.events)[-20:]}
        for name in ("api", "sc"):
            nxt = self._next_at.get(name)
            out[name]["consecutive_failures"] = self._failures.get(name, 0)
            out[name]["next_restart_in_s"] = max(0, int((nxt - now).total_seconds())) if nxt else None
        return out

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
