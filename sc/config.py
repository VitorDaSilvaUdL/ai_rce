import os
import json
from pathlib import Path

# Carpeta donde está este fichero
BASE_DIR = Path(__file__).resolve().parent
PROJECT_DIR = BASE_DIR.parent

# SC_ENV puede ser "prod" o "test"; SC_CONFIG (ruta a un .json) tiene prioridad
ENV = os.getenv("SC_ENV", "test")

CONFIG_FILE = Path(os.getenv("SC_CONFIG")) if os.getenv("SC_CONFIG") else BASE_DIR / f"config.{ENV}.json"

if not CONFIG_FILE.exists():
    raise FileNotFoundError(f"No se ha encontrado el fichero de configuración: {CONFIG_FILE}")

with CONFIG_FILE.open("r", encoding="utf-8") as f:
    _raw = json.load(f)

PREDICT_URL = _raw["predict_url"]

# PLC: plc.mode = "shadow" (por defecto: no se conecta ni escribe en el PLC) o "real"
PLC_CONFIG = _raw["plc"]
PLC_MODE = PLC_CONFIG.get("mode", "shadow")
PLC_IP = PLC_CONFIG["ip"]
PLC_RACK = PLC_CONFIG["rack"]
PLC_SLOT = PLC_CONFIG["slot"]

# CSV de WinCC que lee el sistema
LECT_DIR = _raw["sc"]["lect_dir"]
SOLAR_DIR = _raw["sc"]["solar_dir"]
IR_DIR = _raw["sc"]["ir_dir"]

P_BOMBA_WATTS = _raw["pump"]["power_watts"]
MIN_TIME_STEP = _raw["pump"]["min_time_step_minutes"]

# dades.csv: ruta relativa a la carpeta del proyecto, o absoluta
OUTPUT_CSV = _raw["paths"]["output_csv"]
OUTPUT_CSV_PATH = Path(OUTPUT_CSV) if Path(OUTPUT_CSV).is_absolute() else PROJECT_DIR / OUTPUT_CSV

TEST_MODE = _raw.get("test_mode", False)
LOG_LEVEL = _raw.get("log_level", "INFO")
ITER_TIME_SEC = _raw.get("iteration_time_in_minutes", "15")

SMTP_CONFIG = _raw.get("smtp", {})

# En modo sombra no se envían correos (los avisos hablarían de cambios que no se aplican),
# salvo que se pida con smtp.enabled_in_shadow
SMTP_ENABLED = bool(SMTP_CONFIG.get("enabled", False)) and (
    PLC_MODE == "real" or bool(SMTP_CONFIG.get("enabled_in_shadow", False)))
SMTP_FROM = SMTP_CONFIG.get("from")
SMTP_TO = SMTP_CONFIG.get("to")  # puede ser string o lista
SMTP_USER = SMTP_CONFIG.get("user")
SMTP_PASSWORD = SMTP_CONFIG.get("password")
SMTP_SUBJECT = SMTP_CONFIG.get("subject", "ALERTA Sistema RCE")
SMTP_MIN_LEVEL = SMTP_CONFIG.get("min_level", "WARNING")
