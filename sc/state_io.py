"""Shared state between the control loop (sc.main) and the panel, as small JSON files in state/.

- status.json: written by the control loop at the end of every cycle (what it read, forecast, plan, decision, PLC state).
- control.json: written by the panel; read by the control loop at the start of every cycle
  (paused: decide but do not apply; set_state: shadow mode only, set the virtual PLC state once per set_state_seq).
Writes are atomic (temporary file + os.replace) so a reader never sees a half-written file.
"""
import json
import os
import tempfile
from pathlib import Path

STATE_DIR = Path(__file__).resolve().parent.parent / "state"
STATUS_NAME = "status.json"
CONTROL_NAME = "control.json"

CONTROL_DEFAULTS = {"paused": False, "paused_by": "", "since": "", "set_state": None, "set_state_seq": 0}


def write_json_atomic(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=path.stem + "-", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=1, default=str)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def read_json(path, default=None):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def read_control(state_dir=STATE_DIR):
    data = read_json(Path(state_dir) / CONTROL_NAME, {}) or {}
    return {**CONTROL_DEFAULTS, **{k: v for k, v in data.items() if k in CONTROL_DEFAULTS}}


def write_control(state_dir=STATE_DIR, **fields):
    current = read_control(state_dir)
    current.update(fields)
    write_json_atomic(Path(state_dir) / CONTROL_NAME, current)
    return current


def write_status(state_dir, status):
    write_json_atomic(Path(state_dir) / STATUS_NAME, status)


def read_status(state_dir=STATE_DIR):
    return read_json(Path(state_dir) / STATUS_NAME, None)
