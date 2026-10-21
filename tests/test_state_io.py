"""Shared state between the control loop and the panel: state/status.json (SC writes) and
state/control.json (panel writes, SC reads at the start of every cycle)."""
import json

import pytest

from sc import state_io
from sc.plc_shadow import PLCShadow
from sc import cycle_log

SCHEDULE = {"heat_start": "08:00:00", "heat_end": "14:00:00", "parada_end": "16:00:00"}


# ----------------------------------------------------------------------------------------------- state_io
def test_write_json_atomic_leaves_no_temp_file_and_round_trips(tmp_path):
    p = tmp_path / "x.json"
    state_io.write_json_atomic(p, {"a": 1, "when": "2026-10-21 10:00:00"})
    assert json.loads(p.read_text(encoding="utf-8")) == {"a": 1, "when": "2026-10-21 10:00:00"}
    assert [f.name for f in tmp_path.iterdir()] == ["x.json"]


def test_read_json_returns_default_when_missing_or_corrupt(tmp_path):
    assert state_io.read_json(tmp_path / "none.json", {"d": 1}) == {"d": 1}
    (tmp_path / "bad.json").write_text("{not json", encoding="utf-8")
    assert state_io.read_json(tmp_path / "bad.json", None) is None


def test_control_defaults_and_merge(tmp_path):
    assert state_io.read_control(tmp_path) == state_io.CONTROL_DEFAULTS
    state_io.write_control(tmp_path, paused=True, paused_by="AC")
    c = state_io.read_control(tmp_path)
    assert c["paused"] is True and c["paused_by"] == "AC" and c["set_state_seq"] == 0
    state_io.write_control(tmp_path, set_state="ColdMode1", set_state_seq=1)
    c = state_io.read_control(tmp_path)
    assert c["paused"] is True and c["set_state"] == "ColdMode1" and c["set_state_seq"] == 1


# ----------------------------------------------------------------------------------------------- run_cycle
@pytest.fixture
def main_in_shadow(monkeypatch, tmp_path):
    import sc.main as main
    monkeypatch.setattr(main, "get_decision", lambda: {"respuesta": "si"})
    monkeypatch.setattr(main, "last_decision_row", ["2026-10-21 10:00:00", True, 6.1e6, "si", "cold", 1, 2, 3, 4])
    monkeypatch.setattr(main, "system_data", {"data": [{"cold": 11.0, "hot": 40.0}, {"cold": 12.5, "hot": 41.2,
                                                                                     "wind_vel_m_s": 1.1}]})
    monkeypatch.setattr(main, "prod_data", {"hot": {"2026-10-21 10:00": 1.0}, "cold": {"2026-10-21 22:00": 2.0}})
    monkeypatch.setattr(main, "dem_data", {"hot_dem": {"2026-10-21T11:00:00": 3.0}, "cold_dem": {}})
    monkeypatch.setattr(main, "rain_data", {"2026-10-21": 0.1})
    monkeypatch.setattr(main, "selected_time_frames", ["2026-10-21T22:00:00"])
    monkeypatch.setattr(main, "current_dem_target", 6.1e6)
    monkeypatch.setattr(main, "total_predicted_production", 4.2e6)
    monkeypatch.setattr(main, "_last_set_state_seq", 0)
    return main


