# watchdog.py
import time
import os
from datetime import datetime, timedelta
import subprocess
from sc.logger import get_logger

HEARTBEAT_FILE = "heartbeat_sc.txt"
CHECK_INTERVAL_SEC = 3600        # cada cuánto comprobar
TIMEOUT_SEC = 30               # si en 30s no hay latido → problema

logger = get_logger(__name__)

def get_last_heartbeat():
    if not os.path.exists(HEARTBEAT_FILE):
        return None
    try:
        text = open(HEARTBEAT_FILE, "r", encoding="utf-8").read().strip()
        return datetime.fromisoformat(text)
    except Exception:
        logger.exception("Error leyendo heartbeat")
        return None

def main():
    logger.info("Watchdog iniciado.")

    # OPCIONAL: lanzar main aquí
    # process = subprocess.Popen(["python", "-m", "sc.main"])

    while True:
        hb = get_last_heartbeat()

        if hb is None:
            logger.warning("No hay heartbeat. ¿main se ha iniciado ya?")
        else:
            age = datetime.now() - hb
            logger.info(f"Último latido hace {age.total_seconds():.1f} s")

            if age > timedelta(seconds=TIMEOUT_SEC):
                logger.error(f"¡Heartbeat demasiado antiguo! main puede estar colgado o muerto. Último latido hace {age.total_seconds():.1f} s")
          
        time.sleep(CHECK_INTERVAL_SEC)

if __name__ == "__main__":
    main()
