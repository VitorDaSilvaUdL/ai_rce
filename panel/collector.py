"""The panel's collector: every minute it writes the files that feed the Kibana dashboards.

- state/metrics.csv (K1): 5-min thermal metrics from the WinCC readings, with the control context (cover, mode,
  execution) and the open trial of the diary.
- state/ensayos.csv (K2): hourly summary of the open trial and a final row when it is closed.
- state/plc_probe.csv (K3): in REAL mode, the state read from the PLC (every change, or every 10 min).
Errors are recorded in last_error and never stop the panel.
"""
import csv
import threading
from datetime import datetime, timedelta
from pathlib import Path

from panel import metrics, readers, trials
from sc import state_io

PROBE_HEADER = ["timestamp", "ok", "state", "plc_mode", "cover", "pump_b1", "pump_b2", "ev1", "ev2", "alarm_lluvia",
                "alarm_viento", "error"]


class ProbeLog:
    def __init__(self, path, repeat_after=timedelta(minutes=10)):
        self.path = Path(path)
        self.repeat_after = repeat_after

    def rows(self):
        if not self.path.exists():
            return []
        with open(self.path, newline="", encoding="utf-8") as f:
            return list(csv.DictReader(f))

    @staticmethod
    def row_of(p, now):
        pumps = p.get("pumps") or {}
        alarms = p.get("alarms") or {}
        return {"timestamp": now.strftime("%Y-%m-%d %H:%M:%S"), "ok": 1 if p.get("ok") else 0,
                "state": p.get("state") or "", "plc_mode": p.get("plc_mode") or "", "cover": p.get("cover") or "",
                "pump_b1": pumps.get("b1", ""), "pump_b2": pumps.get("b2", ""), "ev1": pumps.get("ev1", ""), "ev2": pumps.get("ev2", ""),
                "alarm_lluvia": 1 if alarms.get("lluvia") else 0, "alarm_viento": 1 if alarms.get("viento") else 0,
                "error": (p.get("error") or "")[:200]}

    def update(self, probe_result, now):
        """Append when the PLC state changed or repeat_after has passed. Returns 1 or 0."""
        row = self.row_of(probe_result, now)
        rows = self.rows()
        if rows:
            last = rows[-1]
            same = all(str(last.get(k, "")) == str(row[k]) for k in PROBE_HEADER if k != "timestamp")
            if same and datetime.strptime(last["timestamp"], "%Y-%m-%d %H:%M:%S") + self.repeat_after > now:
                return 0
        self.path.parent.mkdir(parents=True, exist_ok=True)
        new = not self.path.exists() or self.path.stat().st_size == 0
        with open(self.path, "a", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=PROBE_HEADER)
            if new:
                w.writeheader()
            w.writerow(row)
        return 1


class Collector:
    def __init__(self, state_dir, readings, decisions, diary, mode, probe, interval_s=60):
        self.state_dir = Path(state_dir)
        self.readings = readings          # callable(hours) -> list[Reading]
        self.decisions = decisions        # callable(since) -> rows of dades.csv
        self.diary = diary
        self.mode = mode                  # callable() -> "shadow" | "real"
        self.probe = probe                # callable() -> probe dict
        self.interval_s = interval_s
        self.metrics_log = metrics.MetricsLog(self.state_dir / "metrics.csv")
        self.trials_log = trials.TrialsLog(self.state_dir / "ensayos.csv")
        self.probe_log = ProbeLog(self.state_dir / "plc_probe.csv")
        self.last_error = None
        self.last_run = None
        self._stop = threading.Event()

    def context(self):
        st = state_io.read_status(self.state_dir) or {}
        trial = self.diary.open_trial_info() or {}
        return {"cover": st.get("cover", ""), "cover_measured": st.get("cover_measured", ""), "plc_mode": st.get("plc_mode", ""),
                "execution": st.get("execution", ""), "trial_id": trial.get("ensayo_id", ""), "operation": trial.get("operacion", "")}

    def run_once(self, now=None):
        now = now or datetime.now()
        counts = {"metrics": 0, "trials": 0, "plc": 0}
        self.last_error = None
        try:
            rs = self.readings(hours=8)
            counts["metrics"] = self.metrics_log.update(rs, now=now, context=self.context())
        except Exception as e:
            self.last_error = f"metrics: {e}"
        try:
            decisions = self.decisions(now - timedelta(days=8))
            counts["trials"] = self.trials_log.update(self.diary, self.metrics_log, decisions, now=now)
        except Exception as e:
            self.last_error = f"trials: {e}"
        try:
            if self.mode() == "real":
                counts["plc"] = self.probe_log.update(self.probe(), now)
        except Exception as e:
            self.last_error = f"plc: {e}"
        self.last_run = now
        return counts

    def run_in_thread(self):
        def loop():
            while not self._stop.wait(self.interval_s):
                try:
                    self.run_once()
                except Exception as e:          # never die
                    self.last_error = str(e)
        threading.Thread(target=loop, name="collector", daemon=True).start()

    def stop(self):
        self._stop.set()
