"""Shadow mode: ai_rce runs its full control cycle without ever talking to the PLC.

The decisions are applied to a virtual state and logged (dades.csv) with the operating mode and the
cover (horizontal lid) position they imply, so dashboards can plot them next to the real measurements.
"""
import csv
import datetime as real_datetime
import importlib
import json
import types

import pytest
import snap7

from sc.plc_controller import PLCController
from sc.plc_shadow import PLCShadow, make_plc
from sc import cycle_log


# ----------------------------------------------------------------------------------------------- helpers
def fake_now(monkeypatch, when):
    """Freeze datetime.datetime.now() as seen by sc.plc_controller (it uses `import datetime`)."""
    class FakeDateTime(real_datetime.datetime):
        @classmethod
        def now(cls, tz=None):
            return when
    import sc.plc_controller as pc
    monkeypatch.setattr(pc, "datetime", types.SimpleNamespace(datetime=FakeDateTime))


SCHEDULE = {"heat_start": "08:00:00", "heat_end": "14:00:00", "parada_end": "16:00:00"}


# ----------------------------------------------------------------------------------------------- no PLC access
def test_shadow_cycle_never_creates_a_snap7_client(monkeypatch):
    def forbidden(*a, **k):
        raise AssertionError("shadow mode created a snap7 client")
    monkeypatch.setattr(snap7.client, "Client", forbidden)

    plc = PLCShadow(initial_state="ColdMode1", schedule=SCHEDULE)
    plc.connect()
    estado, comb = plc.get_system_state()
    nuevo = plc.decide_next_state_from_nn("si", estado)
    plc.final_write_to_plc_nn_mode(nuevo, comb)
    plc.get_current_mode()
    plc.read_cover_position()
    plc.disconnect()
    assert plc.client is None


@pytest.mark.parametrize("call", [
    lambda p: p._write_bool_db(500, 12, 0, True),
    lambda p: p.read_bool(10, 0, 0),
    lambda p: p.set_cold_mode(),
    lambda p: p.set_heat_mode(),
    lambda p: p.set_parada_mode(),
    lambda p: p.set_automatic_mode(),
])
def test_shadow_blocks_every_plc_read_and_write(call):
    with pytest.raises(RuntimeError, match="shadow"):
        call(PLCShadow(schedule=SCHEDULE))


# ----------------------------------------------------------------------------------------------- virtual state
def test_shadow_applies_decisions_to_the_virtual_state():
    plc = PLCShadow(initial_state="ColdMode1", schedule=SCHEDULE)
    estado, comb = plc.get_system_state()
    assert estado == "ColdMode1"
    assert plc.get_current_mode() == "cold"

    plc.final_write_to_plc_nn_mode(plc.decide_next_state_from_nn("si", estado), comb)
    assert plc.get_system_state()[0] == "HeatMode1"

    plc.final_write_to_plc_nn_mode(plc.decide_next_state_from_nn("no", "HeatMode1"), comb)
    assert plc.get_system_state()[0] == "HeatMode1"

    plc.final_write_to_plc_nn_mode(plc.decide_next_state_from_nn("parada", "HeatMode1"), comb)
    assert plc.get_system_state()[0] == "Parada"


@pytest.mark.parametrize("when, expected", [
    (real_datetime.datetime(2026, 10, 20, 9, 30), "HeatMode1"),   # Tuesday 09:30: one-digit hour in the window
    (real_datetime.datetime(2026, 10, 20, 15, 0), "Parada"),      # between heat_end and parada_end
    (real_datetime.datetime(2026, 10, 20, 22, 0), "ColdMode1"),   # night
    (real_datetime.datetime(2026, 10, 24, 22, 0), "Parada"),      # Saturday
])
def test_shadow_schedule_comes_from_config(monkeypatch, when, expected):
    fake_now(monkeypatch, when)
    plc = PLCShadow(initial_state="Parada", schedule=SCHEDULE)
    assert plc.decide_next_state_from_nn("si", "Parada") == expected


# ----------------------------------------------------------------------------------------------- outputs per state
@pytest.mark.parametrize("state, mode, cover, pumps", [
    ("ColdMode1", "cold", "open", ("auto", "on", "on", "off")),
    ("HeatMode1", "hot", "closed", ("auto", "off", "off", "on")),
    ("Parada", "parada", "closed", ("off", "off", "off", "on")),
    ("AutomaticMode", "automatic", "plc", ("auto", "auto", "auto", "auto")),
    (None, "unknown", "unknown", ("", "", "", "")),
])
def test_state_outputs(state, mode, cover, pumps):
    out = cycle_log.state_outputs(state)
    assert out["plc_mode"] == mode
    assert out["cover"] == cover
    assert (out["pump_b1"], out["pump_b2"], out["ev1"], out["ev2"]) == pumps


