"""Planning with no demand: a target of 0 (or below) selects no frames and never divides by the demand.

On 2026-10-22 the control stopped at start-up with ZeroDivisionError: the cold demand of the next day summed 0.0 and
calculate_optimal_production_plan() divided by it in a debug line. These tests cover the plan itself, the start-up
planning (heat and cold) and the replanning of the 15-minute cycle (heat at 07:00, cold at 19:00)."""
from datetime import datetime

import pytest

import sc.main as main

PROD = {"2026-10-23T01:00:00": 5.0e5, "2026-10-23T02:00:00": 2.0e5, "2026-10-23T03:00:00": 9.0e5}


@pytest.fixture(autouse=True)
def clean_plan_state(monkeypatch):
    main.temp_mode.clear()
    monkeypatch.setattr(main, "current_dem_target", 0.0)
    monkeypatch.setattr(main, "selected_time_frames", [])
    monkeypatch.setattr(main, "total_predicted_production", 0.0)
    yield
    main.temp_mode.clear()


# ------------------------------------------------------------------------------------------------ the plan
@pytest.mark.parametrize("kind", [0, 1], ids=["cold", "heat"])
@pytest.mark.parametrize("target", [0.0, 0, -3.0e5], ids=["zero", "int-zero", "negative"])
def test_no_demand_selects_no_frames(kind, target):
    frames, total = main.calculate_optimal_production_plan(PROD, target, kind)
    assert frames == [] and total == 0.0
    assert main.temp_mode == {}


def test_no_demand_and_no_production_selects_no_frames():
    assert main.calculate_optimal_production_plan({}, 0.0, 0) == ([], 0.0)


def test_positive_demand_still_takes_the_cheapest_frames_first():
    frames, total = main.calculate_optimal_production_plan(PROD, 6.0e5, 0)
    assert frames == [datetime(2026, 10, 23, 2), datetime(2026, 10, 23, 1)]
    assert total == 7.0e5
    assert main.temp_mode == {datetime(2026, 10, 23, 2): 0, datetime(2026, 10, 23, 1): 0}


def test_unreachable_demand_still_returns_minus_one():
    assert main.calculate_optimal_production_plan(PROD, 1.0e9, 1) == ([], -1)
    assert main.temp_mode == {}


@pytest.mark.parametrize("label, dem_key, prod_key, kind", [("HOT", "hot_dem", "hot", 1), ("COLD", "cold_dem", "cold", 0)])
def test_plan_mode_with_no_demand(monkeypatch, label, dem_key, prod_key, kind):
    monkeypatch.setattr(main, "dem_data", {"hot_dem": {}, "cold_dem": {}})
    monkeypatch.setattr(main, "prod_data", {"hot": dict(PROD), "cold": dict(PROD)})
    monkeypatch.setattr(main, "current_dem_target", 6.0e6)          # target of the previous plan
    main.plan_mode(datetime(2026, 10, 22, 7, 0), label, dem_key, prod_key,
                   datetime(2026, 10, 23, 0, 0), datetime(2026, 10, 24, 0, 0), kind)
    assert main.current_dem_target == 0.0
    assert main.selected_time_frames == [] and main.total_predicted_production == 0.0
    assert main.temp_mode == {}


# ------------------------------------------------------------------------------------------------ start-up
def test_startup_planning_with_no_cold_demand(monkeypatch):
    """The case of the laboratory: heat demand today, no cold demand keys for tomorrow."""
    monkeypatch.setattr(main, "dem_data", {"hot_dem": {"2026-10-22T13:00:00": 3.0e5}, "cold_dem": {"2026-10-22T13:00:00": 1.3e6}})
    monkeypatch.setattr(main, "prod_data", {"hot": {"2026-10-22T13:00:00": 5.0e5}, "cold": dict(PROD)})
    main.plan_at_startup(datetime(2026, 10, 22, 11, 8))
    assert main.current_dem_target == 3.0e5                           # the heat plan
    assert main.temp_mode == {datetime(2026, 10, 22, 13): 1}          # no cold frames


def test_startup_planning_with_no_demand_at_all(monkeypatch):
    monkeypatch.setattr(main, "dem_data", {"hot_dem": {}, "cold_dem": {}})
    monkeypatch.setattr(main, "prod_data", {"hot": dict(PROD), "cold": dict(PROD)})
    main.plan_at_startup(datetime(2026, 10, 22, 11, 8))
    assert main.current_dem_target == 0.0 and main.selected_time_frames == [] and main.temp_mode == {}


def test_startup_planning_with_cold_demand_still_plans_cold(monkeypatch):
    monkeypatch.setattr(main, "dem_data", {"hot_dem": {}, "cold_dem": {"2026-10-23T04:00:00": 6.0e5}})
    monkeypatch.setattr(main, "prod_data", {"hot": {}, "cold": dict(PROD)})
    main.plan_at_startup(datetime(2026, 10, 22, 11, 8))
    assert main.temp_mode == {datetime(2026, 10, 23, 2): 0, datetime(2026, 10, 23, 1): 0}


# ------------------------------------------------------------------------------------------- 15-min cycle
@pytest.mark.parametrize("hour, label", [(7, "heat"), (19, "cold")])
def test_cycle_replanning_with_no_demand(monkeypatch, hour, label):
    now = datetime(2026, 10, 22, hour, 5)

    class FakeDT(datetime):
        @classmethod
        def now(cls, tz=None):
            return now
    monkeypatch.setattr(main, "datetime", FakeDT)
    monkeypatch.setattr(main.random, "uniform", lambda a, b: 1.0)
    monkeypatch.setattr(main, "get_last_data_from_db", lambda: {"data": [{"cold": 12.0, "hot": 40.0}]})
    monkeypatch.setattr(main, "update_predictions", lambda *a, **k: True)
    monkeypatch.setattr(main, "get_now_val", lambda c: 0.0)
    monkeypatch.setattr(main, "get_now_val_2", lambda c: 0)
    monkeypatch.setattr(main, "dem_data", {"hot_dem": {}, "cold_dem": {}})
    monkeypatch.setattr(main, "prod_data", {"hot": dict(PROD), "cold": dict(PROD)})
    monkeypatch.setattr(main, "current_dem_target", 6.0e6)          # previous plan, replaced by the new one
    monkeypatch.setattr(main, "total_predicted_production", 7.0e6)
    monkeypatch.setattr(main, "action", 0)
    monkeypatch.setattr(main, "mode", "hot")
    monkeypatch.setattr(main, "last_prediction_update_day", now.day)
    r = main.get_decision()
    assert r["respuesta"] in ("si", "no", "parada")
    assert main.current_dem_target == 0.0 and main.total_predicted_production == 0.0, label
    assert main.temp_mode == {}
