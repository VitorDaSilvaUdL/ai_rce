"""Point sc/config.prod.json at the WinCC CSV folder of the laboratory PC and force shadow mode.

    python deploy/configure_lab.py <wincc_dir>

Writes sc.lect_dir/solar_dir/ir_dir, plc.mode = "shadow", paths.output_csv = "dades.csv" and prints the result.
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG = os.path.join(HERE, "..", "sc", "config.prod.json")


def main(wincc_dir):
    with open(CONFIG, encoding="utf-8") as f:
        cfg = json.load(f)
    cfg["sc"]["lect_dir"] = os.path.join(wincc_dir, "Lecturas0.csv")
    cfg["sc"]["solar_dir"] = os.path.join(wincc_dir, "Solarimeter0.csv")
    cfg["sc"]["ir_dir"] = os.path.join(wincc_dir, "Pyrgeometer0.csv")
    cfg["plc"]["mode"] = "shadow"
    cfg["plc"].setdefault("shadow", {"initial_state": "Parada"})
    cfg["paths"]["output_csv"] = "dades.csv"
    with open(CONFIG, "w", encoding="utf-8", newline="\n") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)
    print(f"plc.mode={cfg['plc']['mode']} lect_dir={cfg['sc']['lect_dir']} output_csv={cfg['paths']['output_csv']}")


if __name__ == "__main__":
    main(sys.argv[1])
