"""K1 metrics every 5 min from the WinCC readings, K2 trial summaries, and the operation column of the diary."""
from datetime import datetime, timedelta

import pytest

from panel import metrics, journal, trials
from panel.wincc import Reading

T0 = datetime(2026, 10, 21, 22, 0)


def mk(ts, var, value, system="RCE-a"):
    return Reading(ts=ts, tag=var, system=system, variable=var, value=value, valid=True)


def night_readings(start=T0, hours=2, flow=3.0, t_in=12.0, t_out=10.0):
    out = []
    t = start
    while t < start + timedelta(hours=hours):
        out += [mk(t, "flow_rce_l_min", flow), mk(t, "t_circuit_2_c", t_in), mk(t, "t_circuit_4_c", t_out),
                mk(t, "t_cold_tank_c", 14 - (t - start).total_seconds() / 3600), mk(t, "t_hot_tank_c", 40.0),
                mk(t, "t_ambient_c", 9.0), mk(t, "ir_net_w_m2", -70.0, "RCE-a"), mk(t, "wind_speed_m_s", 1.0),
                mk(t, "ir_net_w_m2", 33.0, "RCE-b"),          # unit-B pyrgeometer tags: constant, not a measurement
                mk(t, "rh_ambient_pct", 60.0), mk(t, "solar_global_w_m2", 0.0)]
        t += timedelta(minutes=5)
    return out


# ----------------------------------------------------------------------------------------------- K1 metrics
def test_thermal_power_sign_and_energy_of_an_interval():
    rs = night_readings()
    m = metrics.interval_metrics(rs, T0, T0 + timedelta(minutes=5))
    q_m3s = 3.0 / 60000
    expected_w = metrics.RHO * metrics.CP * q_m3s * (10.0 - 12.0)            # out - in: cooling -> negative
    assert m["p_thermal_w"] == pytest.approx(expected_w)
    assert m["p_cold_w"] == pytest.approx(-expected_w) and m["p_heat_w"] == 0.0
    assert m["e_kwh"] == pytest.approx(expected_w * 300 / 3.6e6)
    assert m["e_cold_kwh"] == pytest.approx(-expected_w * 300 / 3.6e6) and m["e_heat_kwh"] == 0.0
    assert m["dt_c"] == -2.0 and m["t_cold_tank_c"] == 14.0 and m["ir_net_w_m2"] == -70.0
    assert m["night_id"] == "2026-10-21" and m["night_hour"] == pytest.approx(1.0)      # 22:00 = 1 h after 21:00


def test_night_id_and_hour_before_noon_belong_to_the_previous_night():
    rs = night_readings(start=datetime(2026, 10, 22, 3, 0), hours=1)
    m = metrics.interval_metrics(rs, datetime(2026, 10, 22, 3, 0), datetime(2026, 10, 22, 3, 5))
    assert m["night_id"] == "2026-10-21" and m["night_hour"] == pytest.approx(6.0)
    rs = night_readings(start=datetime(2026, 10, 22, 13, 0), hours=1)
    m = metrics.interval_metrics(rs, datetime(2026, 10, 22, 13, 0), datetime(2026, 10, 22, 13, 5))
    assert m["night_id"] == "2026-10-22" and m["night_hour"] == pytest.approx(-8.0)


def test_interval_without_flow_or_data_gives_zero_power_and_none_values():
    rs = night_readings(flow=0.0)
    m = metrics.interval_metrics(rs, T0, T0 + timedelta(minutes=5))
    assert m["p_thermal_w"] == 0.0 and m["flow_l_min"] == 0.0
    m2 = metrics.interval_metrics([], T0, T0 + timedelta(minutes=5))
    assert m2["p_thermal_w"] is None and m2["t_cold_tank_c"] is None and m2["n_samples"] == 0


def test_metrics_log_appends_only_completed_intervals(tmp_path):
    log = metrics.MetricsLog(tmp_path / "metrics.csv")
    rs = night_readings(hours=1)
    now = T0 + timedelta(minutes=33)
    n = log.update(rs, now=now, context={"cover": "open", "cover_measured": "", "plc_mode": "cold", "execution": "shadow",
                                         "trial_id": "E1", "operation": "control_ia"})
    assert n == 6                                               # 22:00 .. 22:25 completed, 22:30 not yet
    rows = log.rows()
    assert rows[0]["timestamp"] == "2026-10-21 22:00:00" and rows[-1]["timestamp"] == "2026-10-21 22:25:00"
    assert rows[0]["cover"] == "open" and rows[0]["trial_id"] == "E1" and float(rows[0]["p_thermal_w"]) < 0
    assert log.update(rs, now=now, context={}) == 0              # nothing new
    assert log.update(rs, now=now + timedelta(minutes=5), context={}) == 1
    assert list(rows[0].keys()) == metrics.HEADER


