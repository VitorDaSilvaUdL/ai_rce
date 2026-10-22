"""Alerts in plain language, each with the action the laboratory team should take.

evaluate(ctx) takes a dict assembled by the web layer (see tests/test_panel_alerts.py for the keys) and returns the
active alerts sorted by severity. Rules follow section 6 of the specification.
"""
from dataclasses import dataclass
from datetime import timedelta

from panel.wincc import LABELS

LEVEL_ORDER = ["critical", "warning", "info"]

WINCC_STALE = timedelta(minutes=15)
SC_DEAD_S = 20 * 60
STATUS_OLD_S = 20 * 60
PAUSED_LONG = timedelta(hours=2)
COVER_STUCK_S = 10 * 60
DISK_LOW_GB = 2.0


@dataclass
class Alert:
    id: str
    level: str
    title: str
    action: str
    detail: str = ""

    def as_dict(self):
        return {"id": self.id, "level": self.level, "title": self.title, "action": self.action, "detail": self.detail}


def _label(variable, system):
    name = LABELS.get(variable, (variable, ""))[0]
    unit = {"RCE-a": " (unidad A)", "RCE-b": " (unidad B)"}.get(system, "")
    return name + unit


def evaluate(ctx):
    now = ctx["now"]
    real = ctx.get("mode") == "real"
    status = ctx.get("status") or {}
    out = []

    # --- data
    last = ctx.get("wincc_last_write")
    if last is None or now - last > WINCC_STALE:
        when = f" desde las {last:%H:%M}" if last else ""
        out.append(Alert("wincc_stale", "critical", f"WinCC no está registrando{when}",
                         "Comprobar que WinCC Runtime está abierto en este PC y que registra en la carpeta RCE"))
    for (variable, system), s in sorted((ctx.get("series") or {}).items()):
        if s.get("stale"):
            out.append(Alert(f"probe:{variable}/{system}", "warning", f"La sonda {_label(variable, system)} no da lecturas",
                             "Revisar el cableado de la sonda y anotar la incidencia en el diario",
                             f"última lectura hace {int(s.get('age_s', 0) // 60)} min"))
        elif s.get("frozen"):
            out.append(Alert(f"probe:{variable}/{system}", "warning", f"La sonda {_label(variable, system)} no cambia de valor",
                             "Comprobar la sonda; si el valor es correcto, ignorar este aviso"))
        elif s.get("last_invalid"):
            out.append(Alert(f"probe:{variable}/{system}", "warning", f"La sonda {_label(variable, system)} marca lectura no válida",
                             "Revisar la sonda en WinCC (Validity distinto de 1)"))

    # --- processes
    if not ctx.get("sc_running", True):
        out.append(Alert("sc_stopped", "critical", "El control está parado", "Pulsar «Arrancar»"))
    else:
        hb = ctx.get("heartbeat_age_s")
        if hb is None or hb > SC_DEAD_S:
            out.append(Alert("sc_dead", "critical", "El control no da señales de vida",
                             "Pulsar «Reiniciar». Si se repite, avisar a Vítor",
                             "" if hb is None else f"último latido hace {int(hb // 60)} min"))
    if not ctx.get("api_running", True) or not ctx.get("api_ok", True):
        out.append(Alert("api_down", "warning", "La predicción no responde",
                         "Se reinicia sola; si sigue en rojo 5 minutos, pulsar «Reiniciar»"))

    # --- forecast
    updated_day = (status.get("forecast") or {}).get("updated_day")
    if status and updated_day is not None and updated_day != now.day:
        out.append(Alert("forecast_old", "warning", "Sin previsión meteorológica nueva; se usa la última",
                         "Comprobar la conexión a internet del PC"))

    # --- shipping and disk
    fb = ctx.get("filebeat", "unknown")
    if fb == "stopped":
        out.append(Alert("filebeat_down", "warning", "Kibana no se actualiza: el envío está parado", "Pulsar «Reiniciar envío a Kibana» en Inicio"))
    elif fb == "missing":
        out.append(Alert("filebeat_missing", "info", "El envío a Kibana no está instalado", "Instalar Filebeat (rce-monitoring)"))
    free = ctx.get("disk_free_gb")
    if free is not None and free < DISK_LOW_GB:
        out.append(Alert("disk_low", "warning", f"Poco espacio en disco ({free:.1f} GB libres)", "Avisar a Vítor"))

    # --- real mode only
    if real:
        pumps = status.get("pumps") or {}
        flow = ctx.get("flow_l_min")
        if pumps.get("b2") == "on" and flow is not None and flow <= 0.05:
            out.append(Alert("flow_zero_pump_on", "critical", "La bomba está en marcha pero no hay caudal",
                             "Revisar bomba y válvulas; parar el control si persiste"))
        if ctx.get("plc_alarm"):
            out.append(Alert("plc_alarm", "info", "Tapa bloqueada por alarma de lluvia o viento del PLC",
                             "Nada que hacer: se cierra sola; esperar a que pase"))
        if ctx.get("cover_measured") == "moving" and (status.get("age_s") or 0) > COVER_STUCK_S:
            out.append(Alert("cover_stuck", "critical", "La tapa no ha terminado el movimiento",
                             "Comprobar la mecánica de la tapa; no insistir con los mandos"))
        if status.get("paused"):
            since = status.get("paused_since") or ""
            try:
                from panel.readers import parse_ts
                t0 = parse_ts(since)
            except Exception:
                t0 = None
            if t0 and now - t0 > PAUSED_LONG:
                out.append(Alert("paused_long", "warning", f"Control automático en pausa desde las {t0:%H:%M}",
                                 "Reactivar el control automático o pasar el RCE a parada"))

    out.sort(key=lambda a: LEVEL_ORDER.index(a.level))
    return out