def test_cold_mode_keeps_the_cover_closed_with_a_rain_or_wind_alarm():
    assert cycle_log.state_outputs("ColdMode1", alarm=True)["cover"] == "closed"


def test_state_columns_follow_the_header():
    cols = cycle_log.state_columns("shadow", "Parada", "ColdMode1")
    assert len(cols) == len(cycle_log.STATE_HEADER)
    row = dict(zip(cycle_log.STATE_HEADER, cols))
    assert row == {
        "execution": "shadow", "plc_state_before": "Parada", "plc_state": "ColdMode1",
        "plc_mode": "cold", "plc_mode_code": 2, "cover": "open", "cover_open": 1, "cover_measured": "",
        "pump_b1": "auto", "pump_b2": "on", "ev1": "on", "ev2": "off", "plc_alarm": 0,
        "cop": "", "frame_prod_kwh": "", "control_cfg": "",
    }


def test_state_columns_record_the_alarm():
    row = dict(zip(cycle_log.STATE_HEADER, cycle_log.state_columns("real", "ColdMode1", "ColdMode1", alarm=True)))
    assert row["plc_alarm"] == 1 and row["cover"] == "closed"


# ----------------------------------------------------------------------------------------------- dades.csv
def read_rows(path):
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.reader(f))


def test_write_cycle_row_creates_the_file_and_appends(tmp_path):
    out = tmp_path / "dades.csv"
    decision = ["2026-10-20 10:00:00", True, 0.0, "si", "cold", 1, 2, 3, 4]
    cycle_log.write_cycle_row(out, decision, cycle_log.state_columns("shadow", "Parada", "ColdMode1"))
    cycle_log.write_cycle_row(out, decision, cycle_log.state_columns("shadow", "ColdMode1", "ColdMode1"))
    rows = read_rows(out)
    assert rows[0] == cycle_log.HEADER
    assert len(rows) == 3 and len(rows[1]) == len(cycle_log.HEADER)


def test_write_cycle_row_keeps_an_old_format_file_apart(tmp_path):
    out = tmp_path / "dades.csv"
    out.write_text(",".join(cycle_log.DECISION_HEADER) + "\n2025-12-10 10:00:00,False,0,no,hot,0,0,0,0\n",
                   encoding="utf-8")
    cycle_log.write_cycle_row(out, ["2026-10-20 10:00:00"] + [""] * 8,
                              cycle_log.state_columns("shadow", "Parada", "Parada"))
    assert read_rows(out)[0] == cycle_log.HEADER
    old = [p for p in tmp_path.iterdir() if p.name != "dades.csv"]
    assert len(old) == 1 and old[0].name.startswith("dades_formato_anterior_")
    assert read_rows(old[0])[0] == cycle_log.DECISION_HEADER


def test_write_cycle_row_without_a_decision_still_logs_the_state(tmp_path):
    out = tmp_path / "dades.csv"
    cycle_log.write_cycle_row(out, None, cycle_log.state_columns("shadow", "Parada", "Parada"),
                              now=real_datetime.datetime(2026, 10, 20, 10, 0))
    row = dict(zip(cycle_log.HEADER, read_rows(out)[1]))
    assert row["timestamp"] == "2026-10-20 10:00:00" and row["action"] == "" and row["cover"] == "closed"


# ----------------------------------------------------------------------------------------------- factory, config
def test_make_plc_selects_by_mode():
    assert isinstance(make_plc({"mode": "shadow", "shadow": {"schedule": SCHEDULE}}), PLCShadow)
    real = make_plc({"mode": "real", "ip": "10.0.0.9", "rack": 0, "slot": 1})
    assert type(real) is PLCController and real.ip == "10.0.0.9" and real.client is None
    with pytest.raises(ValueError):
        make_plc({"mode": "maybe"})


def test_shadow_is_the_default_mode_and_disables_email(tmp_path, monkeypatch):
    import sc.config as config
    raw = json.loads((config.BASE_DIR / "config.test.json").read_text(encoding="utf-8"))
    raw["plc"].pop("mode", None)
    raw["smtp"]["enabled"] = True
    cfg = tmp_path / "config.json"
    cfg.write_text(json.dumps(raw), encoding="utf-8")
    monkeypatch.setenv("SC_CONFIG", str(cfg))
    try:
        importlib.reload(config)
        assert config.PLC_MODE == "shadow"
        assert config.SMTP_ENABLED is False
        assert config.LECT_DIR == raw["sc"]["lect_dir"]
    finally:
        monkeypatch.delenv("SC_CONFIG")
        importlib.reload(config)


