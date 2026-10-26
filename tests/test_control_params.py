"""Control parameters that can be changed while the control runs: the electrical power of the pump (W) and the
minimum COP to start it. The panel writes them to state/control_params.json; the control reads them at the start of
every cycle and falls back to sc/config (pump.power_watts) and decision.COP_MIN_DEFAULT when they are missing."""
import json
from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from sc import decision as dec
from sc import state_io
from sc.plc_shadow import PLCShadow
from tests.test_panel_web import ctx, login  # noqa: F401  (fixture and helper of the panel web tests)
from tests.test_state_io import SCHEDULE, main_in_shadow  # noqa: F401
from panel import web

NOW = datetime(2026, 10, 21, 22, 7)
FRAME = datetime(2026, 10, 21, 22, 0)


# ------------------------------------------------------------------------------------------------ the file
def test_params_file_round_trip_and_defaults(tmp_path):
    assert state_io.read_params(tmp_path) == {"pump_power_w": None, "cop_min": None, "updated_by": "", "updated_at": ""}
    state_io.write_params(tmp_path, pump_power_w=45.0, updated_by="rce_admin", other="ignored")
    state_io.write_params(tmp_path, cop_min=2.5)
    p = state_io.read_params(tmp_path)
    assert p["pump_power_w"] == 45.0 and p["cop_min"] == 2.5 and p["updated_by"] == "rce_admin" and "other" not in p
    (tmp_path / state_io.PARAMS_NAME).write_text("{broken", encoding="utf-8")
    assert state_io.read_params(tmp_path)["pump_power_w"] is None


# ------------------------------------------------------------------------------------------- effective values
def test_effective_params_take_the_panel_values_when_valid():
    e = dec.effective_params({"pump_power_w": 45, "cop_min": 2.5}, config_pump_w=10)
    assert e == {"pump_power_w": 45.0, "cop_min": 2.5, "source": {"pump_power_w": "panel", "cop_min": "panel"}}


@pytest.mark.parametrize("params", [{}, {"pump_power_w": None, "cop_min": None}, {"pump_power_w": 0, "cop_min": -1},
                                    {"pump_power_w": "abc", "cop_min": "x"}, {"pump_power_w": 1e9, "cop_min": 1e9}])
def test_effective_params_fall_back_to_the_configuration(params):
    e = dec.effective_params(params, config_pump_w=10)
    assert e == {"pump_power_w": 10.0, "cop_min": dec.COP_MIN_DEFAULT, "source": {"pump_power_w": "config", "cop_min": "config"}}


# --------------------------------------------------------------------------------------------- the decision
def _decide_with(main, monkeypatch, params, frame_prod_j):
    class FakeDT(datetime):
        @classmethod
        def now(cls, tz=None):
            return NOW
    monkeypatch.setattr(main, "datetime", FakeDT)
    monkeypatch.setattr(main.random, "uniform", lambda a, b: 1.0)
    monkeypatch.setattr(main, "get_last_data_from_db", lambda: {"data": [{"cold": 12.0, "hot": 40.0}]})
    monkeypatch.setattr(main, "update_predictions", lambda *a, **k: False)
    monkeypatch.setattr(main, "get_now_val", lambda c: 0.0)
    monkeypatch.setattr(main, "get_now_val_2", lambda c: 0)
    main.temp_mode.clear()
    main.temp_mode[FRAME] = 0
    monkeypatch.setattr(main, "prod_data", {"hot": {}, "cold": {"2026-10-21 22:00": frame_prod_j}})
    monkeypatch.setattr(main, "dem_data", {"hot_dem": {}, "cold_dem": {}})
    monkeypatch.setattr(main, "current_dem_target", 6e6)
    monkeypatch.setattr(main, "total_predicted_production", 7e6)
    monkeypatch.setattr(main, "action", 0)
    monkeypatch.setattr(main, "mode", "hot")
    monkeypatch.setattr(main, "last_prediction_update_day", NOW.day)
    monkeypatch.setattr(main, "control_params", params)
    r = main.get_decision()
    main.temp_mode.clear()
    return r, main.last_decision


