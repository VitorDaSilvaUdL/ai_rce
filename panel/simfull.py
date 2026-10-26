"""Full simulator and replay of a day: the control's own pieces (forecast request, plan, decide(), state machine)
driven from the WinCC readings, with optional overrides, outside the control loop.

What it reproduces faithfully: the 24-sample input window (as sc/utils/read_data.py builds it), the plan of
plan_mode() (demand of the window, cheapest frames until the demand is covered), decide() with its reasons and the
PLC state machine. What it cannot: the prediction API fetches *today's* weather forecast (Open-Meteo, AEMET, NASA),
so a replayed past day uses the measured tank and sensor data of that day but the weather forecast of today.
"""
import math
from datetime import datetime, timedelta, date as date_type

from sc.decision import decide, next_state as decision_next_state
from sc.plc_shadow import PLCShadow, DEFAULT_SCHEDULE
from sc.cycle_log import state_outputs
from sc import config as sc_config
from sc.utils.read_data import get_mode

SAMPLE_VARS = {"cold": ("t_cold_tank_c", "RCE-a"), "hot": ("t_hot_tank_c", "RCE-a"),
               "wind_vel_m_s": ("wind_speed_m_s", "RCE-a"), "solar_rad_w_m2": ("solar_global_w_m2", "RCE-a"),
               "ir_rad_w_m2": ("ir_net_w_m2", "RCE-a" if sc_config.IR_UNIT == "RCEa" else "RCE-b")}   # as the control
OVERRIDE_LABELS = {"cold": "Depósito frío T9 (°C)", "hot": "Depósito caliente T6 (°C)", "wind_vel_m_s": "Viento (m/s)",
                   "solar_rad_w_m2": "Irradiancia solar (W/m²)", "ir_rad_w_m2": "Infrarrojo neto (W/m²)"}
NOTE = ("La previsión meteorológica que usa el modelo es la de hoy (Open-Meteo, AEMET, NASA), no la del día simulado; "
        "las temperaturas y sensores sí son los medidos.")
STEP = timedelta(minutes=15)
PUMP = dict(p_bomba_watts=10, time_step_hours=0.25, min_time_step=15, cop_min=1.0)


# ----------------------------------------------------------------------------------------------- samples
def samples_from_readings(readings, end, n=24, overrides=None):
    """{"data": [24 samples]} ending at `end`, 15 min apart, each with the last reading at or before its time."""
    series = {}
    for r in readings:
        if r.value is None:
            continue
        series.setdefault((r.variable, r.system), []).append((r.ts, r.value))
    for v in series.values():
        v.sort()

    def last_at(key, t):
        pts = series.get(key)
        if not pts:
            return None
        lo, hi = 0, len(pts)
        while lo < hi:                       # bisect on ts
            mid = (lo + hi) // 2
            if pts[mid][0] <= t:
                lo = mid + 1
            else:
                hi = mid
        return pts[lo - 1][1] if lo else None

    data = []
    for k in range(n):
        t = end - (n - 1 - k) * STEP
        secs = t.hour * 3600 + t.minute * 60 + t.second
        doy = t.timetuple().tm_yday
        curr = {name: last_at(key, t) for name, key in SAMPLE_VARS.items()}
        curr.update({"reset_cold": 7, "reset_hot": 16, "mode": get_mode(t),
                     "day_sin": math.sin(2 * math.pi * secs / 86400), "day_cos": math.cos(2 * math.pi * secs / 86400),
                     "year_sin": math.sin(2 * math.pi * doy / 365), "year_cos": math.cos(2 * math.pi * doy / 365)})
        data.append(curr)
    for name, value in (overrides or {}).items():
        if name in SAMPLE_VARS and value is not None:
            data[-1][name] = float(value)
    return {"data": data}


