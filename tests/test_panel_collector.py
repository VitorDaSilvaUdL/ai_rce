"""K0 columns of dades.csv (cop, frame production, control version) and the panel collector (K1/K2/K3 writers)."""
import json
from datetime import datetime, timedelta

from sc import cycle_log, state_io
from panel import collector, journal, metrics, trials
from panel.wincc import Reading

T0 = datetime(2026, 10, 21, 22, 0)


# ----------------------------------------------------------------------------------------------- K0
def test_state_columns_carry_cop_production_and_control_version():
    cols = cycle_log.state_columns("shadow", "Parada", "ColdMode1", cop=55.6, frame_prod_j=5e5, control_cfg="fixed")
    row = dict(zip(cycle_log.STATE_HEADER, cols))
    assert row["cop"] == 55.6 and row["frame_prod_kwh"] == round(5e5 / 3.6e6, 4) and row["control_cfg"] == "fixed"
    row2 = dict(zip(cycle_log.STATE_HEADER, cycle_log.state_columns("shadow", "Parada", "Parada")))
    assert row2["cop"] == "" and row2["frame_prod_kwh"] == "" and row2["control_cfg"] == ""


def test_control_cfg_tag():
    assert cycle_log.control_cfg_tag("forecast", "frame_mode", "frame_prod", "J") == "fixed"
    assert cycle_log.control_cfg_tag("iso", "toggle", "pump_wh", "Wh", "RCEb") == "legacy"
    assert cycle_log.control_cfg_tag("forecast", "toggle", "frame_prod", "J") == "fk=forecast;ns=toggle;da=frame_prod;cop=J;ir=RCEa"
    # the infrared input of the model: unit A (single pyrgeometer) is part of the corrected control
    assert cycle_log.control_cfg_tag("forecast", "frame_mode", "frame_prod", "J", "RCEb") == "fk=forecast;ns=frame_mode;da=frame_prod;cop=J;ir=RCEb"


def test_run_cycle_writes_cop_and_control_version(tmp_path, monkeypatch):
    import sc.main as main
    from sc import decision as dec
    from sc.plc_shadow import PLCShadow
    monkeypatch.setattr(main, "get_decision", lambda: {"respuesta": "si"})
    d = dec.decide(T0 + timedelta(minutes=7), {T0: 0}, {"hot": {}, "cold": {"2026-10-21 22:00": 5e5}}, 6e6, 7e6, rng=lambda a, b: 1.0)
    monkeypatch.setattr(main, "last_decision", d)
    monkeypatch.setattr(main, "last_decision_row", [T0, True, 6e6, "si", "cold", 0, 5e5, 0, 0])
    monkeypatch.setattr(main, "system_data", {"data": []})
    monkeypatch.setattr(main, "_last_set_state_seq", 0)
    plc = PLCShadow(initial_state="Parada", schedule={"heat_start": "08:00:00", "heat_end": "14:00:00", "parada_end": "16:00:00"})
    main.run_cycle(plc, tmp_path / "dades.csv", state_dir=tmp_path)
    row = (tmp_path / "dades.csv").read_text(encoding="utf-8").splitlines()[1].split(",")
    r = dict(zip(cycle_log.HEADER, row))
    assert float(r["cop"]) == round(5e5 / 9000, 2) and r["frame_prod_kwh"] == "0.1389" and r["control_cfg"] == "fixed"
    assert r["plc_state"] == "ColdMode1"


# ----------------------------------------------------------------------------------------------- collector
def mk(ts, var, value, system="RCE-a"):
    return Reading(ts=ts, tag=var, system=system, variable=var, value=value, valid=True)


def readings(hours=2):
    out, t = [], T0
    while t < T0 + timedelta(hours=hours):
        out += [mk(t, "flow_rce_l_min", 3.0), mk(t, "t_circuit_2_c", 12.0), mk(t, "t_circuit_4_c", 10.0),
                mk(t, "t_cold_tank_c", 14.0), mk(t, "t_hot_tank_c", 40.0), mk(t, "t_ambient_c", 9.0),
                mk(t, "ir_net_w_m2", -70.0, "RCE-b")]
        t += timedelta(minutes=5)
    return out


def test_collector_writes_metrics_trials_and_plc_probe(tmp_path):
    state = tmp_path / "state"
    state.mkdir()
    state_io.write_status(state, {"written_at": "2026-10-21 23:00:00", "execution": "real", "plc_mode": "cold",
                                  "cover": "open", "cover_measured": "open"})
    diary = journal.Diary(state / "diario.csv")
    diary.add(author="AC", kind="ensayo_inicio", text="t2", now=T0, operation="control_ia")
    probes = [{"ok": True, "ts": "2026-10-21 23:00:00", "state": "ColdMode1", "plc_mode": "cold", "cover": "open",
               "alarms": {"lluvia": False, "viento": True}, "pumps": {"b1": "auto", "b2": "on", "ev1": "on", "ev2": "off"}}]
    c = collector.Collector(state_dir=state, readings=lambda hours: readings(), decisions=lambda since: [],
                            diary=diary, mode=lambda: "real", probe=lambda: probes[0])
    n = c.run_once(now=T0 + timedelta(hours=1, minutes=3))
    assert n["metrics"] == 12 and n["trials"] == 1 and n["plc"] == 1
    m = metrics.MetricsLog(state / "metrics.csv").rows()
    assert m[-1]["trial_id"] == "E20261021-2200" and m[-1]["operation"] == "control_ia" and m[-1]["cover"] == "open"
    assert m[-1]["execution"] == "real" and m[-1]["cover_measured"] == "open"
    t = trials.TrialsLog(state / "ensayos.csv").rows()
    assert t[-1]["estado"] == "en_curso" and t[-1]["operacion"] == "control_ia"
    p = collector.ProbeLog(state / "plc_probe.csv").rows()
    assert p[-1]["state"] == "ColdMode1" and p[-1]["alarm_viento"] == "1" and p[-1]["pump_b2"] == "on"
    # second run a minute later: no new metrics interval, trial not refreshed yet, probe unchanged -> not repeated
    n2 = c.run_once(now=T0 + timedelta(hours=1, minutes=4))
    assert n2 == {"metrics": 0, "trials": 0, "plc": 0}
    probes[0] = {**probes[0], "cover": "closed", "ts": "2026-10-21 23:04:00"}
    assert c.run_once(now=T0 + timedelta(hours=1, minutes=4, seconds=30))["plc"] == 1


def test_collector_in_shadow_does_not_probe_and_survives_errors(tmp_path):
    state = tmp_path / "state"
    state.mkdir()
    diary = journal.Diary(state / "diario.csv")
    def boom(hours):
        raise RuntimeError("disk")
    c = collector.Collector(state_dir=state, readings=boom, decisions=lambda since: [], diary=diary,
                            mode=lambda: "shadow", probe=lambda: {"ok": False})
    n = c.run_once(now=T0)
    assert n["metrics"] == 0 and n["plc"] == 0 and c.last_error