def test_metrics_log_starts_from_the_last_hours_on_first_run(tmp_path):
    log = metrics.MetricsLog(tmp_path / "metrics.csv", backfill=timedelta(minutes=20))
    rs = night_readings(hours=2)
    n = log.update(rs, now=T0 + timedelta(hours=2), context={})
    assert n == 4                                               # only the last 20 min are back-filled


# ----------------------------------------------------------------------------------------------- diary operation
def test_diary_has_an_operation_column(tmp_path):
    d = journal.Diary(tmp_path / "diario.csv")
    r = d.add(author="AC", kind="ensayo_inicio", text="tipo 2", now=T0, operation="manual")
    assert r["operacion"] == "manual" and journal.DIARY_HEADER[-1] == "operacion"
    r2 = d.add(author="AC", kind="incidencia", text="x", now=T0 + timedelta(hours=1))
    assert r2["operacion"] == "manual"                         # inherited from the open trial
    assert d.open_trial_info() == {"ensayo_id": "E20261021-2200", "operacion": "manual", "inicio": "2026-10-21 22:00:00", "texto": "tipo 2"}
    assert "manual" in journal.OPERATIONS


# ----------------------------------------------------------------------------------------------- K2 trials
def test_trial_summary(tmp_path):
    d = journal.Diary(tmp_path / "diario.csv")
    d.add(author="AC", kind="ensayo_inicio", text="Tipo 2; unidad A", now=T0, operation="control_ia")
    log = metrics.MetricsLog(tmp_path / "metrics.csv")
    rs = night_readings(hours=3)
    log.update(rs, now=T0 + timedelta(hours=3), context={"cover": "open", "execution": "shadow"})
    decisions = [{"ts": T0 + timedelta(minutes=15 * i), "cover_open": 1 if i < 8 else 0, "cover_measured": ""} for i in range(12)]
    s = trials.summarize(d.open_trial_info(), end=T0 + timedelta(hours=3), metrics_rows=log.rows(), decisions=decisions)
    assert s["ensayo_id"] == "E20261021-2200" and s["operacion"] == "control_ia" and s["estado"] == "en_curso"
    assert s["e_cold_kwh"] > 0 and s["e_heat_kwh"] == 0
    assert s["t9_inicio_c"] == pytest.approx(14.0) and s["t9_fin_c"] == pytest.approx(14.0 - 2 - 55 / 60, abs=0.1)
    assert s["delta_t9_c"] < 0 and s["t_ambient_media_c"] == 9.0 and s["ir_medio_w_m2"] == -70.0
    assert s["horas_tapa_abierta"] == pytest.approx(2.0)      # 8 cycles x 15 min
    assert s["duracion_h"] == pytest.approx(3.0)


def test_trials_log_writes_in_progress_and_closed_rows(tmp_path):
    d = journal.Diary(tmp_path / "diario.csv")
    d.add(author="AC", kind="ensayo_inicio", text="t", now=T0, operation="manual")
    log = metrics.MetricsLog(tmp_path / "metrics.csv")
    log.update(night_readings(hours=1), now=T0 + timedelta(hours=1), context={})
    tl = trials.TrialsLog(tmp_path / "ensayos.csv")
    tl.update(d, log, decisions=[], now=T0 + timedelta(hours=1))
    assert tl.rows()[-1]["estado"] == "en_curso"
    d.add(author="AC", kind="ensayo_fin", text="", now=T0 + timedelta(hours=1, minutes=5))
    tl.update(d, log, decisions=[], now=T0 + timedelta(hours=1, minutes=10))
    rows = tl.rows()
    assert rows[-1]["estado"] == "cerrado" and rows[-1]["ensayo_id"] == "E20261021-2200"
    assert tl.update(d, log, decisions=[], now=T0 + timedelta(hours=2)) == 0     # closed once
