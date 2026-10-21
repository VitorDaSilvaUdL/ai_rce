"""One row per control cycle in dades.csv: the ai_rce decision plus the PLC state it leads to.

The state columns say, for every cycle and in both execution modes, which operating mode the RCE is in and
what that implies for pumps, valves and the cover (horizontal lid), so dashboards can plot them:

- execution = "real": state applied to the PLC; cover_measured comes from the limit switches (DB10).
- execution = "shadow": nothing is written to the PLC; the state is what ai_rce would have applied.

Outputs per state follow the write sequences of plc_controller.py: set_cold_mode opens the cover
(_sequence_obrir, blocked by a rain/wind alarm), set_heat_mode and set_parada_mode close it
(_sequence_tancar), set_automatic_mode hands every actuator to the PLC program.
"""
import csv
import os
from datetime import datetime
from pathlib import Path

from sc.plc_controller import SystemCase

DECISION_HEADER = [
    "timestamp", "in_time_frame", "current_dem_target",
    "action", "mode", "hot_prod", "cold_prod", "hot_dem", "cold_dem",
]
STATE_HEADER = [
    "execution", "plc_state_before", "plc_state", "plc_mode", "plc_mode_code",
    "cover", "cover_open", "cover_measured", "pump_b1", "pump_b2", "ev1", "ev2", "plc_alarm",
    "cop", "frame_prod_kwh", "control_cfg",
]
HEADER = DECISION_HEADER + STATE_HEADER

MODE_OF_STATE = {
    "ColdMode1": "cold", "ColdMode2": "cold",
    "HeatMode1": "hot", "HeatMode2": "hot",
    "Parada": "parada", "AutomaticMode": "automatic",
}
MODE_CODE = {"parada": 0, "hot": 1, "cold": 2, "automatic": 3}     # numeric, for step plots
COVER_OF_MODE = {"cold": "open", "hot": "closed", "parada": "closed", "automatic": "plc"}
ACTUATOR_LABEL = {"A": "auto", "MM": "on", "MP": "off"}


def combination_of(state):
    """First actuator combination (B1, B2, EV1, EV2, TH, TV) that the PLC maps to this state."""
    for combination, name in SystemCase.items():
        if name == state:
            return combination
    return None


def state_outputs(state, alarm=False):
    """Mode, cover and pump/valve states implied by a logical PLC state."""
    mode = MODE_OF_STATE.get(state, "unknown")
    cover = COVER_OF_MODE.get(mode, "unknown")
    if alarm and cover == "open":
        cover = "closed"        # _sequence_obrir refuses to open with a rain/wind alarm
    combination = combination_of(state)
    b1, b2, ev1, ev2 = (ACTUATOR_LABEL[c] for c in combination[:4]) if combination else ("", "", "", "")
    return {
        "plc_mode": mode,
        "plc_mode_code": MODE_CODE.get(mode, ""),
        "cover": cover,
        "cover_open": {"open": 1, "closed": 0}.get(cover, ""),
        "pump_b1": b1, "pump_b2": b2, "ev1": ev1, "ev2": ev2,
    }


def control_cfg_tag(frame_key_format, next_state_policy, demand_adjust, cop_units):
    """Short tag of the control version that produced a row: 'fixed' (corrections of 2026-10-21), 'legacy'
    (campaign 2025 behaviour) or the explicit mix."""
    cfg = (frame_key_format, next_state_policy, demand_adjust, cop_units)
    if cfg == ("forecast", "frame_mode", "frame_prod", "J"):
        return "fixed"
    if cfg == ("iso", "toggle", "pump_wh", "Wh"):
        return "legacy"
    return f"fk={frame_key_format};ns={next_state_policy};da={demand_adjust};cop={cop_units}"


def state_columns(execution, state_before, state, cover_measured="", alarm=False, cop=None, frame_prod_j=None,
                  control_cfg=""):
    """The STATE_HEADER columns for one cycle."""
    out = state_outputs(state, alarm=alarm)
    values = {
        "execution": execution,
        "plc_state_before": state_before if state_before is not None else "",
        "plc_state": state if state is not None else "",
        "cover_measured": cover_measured or "",
        "plc_alarm": 1 if alarm else 0,
        "cop": round(cop, 2) if cop is not None else "",
        "frame_prod_kwh": round(frame_prod_j / 3.6e6, 4) if frame_prod_j is not None else "",
        "control_cfg": control_cfg or "",
        **out,
    }
    return [values[k] for k in STATE_HEADER]


def write_cycle_row(path, decision_row, state_cols, now=None):
    """Append one cycle to dades.csv (header on a new file).

    decision_row: the 9 DECISION_HEADER values from get_decision(), or None if the decision failed
    (the state is logged anyway). A file with another header is kept apart, renamed
    dades_formato_anterior_<timestamp>.csv, so a file never mixes formats.
    """
    path = Path(path)
    if decision_row is None:
        decision_row = [(now or datetime.now()).replace(microsecond=0)] + [""] * (len(DECISION_HEADER) - 1)
    decision_row = [v.strftime("%Y-%m-%d %H:%M:%S") if isinstance(v, datetime) else v for v in decision_row]

    if path.exists() and path.stat().st_size > 0:
        with open(path, newline="", encoding="utf-8") as f:
            first = next(csv.reader(f), [])
        if first != HEADER:
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            os.replace(path, path.with_name(f"{path.stem}_formato_anterior_{stamp}{path.suffix}"))

    new_file = not path.exists() or path.stat().st_size == 0
    with open(path, mode="a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if new_file:
            w.writerow(HEADER)
        w.writerow(list(decision_row) + list(state_cols))
