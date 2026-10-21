"""Reader of the WinCC CSV archives (Lecturas0.csv, Solarimeter0.csv, ...) for the panel.

WinCC appends one reading per line: "VarName";"TimeString";"VarValue";"Validity";"Time_ms", Latin-1, decimal comma,
local time dd/MM/yyyy HH:mm:ss. The files grow to tens of MB, so the panel only reads their tail (last max_bytes).
Tag names map to the variable names of the published RCE dataset (same table as the Kibana pipeline rce-wincc).
"""
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

ARCHIVES = ("Lecturas*.csv", "Solarimeter*.csv", "Pyrgeometer*.csv", "EnergymeterRCE_*.csv", "TaulaKW*.csv")

# WinCC tag (without the _RCEa/_RCEb suffix) -> variable name of the dataset. Source of truth shared with
# rce_review/monitoring/server/setup/pipeline_rce_wincc.json.
CANONICAL = {
    "TempT6": "t_hot_tank_c", "TempT9": "t_cold_tank_c",
    "TempT9_1": "t_cold_tank_l1_c", "TempT9_2": "t_cold_tank_l2_c", "TempT9_3": "t_cold_tank_l3_c",
    "TempT3_1": "t_surface_1_c", "TempT3_2": "t_surface_2_c", "TempT3_3": "t_surface_3_c",
    "TempT3_4": "t_surface_4_c", "TempT3_5": "t_surface_5_c", "TempT3_6": "t_surface_6_c",
    "TempT1": "t_circuit_1_c", "TempT2": "t_circuit_2_c", "TempT4": "t_circuit_4_c", "TempT5": "t_circuit_5_c",
    "TempT7": "t_circuit_7_c", "TempT8": "t_circuit_8_c", "TempT10": "t_circuit_10_c", "TempT11": "t_circuit_11_c",
    "TempT22": "t_aux_tank_22_c", "TempT23": "t_aux_tank_23_c", "TempT24": "t_aux_tank_24_c",
    "CauQ1": "flow_rce_l_min", "PresPT1": "p_circuit_1_bar", "PresPT2": "p_circuit_2_bar",
    "TempExt": "t_ambient_c", "HumExt": "rh_ambient_pct", "VelVent": "wind_speed_m_s",
    "IO_SENSOR1_DATA": "solar_global_w_m2", "E_FIR, neto, [W/m2]": "ir_net_w_m2",
}

# variable -> (label for the panel, unit)
LABELS = {
    "t_hot_tank_c": ("Depósito caliente (T6)", "°C"), "t_cold_tank_c": ("Depósito frío (T9)", "°C"),
    "t_cold_tank_l1_c": ("Depósito frío, nivel 1", "°C"), "t_cold_tank_l2_c": ("Depósito frío, nivel 2", "°C"),
    "t_cold_tank_l3_c": ("Depósito frío, nivel 3", "°C"),
    "t_surface_1_c": ("Superficie del panel 1", "°C"), "t_surface_2_c": ("Superficie del panel 2", "°C"),
    "t_surface_3_c": ("Superficie del panel 3", "°C"), "t_surface_4_c": ("Superficie del panel 4", "°C"),
    "t_surface_5_c": ("Superficie del panel 5", "°C"), "t_surface_6_c": ("Superficie del panel 6", "°C"),
    "t_circuit_1_c": ("Circuito T1", "°C"), "t_circuit_2_c": ("Entrada al RCE (T2)", "°C"),
    "t_circuit_4_c": ("Salida del RCE (T4)", "°C"), "t_circuit_5_c": ("Circuito T5", "°C"),
    "t_circuit_7_c": ("Circuito T7", "°C"), "t_circuit_8_c": ("Circuito T8", "°C"),
    "t_circuit_10_c": ("Circuito T10", "°C"), "t_circuit_11_c": ("Circuito T11", "°C"),
    "t_aux_tank_22_c": ("Depósito auxiliar T22", "°C"), "t_aux_tank_23_c": ("Depósito auxiliar T23", "°C"),
    "t_aux_tank_24_c": ("Depósito auxiliar T24", "°C"),
    "flow_rce_l_min": ("Caudal por el RCE", "L/min"), "p_circuit_1_bar": ("Presión PT1", "bar"),
    "p_circuit_2_bar": ("Presión PT2", "bar"), "t_ambient_c": ("Aire exterior", "°C"),
    "rh_ambient_pct": ("Humedad exterior", "%"), "wind_speed_m_s": ("Viento", "m/s"),
    "solar_global_w_m2": ("Irradiancia solar", "W/m²"), "ir_net_w_m2": ("Infrarrojo neto", "W/m²"),
}

