# watchdog.py  (versión extendida con verificación de la API)
import time
import os
from datetime import datetime, timedelta
import subprocess
import urllib.request
import urllib.error

from sc.logger import get_logger

HEARTBEAT_FILE = "heartbeat_sc.txt"
CHECK_INTERVAL_SEC = 60         # comprobar cada minuto
# TIMEOUT_SC_SEC = 900            # 15 minutos sin latido = SC colgado
TIMEOUT_SC_SEC = 10            # 10 seg sin latido = SC colgado TEST
API_URL = "http://localhost:8000/health"   # endpoint de salud de la API
API_TIMEOUT_SEC = 10             # segundos máximos para respuesta API

logger = get_logger(__name__)


# -------------------------------
#  SC HEARTBEAT CHECK
# -------------------------------
def get_last_heartbeat():
    if not os.path.exists(HEARTBEAT_FILE):
        return None
    try:
        text = open(HEARTBEAT_FILE, "r", encoding="utf-8").read().strip()
        return datetime.fromisoformat(text)
    except Exception:
        logger.exception("Error leyendo heartbeat")
        return None


# -------------------------------
#  API HEALTH CHECK
# -------------------------------
def check_api_alive():
    """
    Llama al endpoint /health de la API.
    Si no responde correctamente, devuelve False.
    """
    try:
        with urllib.request.urlopen(API_URL, timeout=API_TIMEOUT_SEC) as resp:
            if resp.status == 200:
                return True
            else:
                logger.warning(f"API respondió con código HTTP {resp.status}")
                return False
    except urllib.error.URLError as e:
        logger.error(f"API no responde: {e}")
        return False
    except Exception as e:
        logger.exception(f"Error desconocido al verificar API: {e}")
        return False


# -------------------------------
#  MAIN WATCHDOG LOOP
# -------------------------------
def main():
    logger.info("Watchdog iniciado (SC + API).")

    while True:

        # ------------ 1. CHECK HEARTBEAT SC ------------
        hb = get_last_heartbeat()

        if hb is None:
            logger.warning("No se ha encontrado heartbeat. ¿sc.main está arrancando?")
        else:
            age = datetime.now() - hb
            secs = age.total_seconds()
            logger.info(f"Último heartbeat SC hace {secs:.1f} s")

            if secs > TIMEOUT_SC_SEC:
                logger.error(
                    f"SC colgado: heartbeat demasiado antiguo ({secs:.1f} s)."
                )
                # OPCIONAL → Reiniciar SC automáticamente:
                # subprocess.Popen(["taskkill", "/IM", "python.exe", "/F"])
                # subprocess.Popen(["python", "-m", "sc.main"])

        # ------------ 2. CHECK API ------------
        api_ok = check_api_alive()

        if not api_ok:
            logger.error("La API no está respondiendo al health check.")

            # OPCIONAL → reiniciar API automáticamente:
            # subprocess.Popen(["taskkill", "/IM", "python.exe", "/F"])
            # subprocess.Popen(["python", "-m", "api.main"])

        time.sleep(CHECK_INTERVAL_SEC)


if __name__ == "__main__":
    main()