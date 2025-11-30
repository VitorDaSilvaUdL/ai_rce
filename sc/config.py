import os
import json
from pathlib import Path

# Carpeta donde está este fichero
BASE_DIR = Path(__file__).resolve().parent

# SC_ENV puede ser "prod" o "test"
ENV = os.getenv("SC_ENV", "test")  # por defecto prod

CONFIG_FILE = BASE_DIR / f"config.{ENV}.json"

if not CONFIG_FILE.exists():
    raise FileNotFoundError(f"No se ha encontrado el fichero de configuración: {CONFIG_FILE}")

with CONFIG_FILE.open("r", encoding="utf-8") as f:
    _raw = json.load(f)

PREDICT_URL = _raw["predict_url"]

PLC_IP = _raw["plc"]["ip"]
PLC_RACK = _raw["plc"]["rack"]
PLC_SLOT = _raw["plc"]["slot"]

P_BOMBA_WATTS = _raw["pump"]["power_watts"]
MIN_TIME_STEP = _raw["pump"]["min_time_step_minutes"]

OUTPUT_CSV = _raw["paths"]["output_csv"]

TEST_MODE = _raw.get("test_mode", False)
LOG_LEVEL = _raw.get("log_level", "INFO")
ITER_TIME_SEC = _raw.get("iteration_time_in_minutes", "15") 