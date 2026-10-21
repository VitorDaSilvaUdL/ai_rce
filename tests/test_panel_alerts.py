"""Alerts: each rule of the specification fires on its condition and stays quiet otherwise."""
from datetime import datetime, timedelta

from panel import alerts

NOW = datetime(2026, 10, 21, 11, 0, 0)


def ctx(**over):
    base = {
        "now": NOW, "mode": "shadow",
        "wincc_last_write": NOW - timedelta(minutes=3),
        "series": {}, "heartbeat_age_s": 60, "sc_running": True, "api_running": True, "api_ok": True,
        "status": {"age_s": 300, "forecast": {"updated_day": NOW.day}, "paused": False, "paused_since": "",
                   "pumps": {"b2": "off", "b1": "auto"}, "decision": {"action": "no"}, "errors": []},
        "filebeat": "running", "disk_free_gb": 100.0, "plc_alarm": False, "cover_measured": "",
        "flow_l_min": 0.0,
    }
    base.update(over)
    return base


def ids(c):
    return {a.id for a in alerts.evaluate(c)}


def test_all_quiet_when_everything_is_fine():
    assert ids(ctx()) == set()


def test_wincc_not_writing():
    a = alerts.evaluate(ctx(wincc_last_write=NOW - timedelta(minutes=20)))
    assert a and a[0].id == "wincc_stale" and "WinCC" in a[0].title and a[0].action
    assert ids(ctx(wincc_last_write=None)) == {"wincc_stale"}


def test_probe_stale_frozen_and_invalid():
    series = {("t_cold_tank_c", "RCE-a"): {"stale": True, "frozen": False, "last_invalid": False, "age_s": 4000},
              ("t_hot_tank_c", "RCE-a"): {"stale": False, "frozen": True, "last_invalid": False, "age_s": 60},
              ("ir_net_w_m2", "RCE-b"): {"stale": False, "frozen": False, "last_invalid": True, "age_s": 60}}
    got = ids(ctx(series=series))
    assert got == {"probe:t_cold_tank_c/RCE-a", "probe:t_hot_tank_c/RCE-a", "probe:ir_net_w_m2/RCE-b"}
    titles = {a.id: a.title for a in alerts.evaluate(ctx(series=series))}
    assert "Depósito frío (T9)" in titles["probe:t_cold_tank_c/RCE-a"]


def test_control_stopped_or_dead():
    assert ids(ctx(sc_running=False)) == {"sc_stopped"}
    assert ids(ctx(heartbeat_age_s=1500)) == {"sc_dead"}
    assert ids(ctx(heartbeat_age_s=None)) == {"sc_dead"}


def test_api_down_and_forecast_error():
    assert ids(ctx(api_running=False, api_ok=False)) == {"api_down"}
    assert ids(ctx(api_ok=False)) == {"api_down"}
    assert ids(ctx(status={**ctx()["status"], "forecast": {"updated_day": (NOW - timedelta(days=2)).day}})) == {"forecast_old"}


def test_filebeat_and_disk():
    assert ids(ctx(filebeat="stopped")) == {"filebeat_down"}
    assert ids(ctx(filebeat="missing")) == {"filebeat_missing"}
    assert ids(ctx(disk_free_gb=1.5)) == {"disk_low"}


def test_real_mode_only_rules():
    pump_on = {**ctx()["status"], "pumps": {"b2": "on", "b1": "auto"}}
    assert ids(ctx(mode="shadow", status=pump_on, flow_l_min=0.0)) == set()
    assert ids(ctx(mode="real", status=pump_on, flow_l_min=0.0)) == {"flow_zero_pump_on"}
    assert ids(ctx(mode="real", status=pump_on, flow_l_min=3.2)) == set()
    assert ids(ctx(mode="real", plc_alarm=True)) == {"plc_alarm"}
    assert ids(ctx(mode="real", cover_measured="moving", status={**ctx()["status"], "age_s": 1200})) == {"cover_stuck"}
    paused = {**ctx()["status"], "paused": True, "paused_since": (NOW - timedelta(hours=3)).isoformat(sep=" ")}
    assert ids(ctx(mode="real", status=paused)) == {"paused_long"}
    assert ids(ctx(mode="shadow", status=paused)) == set()


def test_alerts_are_sorted_by_severity():
    a = alerts.evaluate(ctx(disk_free_gb=1.0, sc_running=False, wincc_last_write=None))
    assert [x.level for x in a] == sorted([x.level for x in a], key=alerts.LEVEL_ORDER.index)
    assert a[0].id == "wincc_stale"