# ----------------------------------------------------------------------------------------------- plan
def demand_in_period(dem, start, end):
    """Sum of the demand keys (ISO seconds) minute by minute, as calculate_dem_for_period()."""
    total, t = 0.0, start
    while t < end:
        key = t.replace(second=0, microsecond=0).isoformat(timespec="seconds")
        if key in dem:
            total += float(dem[key])
        t += timedelta(minutes=1)
    return total


def optimal_plan(prod, target):
    """Cheapest frames first until the target is covered, as calculate_optimal_production_plan(): ([], -1) if not,
    ([], 0.0) when there is no demand."""
    if target <= 0:
        return [], 0.0
    acc, frames = 0.0, []
    for key, value in sorted(prod.items(), key=lambda kv: float(kv[1])):
        acc += float(value)
        frames.append(key)
        if acc >= target:
            break
    if acc < target:
        return [], -1
    return [datetime.fromisoformat(k) for k in frames], acc


def plan_windows(now, kind):
    if kind == "hot":                      # planned at 07:00: today 12:00 -> tomorrow 12:00
        start = now.replace(hour=12, minute=0, second=0, microsecond=0)
        return start, start + timedelta(days=1)
    start = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)   # at 19:00: tomorrow
    return start, start + timedelta(days=1)


def plan_from_forecast(now, info, kind):
    dem = info["demand"]["hot_dem" if kind == "hot" else "cold_dem"]
    prod = info["energy-production"]["hot" if kind == "hot" else "cold"]
    start, end = plan_windows(now, kind)
    target = demand_in_period(dem, start, end)
    frames, total = optimal_plan(prod, target)
    return {"kind": kind, "window": (start.isoformat(sep=" ", timespec="seconds"), end.isoformat(sep=" ", timespec="seconds")),
            "target_j": target, "total_j": total, "frames": frames,
            "temp_mode": {f: (1 if kind == "hot" else 0) for f in frames}}


def active_plan_kind(now):
    """Which plan the control would be holding at this hour: heat from 07:00 to 19:00, cold otherwise."""
    return "hot" if 7 <= now.hour < 19 else "cold"


# ----------------------------------------------------------------------------------------------- simulate
def _forecast_summary(info, now):
    def total(series, hours):
        out = 0.0
        for k, v in series.items():
            try:
                t = datetime.fromisoformat(k)
            except ValueError:
                continue
            if now <= t < now + timedelta(hours=hours):
                out += float(v)
        return out / 3.6e6
    prod, dem = info["energy-production"], info["demand"]
    return {"prod_hot_24h_kwh": total(prod.get("hot", {}), 24), "prod_cold_24h_kwh": total(prod.get("cold", {}), 24),
            "demand_hot_24h_kwh": total(dem.get("hot_dem", {}), 24), "demand_cold_24h_kwh": total(dem.get("cold_dem", {}), 24),
            "rain": info.get("rain-prediction")}


def _outcome(d, state, now, alarm, schedule, policy="frame_mode"):
    plc = PLCShadow(initial_state=state, schedule=schedule or DEFAULT_SCHEDULE)
    nxt = decision_next_state(d, state, plc, now=now, policy=policy)
    out = state_outputs(nxt, alarm=alarm)
    return nxt, out


def _pump(params):
    """PUMP con la potencia de la bomba y el COP mínimo vigentes (los del panel), si se dan."""
    if not params:
        return PUMP
    return {**PUMP, "p_bomba_watts": params["pump_power_w"], "cop_min": params["cop_min"]}