def test_read_data_paths_come_from_config():
    import sc.config as config
    from sc.utils import read_data as rd
    assert (rd.lect_dir, rd.solar_dir, rd.ir_dir) == (config.LECT_DIR, config.SOLAR_DIR, config.IR_DIR)


# ----------------------------------------------------------------------------------------------- real mode
@pytest.mark.parametrize("fc_open, fc_closed, expected", [
    (True, False, "open"), (False, True, "closed"), (False, False, "moving"), (True, True, "error"),
])
def test_real_controller_reads_the_cover_from_the_limit_switches(monkeypatch, fc_open, fc_closed, expected):
    plc = PLCController(ip="10.0.0.9")
    bits = {(10, 0, 0): fc_open, (10, 0, 1): fc_closed}
    monkeypatch.setattr(plc, "read_bool", lambda db, byte, bit: bits[(db, byte, bit)])
    assert plc.read_cover_position() == expected


# ----------------------------------------------------------------------------------------------- control cycle
def test_run_cycle_in_shadow_logs_what_would_be_applied(tmp_path, monkeypatch):
    import sc.main as main
    monkeypatch.setattr(main, "get_decision", lambda: {"respuesta": "si"})
    monkeypatch.setattr(main, "last_decision", None)        # no frame info: the original state machine toggles
    monkeypatch.setattr(main, "last_decision_row", ["2026-10-20 10:00:00", True, 0.0, "si", "hot", 1, 2, 3, 4])
    out = tmp_path / "dades.csv"
    plc = PLCShadow(initial_state="ColdMode1", schedule=SCHEDULE)

    main.run_cycle(plc, out, state_dir=tmp_path)

    row = dict(zip(cycle_log.HEADER, read_rows(out)[1]))
    assert row["execution"] == "shadow"
    assert (row["plc_state_before"], row["plc_state"]) == ("ColdMode1", "HeatMode1")
    assert (row["cover"], row["cover_open"]) == ("closed", "0")
    assert plc.get_system_state()[0] == "HeatMode1"


# ----------------------------------------------------------------------------------------------- panel orders between cycles
def test_panel_orders_apply_between_cycles_without_waiting_for_the_next_one(tmp_path, monkeypatch):
    """The schedule and the dry-run «apply» set a virtual state in control.json: the control applies it within seconds
    and updates status.json (state, cover, pumps, pause), keeping the times of its last cycle."""
    import sc.main as main
    from sc import state_io
    monkeypatch.setattr(main, "_last_set_state_seq", 0)
    plc = PLCShadow(initial_state="Parada", schedule=SCHEDULE)
    state_io.write_status(tmp_path, {"cycle_time": "2026-10-22 21:00:00", "next_cycle_time": "2026-10-22 21:15:00",
                                     "plc_state": "Parada", "plc_mode": "parada", "cover": "closed", "paused": False})
    assert main.apply_panel_orders(plc, tmp_path) is False                 # nothing new
    state_io.write_control(tmp_path, set_state="ColdMode1", set_state_seq=1, paused=True, paused_by="agenda")
    assert main.apply_panel_orders(plc, tmp_path) is True
    assert plc.get_system_state()[0] == "ColdMode1"
    st = state_io.read_status(tmp_path)
    assert (st["plc_state"], st["plc_mode"], st["cover"], st["paused"], st["paused_by"]) == ("ColdMode1", "cold", "open", True, "agenda")
    assert st["pumps"]["b2"] == "on" and st["next_cycle_time"] == "2026-10-22 21:15:00"
    assert main.apply_panel_orders(plc, tmp_path) is False                 # applied once per sequence number


def test_panel_orders_are_ignored_in_real_mode(tmp_path, monkeypatch):
    import sc.main as main
    from sc import state_io
    monkeypatch.setattr(main, "_last_set_state_seq", 0)
    plc = PLCShadow(initial_state="Parada", schedule=SCHEDULE)
    monkeypatch.setattr(plc, "execution", "real")
    state_io.write_control(tmp_path, set_state="ColdMode1", set_state_seq=5)
    assert main.apply_panel_orders(plc, tmp_path) is False and plc.state == "Parada"
