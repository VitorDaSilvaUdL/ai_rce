"""sc/decision.py: the real-time decision of the control as a pure function with reasons.

The regression part freezes the behaviour of get_decision() captured on 2026-10-21 before the refactor
(same inputs -> same action, mode, remaining demand and dades.csv row), including its quirks:
the production of the active frame is looked up with an ISO key with 'T' and seconds while the forecast uses
"%Y-%m-%d %H:%M", and the pump energy is in Wh while production is in J.
"""
from datetime import datetime

import pytest

from sc import decision as dec

NOW = datetime(2026, 10, 21, 22, 7, 0)
F = datetime(2026, 10, 21, 22, 0)
PROD = {"hot": {}, "cold": {"2026-10-21 22:00": 500000.0}}
PARAMS = dict(p_bomba_watts=10, time_step_hours=0.25, min_time_step=15, cop_min=1.0, rng=lambda a, b: 1.0)


def run(temp_mode, prod, demand, total, action_prev=0, mode_prev="hot"):
    return dec.decide(NOW, temp_mode, prod, demand, total, action_prev=action_prev, mode_prev=mode_prev, **PARAMS)


# ----------------------------------------------------------------------------------------------- regression
@pytest.mark.parametrize("name, temp_mode, prod, demand, total, exp", [
    ("A frame cold, forecast key mismatch", {F: 0}, PROD, 6e6, 7e6, dict(action=0, label="no", mode="cold", demand_after=6e6, in_frame=True, plan_reset=False)),
    ("B frame, no production", {F: 0}, {"hot": {}, "cold": {}}, 6e6, 7e6, dict(action=0, label="no", mode="cold", demand_after=6e6, in_frame=True, plan_reset=False)),
    ("C no active frame", {datetime(2026, 10, 21, 23, 0): 0}, PROD, 6e6, 7e6, dict(action=2, label="parada", mode="hot", demand_after=6e6, in_frame=False, plan_reset=False)),
    ("D insufficient production", {F: 0}, PROD, 6e6, -1, dict(action=2, label="parada", mode="hot", demand_after=6e6, in_frame=0, plan_reset=False)),
    ("E no demand, no frame", {}, PROD, 0.0, 0.0, dict(action=0, label="no", mode="hot", demand_after=0.0, in_frame=False, plan_reset=False)),
    ("G frame hot", {F: 1}, {"hot": {"2026-10-21 22:00": 300000.0}, "cold": {}}, 6e6, 7e6, dict(action=0, label="no", mode="hot", demand_after=6e6, in_frame=True, plan_reset=False)),
    ("H frame, no demand", {F: 0}, PROD, 0.0, 7e6, dict(action=0, label="no", mode="cold", demand_after=0.0, in_frame=True, plan_reset=False)),
])
def test_regression_against_the_captured_behaviour(name, temp_mode, prod, demand, total, exp):
    d = run(temp_mode, prod, demand, total)
    assert (d.action, d.label, d.mode, d.demand_after, d.in_time_frame, d.plan_reset) == \
        (exp["action"], exp["label"], exp["mode"], exp["demand_after"], exp["in_frame"], exp["plan_reset"]), name


def test_when_the_frame_key_matches_the_pump_turns_on_and_demand_is_reduced():
    prod = {"hot": {}, "cold": {"2026-10-21T22:00:00": 500000.0}}       # the key format the lookup expects
    d = run({F: 0}, prod, 6e6, 7e6)
    assert d.action == 1 and d.label == "si" and d.cop == 500000.0 / 2.5
    assert d.demand_after == 6e6 - 10 * 0.25 * 1.0 and d.plan_reset is False
    assert "cop_ok" in d.codes and "frame_prod" in d.codes


def test_tiny_demand_is_covered_and_the_plan_is_reset():
    prod = {"hot": {}, "cold": {"2026-10-21T22:00:00": 500000.0}}
    d = run({F: 0}, prod, 1.0, 7e6)
    assert d.action == 0 and d.plan_reset is True and d.demand_after == 0.0 and "plan_done" in d.codes


# ----------------------------------------------------------------------------------------------- reasons
def test_reasons_are_in_plain_spanish_and_name_the_quirk():
    d = run({F: 0}, PROD, 6e6, 7e6)
    text = " ".join(d.reasons)
    assert "franja" in text.lower() and "frío" in text
    assert "sin dato de producción" in text.lower()
    assert d.codes[:2] == ["in_frame:cold", "frame_prod_missing"]
    assert any("COP" in r for r in d.reasons)


def test_out_of_frame_and_insufficient_reasons():
    assert "out_of_frames" in run({}, PROD, 6e6, 7e6).codes
    assert run({F: 0}, PROD, 6e6, -1).codes == ["insufficient_production"]
    d = run({}, PROD, 0.0, 0.0)
    assert d.codes[-1] == "no_pending_demand" and d.action == 0


def test_as_dict_is_json_friendly():
    d = run({F: 0}, PROD, 6e6, 7e6)
    out = d.as_dict()
    assert out["label"] == "no" and isinstance(out["reasons"], list) and out["frame_start"] == "2026-10-21 22:00:00"


# ----------------------------------------------------------------------------------------------- get_decision uses it
def test_get_decision_delegates_and_keeps_its_row(monkeypatch):
    import sc.main as main

    class FakeDT(datetime):
        @classmethod
        def now(cls, tz=None):
            return NOW
    monkeypatch.setattr(main, "datetime", FakeDT)
    monkeypatch.setattr(main.random, "uniform", lambda a, b: 1.0)
    monkeypatch.setattr(main, "get_last_data_from_db", lambda: {"data": [{"cold": 12.0, "hot": 40.0}]})
    monkeypatch.setattr(main, "update_predictions", lambda *a, **k: False)
    monkeypatch.setattr(main, "get_now_val", lambda c: 500000.0)     # the row readers use the other key format
    monkeypatch.setattr(main, "get_now_val_2", lambda c: 0)
    main.temp_mode.clear()
    main.temp_mode[F] = 0
    monkeypatch.setattr(main, "prod_data", {"hot": {}, "cold": {"2026-10-21T22:00:00": 500000.0}})
    monkeypatch.setattr(main, "dem_data", {"hot_dem": {}, "cold_dem": {}})
    monkeypatch.setattr(main, "current_dem_target", 6e6)
    monkeypatch.setattr(main, "total_predicted_production", 7e6)
    monkeypatch.setattr(main, "selected_time_frames", ["x"])
    monkeypatch.setattr(main, "action", 0)
    monkeypatch.setattr(main, "mode", "hot")
    monkeypatch.setattr(main, "last_prediction_update_day", NOW.day)
    r = main.get_decision()
    assert r == {"respuesta": "si"}
    assert main.action == 1 and main.mode == "cold" and main.current_dem_target == 6e6 - 2.5
    row = main.last_decision_row
    assert row[0] == NOW and row[1] is True and row[3] == "si" and row[4] == "cold" and row[6] == 500000.0
    assert main.last_decision.action == 1 and "cop_ok" in main.last_decision.codes
    main.temp_mode.clear()