def test_the_decision_uses_the_pump_power_and_threshold_of_the_panel(monkeypatch):
    import sc.main as main
    # 36 kJ in the frame; pump of 40 W during 15 min = 36 kJ -> COP 1.0
    r, d = _decide_with(main, monkeypatch, {"pump_power_w": 40, "cop_min": 1.0}, 36000.0)
    assert d.cop == pytest.approx(1.0) and r == {"respuesta": "si"}
    r, d = _decide_with(main, monkeypatch, {"pump_power_w": 40, "cop_min": 1.5}, 36000.0)
    assert d.cop == pytest.approx(1.0) and r["respuesta"] == "no" and "cop_low" in d.codes
    assert main.last_params["cop_min"] == 1.5 and main.last_params["source"]["cop_min"] == "panel"


def test_without_panel_values_the_decision_keeps_the_configuration(monkeypatch):
    import sc.main as main
    r, d = _decide_with(main, monkeypatch, {}, 36000.0)
    assert d.pump_energy_wh == pytest.approx(main.P_BOMBA_WATTS * 0.25)
    assert main.last_params["source"] == {"pump_power_w": "config", "cop_min": "config"}


def test_run_cycle_reads_the_params_every_cycle_and_reports_them(main_in_shadow, tmp_path):  # noqa: F811
    main = main_in_shadow
    state_io.write_params(tmp_path, pump_power_w=45.0, cop_min=3.0, updated_by="rce_admin")
    plc = PLCShadow(initial_state="ColdMode1", schedule=SCHEDULE)
    main.run_cycle(plc, tmp_path / "dades.csv", state_dir=tmp_path)
    assert main.control_params["pump_power_w"] == 45.0
    st = json.loads((tmp_path / "status.json").read_text(encoding="utf-8"))
    assert st["params"]["pump_power_w"] == 45.0 and st["params"]["cop_min"] == 3.0
    state_io.write_params(tmp_path, pump_power_w=60.0)                 # changed while running: next cycle
    main.run_cycle(plc, tmp_path / "dades.csv", state_dir=tmp_path)
    st = json.loads((tmp_path / "status.json").read_text(encoding="utf-8"))
    assert st["params"]["pump_power_w"] == 60.0


# ------------------------------------------------------------------------------------------------ the panel
def test_panel_shows_and_changes_the_params(ctx):  # noqa: F811
    client = login(TestClient(web.create_app(ctx)))
    p = client.get("/api/params").json()
    assert p["effective"]["source"] == {"pump_power_w": "config", "cop_min": "config"}
    assert p["defaults"]["cop_min"] == dec.COP_MIN_DEFAULT
    assert client.post("/api/params", json={"pump_power_w": 45}).status_code == 423        # needs the lock
    assert client.post("/api/lock/take").status_code == 200
    r = client.post("/api/params", json={"pump_power_w": 45, "cop_min": 2.5})
    assert r.status_code == 200 and r.json()["effective"]["pump_power_w"] == 45.0
    stored = state_io.read_params(ctx.state_dir)
    assert stored["pump_power_w"] == 45.0 and stored["cop_min"] == 2.5 and stored["updated_by"] == "rce_admin"
    audit = client.get("/api/audit", params={"category": "control"}).json()
    assert any("control:parametros" in a["accion"] and a["categoria"] == "control" for a in audit["rows"])
    assert client.post("/api/params", json={"pump_power_w": -3}).status_code == 422
    assert client.post("/api/params", json={"cop_min": 1e7}).status_code == 422
    r = client.post("/api/params", json={"reset": True})                                      # back to the configuration
    assert r.status_code == 200 and r.json()["effective"]["source"]["pump_power_w"] == "config"


def test_only_rce_admin_changes_the_params(ctx):  # noqa: F811
    client = TestClient(web.create_app(ctx))
    login(client)                                                       # creates rce_admin
    ctx.people.add("ana.garcia", "abcd1234")
    other = TestClient(web.create_app(ctx))
    other.post("/api/login", json={"name": "ana.garcia", "pin": "abcd1234"})
    other.post("/api/password", json={"current": "abcd1234", "new": "otra-clave-1"})
    other.post("/api/lock/take")
    assert other.post("/api/params", json={"pump_power_w": 45}).status_code == 403