# the variables the Inicio and Lecturas screens show first, in this order
MAIN_VARIABLES = ["t_cold_tank_c", "t_hot_tank_c", "t_circuit_2_c", "t_circuit_4_c", "flow_rce_l_min", "ir_net_w_m2",
                  "solar_global_w_m2", "t_ambient_c", "rh_ambient_pct", "wind_speed_m_s"]

TIME_FORMATS = ("%d/%m/%Y %H:%M:%S", "%d/%m/%Y %H:%M")


@dataclass
class Reading:
    ts: datetime
    tag: str
    system: str
    variable: str
    value: float | None
    valid: bool
    raw: str = ""
    source: str = ""

    @property
    def label(self):
        return LABELS.get(self.variable, (self.variable, ""))[0]

    @property
    def unit(self):
        return LABELS.get(self.variable, (self.variable, ""))[1]


def parse_var(varname):
    """(tag, system) from a WinCC VarName: suffix _RCEa/_RCEb -> unit A/B and tag without suffix."""
    if varname.endswith("_RCEa"):
        return varname[:-5], "RCE-a"
    if varname.endswith("_RCEb"):
        return varname[:-5], "RCE-b"
    if varname.endswith("_a"):
        return varname, "RCE-a"
    if varname.endswith("_b"):
        return varname, "RCE-b"
    return varname, "RCE"


def _unquote(s):
    s = s.strip()
    return s[1:-1] if len(s) >= 2 and s[0] == '"' and s[-1] == '"' else s


def parse_line(line, source=""):
    line = line.rstrip("\r\n")
    if not line or line.startswith('"VarName"') or "$RT_" in line:
        return None
    parts = line.split(";")
    if len(parts) < 4:
        return None
    varname, ts_s, val_s, valid_s = (_unquote(p) for p in parts[:4])
    ts = None
    for fmt in TIME_FORMATS:
        try:
            ts = datetime.strptime(ts_s, fmt)
            break
        except ValueError:
            continue
    if ts is None:
        return None
    try:
        value = float(val_s.replace(",", "."))
    except ValueError:
        value = None
    tag, system = parse_var(varname)
    return Reading(ts=ts, tag=tag, system=system, variable=CANONICAL.get(tag, tag), value=value,
                   valid=valid_s.strip() == "1", raw=line, source=source)


def tail_text(path, max_bytes):
    """Last max_bytes of the file as text, starting at a line boundary (the first partial line is dropped)."""
    path = Path(path)
    size = path.stat().st_size
    with open(path, "rb") as f:
        if size > max_bytes:
            f.seek(size - max_bytes)
            data = f.read()
            nl = data.find(b"\n")
            data = data[nl + 1:] if nl >= 0 else b""
        else:
            data = f.read()
    return data.decode("latin-1")


def read_recent(path, since, max_bytes=4_000_000):
    """Readings of one archive with ts >= since, from the tail of the file. Missing file -> []."""
    path = Path(path)
    if not path.exists():
        return []
    out = []
    for line in tail_text(path, max_bytes).splitlines():
        r = parse_line(line, source=path.name)
        if r is not None and r.ts >= since:
            out.append(r)
    return out


def read_folder(folder, since, max_bytes=4_000_000, patterns=ARCHIVES):
    folder = Path(folder)
    out = []
    for pattern in patterns:
        for path in sorted(folder.glob(pattern)):
            out.extend(read_recent(path, since, max_bytes))
    return out


def latest_values(readings):
    """{(variable, system): last Reading} (by timestamp)."""
    latest = {}
    for r in readings:
        key = (r.variable, r.system)
        if key not in latest or r.ts >= latest[key].ts:
            latest[key] = r
    return latest


FROZEN_PREFIXES = ("t_", "rh_", "ir_")   # a constant 0 is normal for solar at night, flow, wind or energy counters


def series_status(readings, now, stale_after=timedelta(minutes=30), frozen_window=timedelta(hours=1)):
    """Per series: last reading, stale (no reading for stale_after), frozen (temperature, humidity or IR constant
    over frozen_window with at least 3 samples), last_invalid (Validity != 1 in the last reading)."""
    by_key = defaultdict(list)
    for r in readings:
        by_key[(r.variable, r.system)].append(r)
    status = {}
    for key, rs in by_key.items():
        rs.sort(key=lambda r: r.ts)
        last = rs[-1]
        window = [r for r in rs if r.ts >= now - frozen_window and r.value is not None]
        frozen = (key[0].startswith(FROZEN_PREFIXES) and len(window) >= 3 and len({r.value for r in window}) == 1)
        status[key] = {
            "last": last, "age_s": (now - last.ts).total_seconds(),
            "stale": now - last.ts > stale_after, "frozen": frozen, "last_invalid": not last.valid,
        }
    return status
