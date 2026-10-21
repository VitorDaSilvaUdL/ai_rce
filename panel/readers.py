"""Readers of the control's own files and services for the panel: status.json, heartbeat, dades.csv, sc.log,
Windows services, disk, API health."""
import csv
import io
import re
import shutil
import subprocess
import urllib.request
from datetime import datetime
from pathlib import Path

from sc import state_io
from sc.cycle_log import HEADER, DECISION_HEADER, STATE_HEADER

TS_FORMATS = ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S.%f")


def parse_ts(s):
    s = (s or "").strip()
    for fmt in TS_FORMATS:
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None


# ----------------------------------------------------------------------------------------------- control files
def heartbeat_age(path, now):
    """Seconds since the control loop last wrote its heartbeat, or None."""
    try:
        ts = parse_ts(Path(path).read_text(encoding="utf-8"))
    except OSError:
        return None
    return (now - ts).total_seconds() if ts else None


def read_status(state_dir, now):
    """status.json plus age_s (seconds since written_at), or None."""
    st = state_io.read_status(state_dir)
    if not st:
        return None
    ts = parse_ts(st.get("written_at", ""))
    st["age_s"] = (now - ts).total_seconds() if ts else None
    return st


NUMERIC = {"current_dem_target", "hot_prod", "cold_prod", "hot_dem", "cold_dem"}
INTEGER = {"plc_mode_code", "cover_open"}


def read_decisions(path, since, max_bytes=2_000_000):
    """Rows of dades.csv with ts >= since (tail of the file). Old-format rows get empty state columns."""
    path = Path(path)
    if not path.exists() or path.stat().st_size == 0:
        return []
    with open(path, "rb") as f:
        header_line = f.readline().decode("utf-8", errors="replace")
        size = path.stat().st_size
        if size > max_bytes:
            f.seek(size - max_bytes)
            f.readline()                      # partial line
        body = f.read().decode("utf-8", errors="replace")
    header = next(csv.reader([header_line]), [])
    if header not in (HEADER, DECISION_HEADER):
        return []
    rows = []
    for rec in csv.reader(io.StringIO(body)):
        if not rec or rec[0] == "timestamp":
            continue
        row = dict(zip(header, rec))
        ts = parse_ts(row.get("timestamp"))
        if not ts or ts < since:
            continue
        out = {"ts": ts}
        for k in DECISION_HEADER[1:] + STATE_HEADER:
            v = row.get(k, "")
            if k in NUMERIC or k in INTEGER:
                try:
                    v = int(float(v)) if k in INTEGER else float(v)
                except (TypeError, ValueError):
                    v = None if k in INTEGER else (v or None)
            elif k == "in_time_frame":
                v = {"True": True, "False": False}.get(v, v)
            out[k] = v if v is not None else ("" if k in STATE_HEADER else v)
        rows.append(out)
    return rows


# ----------------------------------------------------------------------------------------------- sc.log
LOG_RE = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})(?:,\d+)? \[(\w+)\]: (.*)$")
STATE_ES = {"ColdMode1": "modo frío", "ColdMode2": "modo frío (2)", "HeatMode1": "modo calor", "HeatMode2": "modo calor (2)",
            "Parada": "parada", "AutomaticMode": "automático", "None": "sin estado"}

