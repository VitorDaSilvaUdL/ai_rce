"""K2: summary of each campaign trial (ensayo) for the comparison «manual frente a automático» in Kibana.

A trial is opened and closed in the diary (ensayo_inicio / ensayo_fin, with its operation: manual, automatico_plc,
control_ia). TrialsLog writes state/ensayos.csv: a row with estado=en_curso every hour while the trial is open and
one final row with estado=cerrado; Kibana keeps the last row per ensayo_id. Energy comes from the 5-min metrics,
cover hours from the control cycles (cover_measured in REAL, cover otherwise).
"""
import csv
from datetime import datetime, timedelta
from pathlib import Path

HEADER = ["timestamp", "ensayo_id", "estado", "operacion", "inicio", "fin", "duracion_h", "texto",
          "e_cold_kwh", "e_heat_kwh", "t9_inicio_c", "t9_fin_c", "t9_min_c", "delta_t9_c",
          "t_ambient_media_c", "ir_medio_w_m2", "viento_medio_m_s", "humedad_media_pct", "horas_tapa_abierta", "n_ciclos",
          "p_cold_media_w"]
TS = "%Y-%m-%d %H:%M:%S"


def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def summarize(trial, end, metrics_rows, decisions, closed=False):
    """trial: dict from Diary.open_trial_info() (ensayo_id, operacion, inicio, texto)."""
    start = datetime.strptime(trial["inicio"], TS)
    rows = [r for r in metrics_rows if start <= datetime.strptime(r["timestamp"], TS) < end]
    e_cold = sum(_f(r["e_cold_kwh"]) or 0.0 for r in rows)
    e_heat = sum(_f(r["e_heat_kwh"]) or 0.0 for r in rows)
    t9 = [(datetime.strptime(r["timestamp"], TS), _f(r["t_cold_tank_c"])) for r in rows if _f(r["t_cold_tank_c"]) is not None]

    def mean(key):
        vals = [_f(r[key]) for r in rows if _f(r[key]) is not None]
        return round(sum(vals) / len(vals), 3) if vals else None
    cycles = [d for d in decisions if start <= d["ts"] < end]
    open_cycles = 0
    for d in cycles:
        measured = d.get("cover_measured") or ""
        is_open = measured == "open" if measured else d.get("cover_open") == 1
        open_cycles += 1 if is_open else 0
    return {
        "timestamp": end.strftime(TS), "ensayo_id": trial["ensayo_id"], "estado": "cerrado" if closed else "en_curso",
        "operacion": trial.get("operacion", ""), "inicio": trial["inicio"], "fin": end.strftime(TS) if closed else "",
        "duracion_h": round((end - start).total_seconds() / 3600, 3), "texto": trial.get("texto", ""),
        "e_cold_kwh": round(e_cold, 4), "e_heat_kwh": round(e_heat, 4),
        "t9_inicio_c": t9[0][1] if t9 else None, "t9_fin_c": t9[-1][1] if t9 else None,
        "t9_min_c": min(v for _, v in t9) if t9 else None,
        "delta_t9_c": round(t9[-1][1] - t9[0][1], 3) if t9 else None,
        "t_ambient_media_c": mean("t_ambient_c"), "ir_medio_w_m2": mean("ir_net_w_m2"),
        "viento_medio_m_s": mean("wind_speed_m_s"), "humedad_media_pct": mean("rh_ambient_pct"),
        "horas_tapa_abierta": round(open_cycles * 0.25, 3), "n_ciclos": len(cycles),
        "p_cold_media_w": mean("p_cold_w"),
    }


class TrialsLog:
    def __init__(self, path, refresh=timedelta(hours=1)):
        self.path = Path(path)
        self.refresh = refresh

    def rows(self):
        if not self.path.exists():
            return []
        with open(self.path, newline="", encoding="utf-8") as f:
            return list(csv.DictReader(f))

    def _append(self, row):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        new = not self.path.exists() or self.path.stat().st_size == 0
        with open(self.path, "a", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=HEADER)
            if new:
                w.writeheader()
            w.writerow({k: ("" if row.get(k) is None else row.get(k)) for k in HEADER})

    def update(self, diary, metrics_log, decisions, now=None):
        """Write the open trial (hourly) or close the last one. Returns rows written (0 or 1)."""
        now = now or datetime.now()
        rows = self.rows()
        last = {r["ensayo_id"]: r for r in rows}
        open_trial = diary.open_trial_info()
        if open_trial:
            prev = last.get(open_trial["ensayo_id"])
            if prev and datetime.strptime(prev["timestamp"], TS) + self.refresh > now:
                return 0
            self._append(summarize(open_trial, now, metrics_log.rows(), decisions))
            return 1
        closed = diary.last_closed_trial_info()
        if closed and (not last.get(closed["ensayo_id"]) or last[closed["ensayo_id"]]["estado"] != "cerrado"):
            end = datetime.strptime(closed["fin"], TS)
            self._append(summarize(closed, end, metrics_log.rows(), decisions, closed=True))
            return 1
        return 0