def simulate(now, readings, overrides, state, alarm, predict, schedule=None, demand_j=None, params=None):
    samples = samples_from_readings(readings, end=now, overrides=overrides)
    req = predict(samples)
    if not req or "info" not in req:
        raise RuntimeError("La API de predicción no ha devuelto una previsión")
    info = req["info"]
    kind = active_plan_kind(now)
    plan = plan_from_forecast(now, info, kind)
    other = plan_from_forecast(now, info, "cold" if kind == "hot" else "hot")
    temp_mode = {**other["temp_mode"], **plan["temp_mode"]}        # the control keeps frames of both plans
    demand = plan["target_j"] if demand_j is None else float(demand_j)
    d = decide(now, temp_mode, info["energy-production"], demand, plan["total_j"], action_prev=0,
               mode_prev=kind, rng=lambda a, b: 1.0, **_pump(params))
    next_state, out = _outcome(d, state, now, alarm, schedule)
    inputs = {k: v for k, v in samples["data"][-1].items() if k in SAMPLE_VARS}
    return {
        "now": now.isoformat(sep=" ", timespec="minutes"), "state": state, "alarm": alarm,
        "inputs": inputs, "overrides": {k: v for k, v in (overrides or {}).items() if k in SAMPLE_VARS},
        "forecast": _forecast_summary(info, now),
        "plan": {"kind": kind, "window": plan["window"], "target_kwh": plan["target_j"] / 3.6e6,
                 "total_kwh": plan["total_j"] / 3.6e6 if plan["total_j"] != -1 else -1, "frames_n": len(plan["frames"]),
                 "frames": [f.isoformat(sep=" ", timespec="minutes") for f in sorted(plan["frames"])][:200]},
        "decision": d.as_dict(), "next_state": next_state, "changed": next_state != state,
        "plc_mode": out["plc_mode"], "cover": out["cover"],
        "pumps": {"b1": out["pump_b1"], "b2": out["pump_b2"], "ev1": out["ev1"], "ev2": out["ev2"]},
        "note": NOTE,
    }


# ----------------------------------------------------------------------------------------------- replay
def replay_day(day, readings, state0, predict, schedule=None, params=None):
    """96 control cycles of one day driven by the measured readings: forecast at 00:00 (initial heat plan, as the
    control does when it starts), re-plan at 07:00 (heat) and 19:00 (cold), decide() every 15 min."""
    if isinstance(day, datetime):
        day = day.date()
    start = datetime(day.year, day.month, day.day)
    end = start + timedelta(days=1)
    if not any(start <= r.ts < end for r in readings):
        raise ValueError(f"No hay lecturas de WinCC del {day:%d/%m/%Y} en los ficheros disponibles")
    info = None
    temp_mode, demand, total, kind = {}, 0.0, 0.0, "hot"
    state, action, mode = state0, 0, "parada"
    steps, summary, calls = [], {"si": 0, "no": 0, "parada": 0}, 0

    def replan(now, which):
        nonlocal info, temp_mode, demand, total, kind, calls
        info = predict(samples_from_readings(readings, end=now))
        calls += 1
        p = plan_from_forecast(now, info["info"], which)
        temp_mode = {**temp_mode, **p["temp_mode"]}
        demand, total, kind = p["target_j"], p["total_j"], which

    replan(start, "hot")
    t = start
    while t < end:
        if t.hour == 7 and t.minute == 0:
            replan(t, "hot")
        elif t.hour == 19 and t.minute == 0:
            replan(t, "cold")
        d = decide(t, temp_mode, info["info"]["energy-production"], demand, total, action_prev=action,
                   mode_prev=mode, rng=lambda a, b: 1.0, **_pump(params))
        action, mode, demand = d.action, d.mode, d.demand_after
        if d.plan_reset:
            total = 0.0
        next_state, out = _outcome(d, state, t, False, schedule)
        steps.append({"ts": t.isoformat(sep=" ", timespec="minutes"), "action": d.label, "in_frame": bool(d.in_time_frame),
                      "cop": round(d.cop, 2), "state": state, "next_state": next_state, "cover": out["cover"],
                      "plc_mode": out["plc_mode"], "demand_kwh": round(demand / 3.6e6, 3), "plan": kind,
                      "reason": d.reasons[-1] if d.reasons else ""})
        summary[d.label] += 1
        state = next_state
        t += STEP
    changes = sum(1 for a, b in zip(steps, steps[1:]) if a["next_state"] != b["next_state"])
    return {"day": day.isoformat(), "state0": state0, "steps": steps, "summary": summary, "state_changes": changes,
            "predict_calls": calls, "note": NOTE}
