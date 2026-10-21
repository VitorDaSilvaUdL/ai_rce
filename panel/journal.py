"""Campaign diary (state/diario.csv) and action log (state/acciones.csv): append-only CSV files with author and time.
Filebeat ships both to Kibana."""
import csv
from datetime import datetime
from pathlib import Path

DIARY_HEADER = ["timestamp", "autor", "tipo", "texto", "ensayo_id", "host"]
ACTIONS_HEADER = ["timestamp", "autor", "accion", "modo", "resultado", "duracion_s", "host"]
DIARY_KINDS = {"ensayo_inicio": "Inicio de ensayo", "ensayo_fin": "Fin de ensayo", "incidencia": "Incidencia",
               "nota": "Nota", "mando": "Mando manual", "modo": "Cambio de modo", "sistema": "Sistema"}


def _stamp(now):
    return (now or datetime.now()).strftime("%Y-%m-%d %H:%M:%S")


class CsvLog:
    def __init__(self, path, header):
        self.path = Path(path)
        self.header = header

    def _append(self, row):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        new = not self.path.exists() or self.path.stat().st_size == 0
        with open(self.path, "a", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=self.header)
            if new:
                w.writeheader()
            w.writerow(row)
        return row

    def rows(self):
        if not self.path.exists():
            return []
        with open(self.path, newline="", encoding="utf-8") as f:
            return [r for r in csv.DictReader(f)]

    def last(self, n=50):
        return self.rows()[-n:]


class Diary(CsvLog):
    def __init__(self, path):
        super().__init__(path, DIARY_HEADER)

    def open_trial(self):
        """Id of the trial started and not yet finished, or None."""
        current = None
        for r in self.rows():
            if r["tipo"] == "ensayo_inicio":
                current = r["ensayo_id"]
            elif r["tipo"] == "ensayo_fin":
                current = None
        return current

    def add(self, author, kind, text, now=None, host=""):
        now = now or datetime.now()
        if kind == "ensayo_inicio":
            trial = f"E{now:%Y%m%d-%H%M}"
        else:
            trial = self.open_trial() or ""
        return self._append({"timestamp": _stamp(now), "autor": author, "tipo": kind, "texto": text,
                             "ensayo_id": trial, "host": host})


class Actions(CsvLog):
    def __init__(self, path):
        super().__init__(path, ACTIONS_HEADER)

    def add(self, author, action, mode, result, duration_s=0.0, now=None, host=""):
        return self._append({"timestamp": _stamp(now), "autor": author, "accion": action, "modo": mode,
                             "resultado": result, "duracion_s": f"{duration_s:g}", "host": host})
