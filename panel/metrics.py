"""K1: thermal metrics every 5 min computed by the panel from the WinCC readings, appended to state/metrics.csv
(shipped by Filebeat to the Kibana index rce-metrics).

Thermal power delivered to the fluid: P = rho * cp * Q * (T_out - T_in) with Q = flow_rce_l_min / 60000 m3/s,
T_in = t_circuit_2_c (entrada al RCE), T_out = t_circuit_4_c (salida). P < 0 when the RCE cools the fluid (radiative
cooling), P > 0 when it heats it. rho and cp are the project constants for the water-glycol 30 % mixture
(energy draft workbook: Summary!B2, B3). Energy of a 5-min interval: E = P * 300 s.
Nights are identified by night_id (date of the evening) and night_hour (hours since 21:00), so that several nights
can be overlaid on one chart in Kibana.
"""
import csv
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

RHO = 1050.45        # kg/m3, water-glycol 30 %
CP = 3601.5          # J/(kg K)
INTERVAL = timedelta(minutes=5)
NIGHT_START_HOUR = 21

VARS = {"flow_l_min": ("flow_rce_l_min", "RCE-a"), "t_in_c": ("t_circuit_2_c", "RCE-a"), "t_out_c": ("t_circuit_4_c", "RCE-a"),
        "t_cold_tank_c": ("t_cold_tank_c", "RCE-a"), "t_hot_tank_c": ("t_hot_tank_c", "RCE-a"),
        "t_ambient_c": ("t_ambient_c", "RCE-a"), "ir_net_w_m2": ("ir_net_w_m2", "RCE-a"),   # single pyrgeometer, on unit A
        "solar_global_w_m2": ("solar_global_w_m2", "RCE-a"), "wind_speed_m_s": ("wind_speed_m_s", "RCE-a"),
        "rh_ambient_pct": ("rh_ambient_pct", "RCE-a")}
CONTEXT = ["cover", "cover_measured", "plc_mode", "execution", "trial_id", "operation"]
HEADER = (["timestamp", "interval_s", "n_samples"] + list(VARS) +
          ["dt_c", "p_thermal_w", "p_cold_w", "p_heat_w", "e_kwh", "e_cold_kwh", "e_heat_kwh", "night_id", "night_hour"] + CONTEXT)


def night_of(ts):
    """(night_id, night_hour): the night that an instant belongs to (evening date) and hours since 21:00."""
    ref = ts if ts.hour >= 12 else ts - timedelta(days=1)
    night_id = ref.date().isoformat()
    start = datetime(ref.year, ref.month, ref.day, NIGHT_START_HOUR)
    return night_id, round((ts - start).total_seconds() / 3600, 3)


def interval_metrics(readings, start, end):
    """Averages of the interval [start, end) and the thermal power and energy derived from them."""
    sums = defaultdict(float)
    counts = defaultdict(int)
    n = 0
    for r in readings:
        if r.value is None or not (start <= r.ts < end):
            continue
        for name, key in VARS.items():
            if (r.variable, r.system) == key:
                sums[name] += r.value
                counts[name] += 1
                n = max(n, counts[name])
    avg = {name: (sums[name] / counts[name] if counts[name] else None) for name in VARS}
    secs = (end - start).total_seconds()
    q, t_in, t_out = avg["flow_l_min"], avg["t_in_c"], avg["t_out_c"]
    if q is None or t_in is None or t_out is None:
        p = dt = None
    else:
        dt = t_out - t_in
        p = RHO * CP * (max(q, 0.0) / 60000.0) * dt
    e = p * secs / 3.6e6 if p is not None else None
    night_id, night_hour = night_of(start)
    out = {"timestamp": start.strftime("%Y-%m-%d %H:%M:%S"), "interval_s": int(secs), "n_samples": n, **avg,
           "dt_c": dt, "p_thermal_w": p, "p_cold_w": (max(0.0, -p) if p is not None else None),
           "p_heat_w": (max(0.0, p) if p is not None else None), "e_kwh": e,
           "e_cold_kwh": (max(0.0, -e) if e is not None else None), "e_heat_kwh": (max(0.0, e) if e is not None else None),
           "night_id": night_id, "night_hour": night_hour}
    return out


def _fmt(v):
    if v is None:
        return ""
    if isinstance(v, float):
        return f"{v:.6g}"
    return str(v)


class MetricsLog:
    """state/metrics.csv: one row per completed 5-min interval. update() appends the intervals completed since
    the last row (or the last `backfill` on the first run)."""

    def __init__(self, path, backfill=timedelta(hours=6)):
        self.path = Path(path)
        self.backfill = backfill

    def rows(self):
        if not self.path.exists():
            return []
        with open(self.path, newline="", encoding="utf-8") as f:
            return list(csv.DictReader(f))

    def last_timestamp(self):
        rows = self.rows()
        if not rows:
            return None
        return datetime.strptime(rows[-1]["timestamp"], "%Y-%m-%d %H:%M:%S")

    def update(self, readings, now, context=None):
        """Append completed intervals up to `now`. Returns the number of rows written."""
        context = context or {}
        last = self.last_timestamp()
        floor = now.replace(minute=now.minute - now.minute % 5, second=0, microsecond=0)
        if last is None:
            start = floor - self.backfill
            first = min((r.ts for r in readings), default=None)
            if first is not None and first > start:
                start = first                                  # no empty rows before the first reading
            start = start.replace(minute=start.minute - start.minute % 5, second=0, microsecond=0)
        else:
            start = last + INTERVAL
        written = 0
        self.path.parent.mkdir(parents=True, exist_ok=True)
        new = not self.path.exists() or self.path.stat().st_size == 0
        with open(self.path, "a", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=HEADER)
            if new:
                w.writeheader()
            t = start
            while t + INTERVAL <= floor:
                m = interval_metrics(readings, t, t + INTERVAL)
                for k in CONTEXT:
                    m[k] = context.get(k, "") or ""
                w.writerow({k: _fmt(m.get(k)) for k in HEADER})
                written += 1
                t += INTERVAL
        return written