def test_run_cycle_writes_status_json(main_in_shadow, tmp_path):
    main = main_in_shadow
    plc = PLCShadow(initial_state="ColdMode1", schedule=SCHEDULE)
    main.run_cycle(plc, tmp_path / "dades.csv", state_dir=tmp_path)
    st = json.loads((tmp_path / "status.json").read_text(encoding="utf-8"))
    assert st["execution"] == "shadow"
    assert (st["plc_state_before"], st["plc_state"], st["plc_mode"], st["cover"]) == ("ColdMode1", "HeatMode1", "hot", "closed")
    assert st["inputs"] == {"cold": 12.5, "hot": 41.2, "wind_vel_m_s": 1.1}
    assert st["forecast"]["prod_cold"] == {"2026-10-21 22:00": 2.0}
    assert st["forecast"]["demand_hot"] == {"2026-10-21T11:00:00": 3.0}
    assert st["plan"] == {"target_demand_j": 6.1e6, "selected_frames": ["2026-10-21T22:00:00"], "predicted_production_j": 4.2e6}
    assert st["decision"]["action"] == "si" and st["decision"]["in_time_frame"] is True
    assert st["paused"] is False
    assert st["cycle_time"] and st["next_cycle_time"] > st["cycle_time"]
    assert st["written_at"]


def test_run_cycle_in_pause_does_not_apply_the_decision(main_in_shadow, tmp_path):
    main = main_in_shadow
    state_io.write_control(tmp_path, paused=True, paused_by="AC")
    plc = PLCShadow(initial_state="ColdMode1", schedule=SCHEDULE)
    main.run_cycle(plc, tmp_path / "dades.csv", state_dir=tmp_path)
    assert plc.get_system_state()[0] == "ColdMode1"           # unchanged
    st = json.loads((tmp_path / "status.json").read_text(encoding="utf-8"))
    assert st["paused"] is True and st["paused_by"] == "AC"
    assert st["plc_state"] == "ColdMode1" and st["decision"]["next_state"] == "HeatMode1"   # what it would have done
    row = (tmp_path / "dades.csv").read_text(encoding="utf-8").splitlines()[1]
    assert ",ColdMode1,ColdMode1," in row


def test_run_cycle_applies_set_state_once(main_in_shadow, tmp_path):
    main = main_in_shadow
    main.get_decision = lambda: {"respuesta": "no"}
    state_io.write_control(tmp_path, set_state="Parada", set_state_seq=1)
    plc = PLCShadow(initial_state="ColdMode1", schedule=SCHEDULE)
    main.run_cycle(plc, tmp_path / "dades.csv", state_dir=tmp_path)
    assert plc.get_system_state()[0] == "Parada"
    plc.state = "HeatMode1"                                   # someone else changed it; same seq must not re-apply
    main.run_cycle(plc, tmp_path / "dades.csv", state_dir=tmp_path)
    assert plc.get_system_state()[0] == "HeatMode1"


def test_run_cycle_without_decision_still_writes_status(main_in_shadow, tmp_path):
    main = main_in_shadow
    main.get_decision = lambda: {"respuesta": "parada"}
    main.last_decision_row = None
    plc = PLCShadow(initial_state="ColdMode1", schedule=SCHEDULE)
    main.run_cycle(plc, tmp_path / "dades.csv", state_dir=tmp_path)
    st = json.loads((tmp_path / "status.json").read_text(encoding="utf-8"))
    assert st["decision"]["action"] == "" and st["plc_state"] == "Parada"


def test_status_carries_the_decision_reasons(main_in_shadow, tmp_path):
    from sc import decision as dec
    from datetime import datetime
    main = main_in_shadow
    d = dec.decide(datetime(2026, 10, 21, 22, 7), {datetime(2026, 10, 21, 22, 0): 0},
                   {"hot": {}, "cold": {"2026-10-21T22:00:00": 5e5}}, 6e6, 7e6, rng=lambda a, b: 1.0)
    main.last_decision = d
    plc = PLCShadow(initial_state="ColdMode1", schedule=SCHEDULE)
    main.run_cycle(plc, tmp_path / "dades.csv", state_dir=tmp_path)
    st = json.loads((tmp_path / "status.json").read_text(encoding="utf-8"))
    assert st["decision"]["reasons"] and "cop_ok" in st["decision"]["codes"]
    assert st["decision"]["cop"] == 5e5 / 2.5 and st["decision"]["frame_start"] == "2026-10-21 22:00:00"
    assert st["decision"]["action"] == "si"          # row label still wins
