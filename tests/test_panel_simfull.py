"""Full simulator («¿Qué haría?», nivel completo) and replay of a day: samples from the WinCC readings, the
prediction API (injected), the plan of the control and decide() with reasons."""
from datetime import datetime, timedelta

import pytest

from panel import simfull
from panel.wincc import Reading

SCHEDULE = {"heat_start": "08:00:00", "heat_end": "14:00:00", "parada_end": "16:00:00"}
DAY = datetime(2026, 10, 20)


def readings_for(start, hours, step_min=5):
    """Synthetic readings: cold tank cooling at night, hot tank warm; solar by day; IR negative at night."""
    out = []
    t = start
    while t < start + timedelta(hours=hours):
        h = t.hour + t.minute / 60
        vals = {("t_cold_tank_c", "RCE-a", "TempT9"): 14 - 3 * (h < 8) + (h - 12) * 0.1 * (h > 12),
                ("t_hot_tank_c", "RCE-a", "TempT6"): 40 + (h - 12) * 0.5,
                ("wind_speed_m_s", "RCE-a", "VelVent"): 1.0,
                ("solar_global_w_m2", "RCE-a", "IO_SENSOR1_DATA"): max(0, 600 * (1 - abs(h - 13) / 6)),
                ("ir_net_w_m2", "RCE-b", "E_FIR, neto, [W/m2]"): -70 if (h < 7 or h > 19) else -20}
        for (var, sysm, tag), v in vals.items():
            out.append(Reading(ts=t, tag=tag, system=sysm, variable=var, value=round(v, 2), valid=True))
        t += timedelta(minutes=step_min)
    return out


def fake_predict(samples):
    """A forecast in the API's own formats: 15-min production keys '%Y-%m-%d %H:%M', hourly demand keys ISO."""
    last = samples["data"][-1]
    t0 = datetime(2026, 10, 20, 0, 0)
    prod = {(t0 + timedelta(minutes=15 * i)).strftime("%Y-%m-%d %H:%M"): 4e5 if 6 <= (t0 + timedelta(minutes=15 * i)).hour < 18 else 1e5
            for i in range(192)}
    cold = {(t0 + timedelta(minutes=15 * i)).strftime("%Y-%m-%d %H:%M"): 3e5 if not 8 <= (t0 + timedelta(minutes=15 * i)).hour < 20 else 2e4
            for i in range(192)}
    dem = {(t0 + timedelta(hours=i)).isoformat(timespec="seconds"): 1.2e6 for i in range(72)}
    fake_predict.calls += 1
    fake_predict.last_cold = last["cold"]
    return {"info": {"demand": {"hot_dem": dem, "cold_dem": dem}, "energy-production": {"hot": prod, "cold": cold},
                     "rain-prediction": {"2026-10-20": 0.0}}}
fake_predict.calls = 0


# ----------------------------------------------------------------------------------------------- samples
def test_samples_from_readings_builds_24_samples_like_the_control():
    rs = readings_for(DAY, 24)
    s = simfull.samples_from_readings(rs, end=DAY + timedelta(hours=10))
    assert len(s["data"]) == 24
    last = s["data"][-1]
    assert set(last) >= {"cold", "hot", "wind_vel_m_s", "solar_rad_w_m2", "ir_rad_w_m2", "reset_cold", "reset_hot", "mode",
                         "day_sin", "day_cos", "year_sin", "year_cos"}
    assert last["cold"] == pytest.approx(14.0, abs=0.3) and last["mode"] == "1" and last["reset_cold"] == 7
    assert s["data"][0]["ir_rad_w_m2"] == -70          # 24 samples x 15 min = 6 h back: 04:15 is night


def test_samples_with_overrides_and_missing_data():
    rs = readings_for(DAY, 24)
    s = simfull.samples_from_readings(rs, end=DAY + timedelta(hours=10), overrides={"cold": 20.0, "ir_rad_w_m2": -90.0})
    assert s["data"][-1]["cold"] == 20.0 and s["data"][-1]["ir_rad_w_m2"] == -90.0 and s["data"][-2]["cold"] != 20.0
    empty = simfull.samples_from_readings([], end=DAY)
    assert len(empty["data"]) == 24 and empty["data"][-1]["cold"] is None


