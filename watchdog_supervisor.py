# watchdog_supervisor.py
import time
import subprocess
import logging

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)

def lanzar_main():
    logging.info("Lanzando sc.main ...")
    # Ajusta el comando según cómo lo ejecutes normalmente
    return subprocess.Popen(["python", "-m", "sc.main"])

def main():
    while True:
        proc = lanzar_main()
        # Esperamos a que termine
        ret = proc.wait()
        logging.error(f"sc.main ha terminado con código {ret}. Reiniciando en 5s...")
        time.sleep(5)

if __name__ == "__main__":
    main()