# (regex on the message, plain-language text; {g} = groups)
EVENT_PATTERNS = [
    (re.compile(r"Nou cicle de control"), lambda m: "Nuevo ciclo de control"),
    (re.compile(r"SOMBRA: se aplicaría (\w+) \(antes (\w+)\)"),
     lambda m: f"Sombra: habría cambiado a {STATE_ES.get(m.group(1), m.group(1))} (antes {STATE_ES.get(m.group(2), m.group(2))}); no se escribe en el PLC"),
    (re.compile(r"SOMBRA: sin conexión con el PLC"), lambda m: "Sombra: arranque sin conexión con el PLC"),
    (re.compile(r"PANEL: control en pausa \(([^)]*)\); no se aplica (\w+)"),
     lambda m: f"Control en pausa por {m.group(1) or 'el panel'}: no se aplica {STATE_ES.get(m.group(2), m.group(2))}"),
    (re.compile(r"PANEL: estado virtual fijado a (\w+)"), lambda m: f"Estado virtual fijado desde el panel: {STATE_ES.get(m.group(1), m.group(1))}"),
    (re.compile(r"COP < ([\d.]+) o no hi ha demanda"), lambda m: f"Bomba parada: rendimiento previsto por debajo de {m.group(1)} o sin demanda"),
    (re.compile(r"Hora actual fora de franges"), lambda m: "Bomba parada: fuera de las franjas horarias previstas"),
    (re.compile(r"No hi ha demanda objectiu pendent"), lambda m: "Bomba parada: no queda demanda por cubrir"),
    (re.compile(r"Demanda coberta/esgotada"), lambda m: "Demanda cubierta: plan reiniciado y bomba parada"),
    (re.compile(r"No s'han pogut obtenir prediccions \(([^)]*)\)"), lambda m: f"No se han podido obtener las previsiones ({m.group(1)})"),
    (re.compile(r"Prediccions actualitzades"), lambda m: "Previsiones actualizadas"),
    (re.compile(r"CAMBIO DE ESTADO"), lambda m: "Cambio de estado aplicado en el PLC"),
    (re.compile(r"RCE \(SOMBRA\) - la IA cambiaría de estado: (\w+) -> (\w+)"),
     lambda m: f"Sombra: la IA cambiaría de {STATE_ES.get(m.group(1), m.group(1))} a {STATE_ES.get(m.group(2), m.group(2))}"),
    (re.compile(r"Sistema iniciado"), lambda m: "Control arrancado"),
    (re.compile(r"Planificaci[oó]n? inicial de la demanda de (calor|frio)"), lambda m: f"Planificación inicial de la demanda de {'frío' if m.group(1) == 'frio' else 'calor'}"),
    (re.compile(r"Ventana de planificaci[oó]n (calor|frio): (\S+) <-> (\S+)"),
     lambda m: f"Ventana de planificación de {'frío' if m.group(1) == 'frio' else 'calor'}: de {m.group(2)[:16].replace('T', ' ')} a {m.group(3)[:16].replace('T', ' ')}"),
    (re.compile(r"Demanda total calculada para el intervalo: ([\d.]+)"), lambda m: f"Demanda prevista en la ventana: {float(m.group(1)) / 3.6e6:.1f} kWh"),
    (re.compile(r"Mode establert a (\w+)\. Demanda \w+ objectiu: ([\d.,]+) J.*Producció prevista: ([\d.,]+) J"),
     lambda m: f"Plan de {'calor' if m.group(1).upper() == 'HOT' else 'frío'}: demanda objetivo {float(m.group(2).replace(',', '')) / 3.6e6:.1f} kWh, producción prevista {float(m.group(3).replace(',', '')) / 3.6e6:.1f} kWh"),
    (re.compile(r"Franges hor[àa]ries seleccionades per a (\w+): \[(.*)\]"),
     lambda m: f"Franjas elegidas para {'calor' if m.group(1).upper() == 'HOT' else 'frío'}: {m.group(2).count(chr(39)) // 2} tramos"),
    (re.compile(r"Franges horaries seleccionadas per al mode COLD.*: \[(.*)\]"), lambda m: f"Franjas de frío (solo información): {m.group(1).count(chr(39)) // 2} tramos"),
    (re.compile(r"Demanda de frio estimada para el periodo: ([\d.,]+) J"), lambda m: f"Demanda de frío prevista: {float(m.group(1).replace(',', '')) / 3.6e6:.1f} kWh"),
    (re.compile(r"Energia total prevista para cubrir la demanda de frio: ([\d.,]+) J"), lambda m: f"Producción de frío prevista: {float(m.group(1).replace(',', '')) / 3.6e6:.1f} kWh"),
    (re.compile(r"No hay demanda de frio"), lambda m: "No hay demanda de frío en los próximos días"),
    (re.compile(r"No se ha podido encontrar un plan optimo para la demanda de frio"), lambda m: "No se ha encontrado un plan que cubra la demanda de frío"),
    (re.compile(r"Planificant demanda de calor"), lambda m: "Planificando la demanda de calor (7:00)"),
    (re.compile(r"Iniciant actualització diària de prediccions"), lambda m: "Actualización diaria de previsiones (0:00)"),
    (re.compile(r"Modo actual PLC: (\w+)"), lambda m: f"Modo leído al arrancar: {m.group(1)}"),
    (re.compile(r"Error escrivint|Error escribiendo"), lambda m: "Error al escribir un fichero de registro"),
]


def plain_text(msg):
    msg = re.sub(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}: ", "", msg)
    for rx, fn in EVENT_PATTERNS:
        m = rx.search(msg)
        if m:
            return fn(m)
    return msg


def tail_lines(path, max_lines=400, max_bytes=400_000):
    path = Path(path)
    if not path.exists():
        return []
    size = path.stat().st_size
    with open(path, "rb") as f:
        if size > max_bytes:
            f.seek(size - max_bytes)
            f.readline()
        text = f.read().decode("utf-8", errors="replace")
    return text.splitlines()[-max_lines:]


def read_events(path, max_lines=200, levels=("INFO", "WARNING", "ERROR", "CRITICAL")):
    """Last log lines as events: ts, level, text in plain language (unparseable lines are skipped)."""
    events = []
    for line in tail_lines(path, max_lines=max_lines * 3):
        m = LOG_RE.match(line)
        if not m or m.group(2) not in levels:
            continue
        events.append({"ts": parse_ts(m.group(1)), "level": m.group(2), "text": plain_text(m.group(3)), "raw": m.group(3)})
    return events[-max_lines:]


# ----------------------------------------------------------------------------------------------- services, disk, api
def _run_sc_query(args):
    try:
        p = subprocess.run(["sc", "query"] + args, capture_output=True, text=True, timeout=10)
        return p.returncode, p.stdout + p.stderr
    except (OSError, subprocess.SubprocessError):
        return None, ""


def service_state(name, runner=_run_sc_query):
    """'running' | 'stopped' | 'missing' | 'unknown' for a Windows service."""
    code, out = runner([name])
    if code is None:
        return "unknown"
    if code == 1060 or "does not exist" in out or "no existe" in out:
        return "missing"
    m = re.search(r"(?:STATE|ESTADO)\s*:\s*\d+\s+(\w+)", out)      # English or Spanish Windows
    if not m:
        return "unknown"
    return "running" if m.group(1) == "RUNNING" else "stopped"


def disk_free_gb(path):
    try:
        return shutil.disk_usage(path).free / 1e9
    except OSError:
        return None


def api_health(url="http://localhost:8000/health", timeout=3):
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return r.status == 200
    except Exception:
        return False