# ----------------------------------------------------------------------------------------------- plan
def test_plan_from_forecast_replicates_the_control():
    f = fake_predict({"data": [{"cold": 12.0}]})["info"]
    plan = simfull.plan_from_forecast(DAY + timedelta(hours=7), f, "hot")
    assert plan["kind"] == "hot" and plan["window"][0] == "2026-10-20 12:00:00" and plan["window"][1] == "2026-10-21 12:00:00"
    assert plan["target_j"] == pytest.approx(24 * 1.2e6)
    assert plan["total_j"] >= plan["target_j"] and plan["frames"] and all(isinstance(x, datetime) for x in plan["frames"])
    short = simfull.plan_from_forecast(DAY + timedelta(hours=7), {"demand": f["demand"], "energy-production": {"hot": {"2026-10-20 12:00": 1.0}, "cold": {}}}, "hot")
    assert short["total_j"] == -1 and short["frames"] == []


# ----------------------------------------------------------------------------------------------- simulate
def test_simulate_full_decides_with_the_model_forecast():
    rs = readings_for(DAY, 24)
    r = simfull.simulate(now=DAY + timedelta(hours=10), readings=rs, overrides={}, state="Parada", alarm=False,
                         predict=fake_predict, schedule=SCHEDULE)
    assert r["decision"]["label"] in ("si", "no", "parada") and r["decision"]["reasons"]
    assert r["plan"]["kind"] == "hot" and r["plan"]["frames_n"] > 0
    assert r["next_state"] in ("Parada", "HeatMode1", "ColdMode1") and r["cover"] in ("open", "closed")
    assert r["inputs"]["cold"] == pytest.approx(14.0, abs=0.3) and r["forecast"]["prod_hot_24h_kwh"] > 0
    assert r["note"]                                   # the honesty note about today's weather forecast


def test_simulate_full_uses_overrides():
    rs = readings_for(DAY, 24)
    r = simfull.simulate(now=DAY + timedelta(hours=10), readings=rs, overrides={"cold": 25.5}, state="ColdMode1", alarm=False,
                         predict=fake_predict, schedule=SCHEDULE)
    assert r["inputs"]["cold"] == 25.5 and fake_predict.last_cold == 25.5


# ----------------------------------------------------------------------------------------------- replay
def test_replay_day_runs_96_cycles_with_three_forecasts():
    rs = readings_for(DAY - timedelta(hours=6), 30)
    fake_predict.calls = 0
    r = simfull.replay_day(DAY.date(), readings=rs, state0="Parada", predict=fake_predict, schedule=SCHEDULE)
    assert len(r["steps"]) == 96 and fake_predict.calls == 3
    s0 = r["steps"][0]
    assert {"ts", "action", "in_frame", "cop", "state", "next_state", "cover", "demand_kwh", "plan"} <= set(s0)
    assert r["summary"]["si"] + r["summary"]["no"] + r["summary"]["parada"] == 96
    assert any(st["in_frame"] for st in r["steps"])
    assert r["note"]


def test_replay_day_without_data_is_refused():
    with pytest.raises(ValueError):
        simfull.replay_day(DAY.date(), readings=[], state0="Parada", predict=fake_predict, schedule=SCHEDULE)


def test_replay_with_the_corrected_control_turns_the_pump_on_in_planned_frames():
    rs = readings_for(DAY - timedelta(hours=6), 30)
    r = simfull.replay_day(DAY.date(), readings=rs, state0="Parada", predict=fake_predict, schedule=SCHEDULE)
    assert r["summary"]["si"] > 0
    si_steps = [s for s in r["steps"] if s["action"] == "si"]
    assert all(s["in_frame"] and s["cop"] >= 1 for s in si_steps)
    assert all(s["next_state"] in ("HeatMode1", "ColdMode1") for s in si_steps)
    # no toggling: two consecutive 'si' in the same plan keep the same state
    pairs = [(a, b) for a, b in zip(r["steps"], r["steps"][1:]) if a["action"] == "si" and b["action"] == "si" and a["plan"] == b["plan"]]
    assert pairs and all(a["next_state"] == b["next_state"] for a, b in pairs)
