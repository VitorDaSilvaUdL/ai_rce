import sys
import os
import random
import json
from datetime import datetime, timedelta
from time import sleep
import csv
from sc.logger import get_logger
from sc.utils.data_transform import fmt_joules 

logger = get_logger(__name__)

from sc.api_data.api_req import get_req, get_data
from sc.utils.getters import get_prod, get_dem, get_rain
from fastapi import FastAPI
from sc.utils.read_data import get_last_data_from_db
from .plc_controller import PLCController

from sc.config import (
    PREDICT_URL, P_BOMBA_WATTS, MIN_TIME_STEP,
    PLC_IP, PLC_RACK, PLC_SLOT, OUTPUT_CSV, ITER_TIME_SEC,
    PLC_CONFIG, OUTPUT_CSV_PATH, COP_PUMP_ENERGY_UNITS, FRAME_KEY_FORMAT, DEMAND_ADJUST, NEXT_STATE_POLICY
)
from sc.plc_shadow import make_plc
from sc import cycle_log
from sc import state_io
from sc import decision

last_decision_row = None  # fila de get_decision() del ciclo en curso (DECISION_HEADER)
last_decision = None      # decision.Decision del ciclo en curso (motivos para el panel)
action = 0                # 0 = no, 1 = si, 2 = parada (última decisión aplicada)
mode = "parada"           # modo de la última franja activa o leído del PLC al arrancar
_last_set_state_seq = 0   # último control.json:set_state_seq aplicado (modo sombra)

# logger.setLevel(logging.DEBUG)

# url = "http://localhost:8000/predict"
# action = 0  # 0 = Tancat | 1 = Obert (Bomba OFF | Bomba ON)
# mode = "hot"  # Mode inicial, pot ser "cold" o "hot" pero se cambia automatico con la conexion del plc
# P_bomba_watts = 10  # Potència de la bomba en Watts (W)
# min_time_step = 15  # Durada del time_step en minuts
# time_step_hours = min_time_step / 60  # Durada del time_step en hores per al càlcul d'energia

url = PREDICT_URL
P_bomba_watts = P_BOMBA_WATTS  # Potència de la bomba en Watts (W)
min_time_step = MIN_TIME_STEP # Durada del time_step en minuts
time_step_hours = int(ITER_TIME_SEC) / 60  # Durada del time_step en hores per al càlcul d'energia
COP_MIN = 1.0 # Umbral mínimo de COP para encender la bomba

# plc = PLCController(ip=PLC_IP, rack=PLC_RACK, slot=PLC_SLOT)

prod_data = {}
dem_data = {}
rain_data = {}
temp_mode = {}

system_data = {}

last_prediction_update_day = None

current_dem_target = 0.0
selected_time_frames = []
total_predicted_production = 0.0

import logging
from datetime import timedelta

logger = logging.getLogger(__name__)

import pathlib

HEARTBEAT_FILE = "heartbeat_sc.txt"

def write_heartbeat():
    """
    Escribe la fecha/hora actual en un fichero de 'latido'.
    El watchdog usará este fichero para saber si main está vivo.
    """
    path = pathlib.Path(HEARTBEAT_FILE)
    path.write_text(datetime.now().isoformat())


def calculate_dem_for_period(df_demand, start_dt, end_dt):
    """
    Calcula la demanda total (en unidades del dataset) dentro de un intervalo temporal.

    Recorre minuto a minuto desde `start_dt` hasta `end_dt` (excloent `end_dt`),
    busca cada instante en el diccionario/serie `df_demand` usando formato ISO 
    (YYYY-MM-DDTHH:MM:SS) sin segundos ni microsegundos, y acumula su valor.

    Parámetros:
        df_demand (dict o pandas.Series): 
            Diccionario o serie donde las claves son timestamps en formato ISO 
            "YYYY-MM-DDTHH:MM:SS" y los valores son demandas numéricas.
        start_dt (datetime): 
            Fecha y hora de inicio del intervalo (incluida).
        end_dt (datetime): 
            Fecha y hora de final del intervalo (excluida).

    Retorna:
        float: Demanda total acumulada en el período.
    """

    dem = 0.0
    current_dt = start_dt

    logger.debug(
        f"Calculando demanda desde {start_dt.isoformat()} hasta {end_dt.isoformat()} "
        f"(recorriendo minuto a minuto)."
    )

    while current_dt < end_dt:
        # Normalizar timestamp al formato esperado (segundos exactos, sin microsegundos)
        dt_str = current_dt.replace(second=0, microsecond=0).isoformat(timespec='seconds')

        if dt_str in df_demand:
            value = float(df_demand[dt_str])
            dem += value
            logger.debug(f"  + {value} en {dt_str} (acumulado: {dem})")
        # else:
        #     logger.debug(f"  - No hay dato de demanda para {dt_str}")

        current_dt += timedelta(minutes=1)

    logger.info(
        f"Demanda total calculada para el intervalo: {dem} "
        f"(entre {start_dt.isoformat()} y {end_dt.isoformat()})"
    )

    return dem


# Funció per calcular les franjes horaries optimes
def calculate_optimal_production_plan(available_prod, target_demand, type):
    """
    Calcula el plan óptimo de producción seleccionando las franjas horarias más eficientes
    para cubrir una demanda energética objetivo.

    Parámetros:
        available_prod (dict[str → float]):
            Diccionario {timestamp_iso: producción_prevista_joules}.
        target_demand (float):
            Energía total que se desea cubrir ([Joules]).
        type (int):
            Tipo de modo asignado (1 = HOT, 0 = COLD).

    Retorna:
        (list[datetime], float):
            - Lista de franjas seleccionadas.
            - Producción total acumulada.
        Si no se alcanza la demanda:
            ([], -1)
    """
    logger.debug("=== Inicio cálculo de plan óptimo de producción ===")
    logger.debug(f"Demanda objetivo: {fmt_joules(target_demand)} [Joules]")
    logger.debug(f"Número total de franjas disponibles: {len(available_prod)}")

    # # Ordenar por producción ascendente
    # sorted_prod = dict(sorted(available_prod.items(), key=lambda item: item[1]))

    # logger.debug("Franjas ordenadas por producción (ascendente):")
    # for ts, prod in sorted_prod.items():
    #     logger.debug(f"  - {ts}: {prod} [Joules]")

    # Ordenar por producción ascendente, con conversión explícita a float
    sorted_items = sorted(
        available_prod.items(),
        key=lambda item: float(item[1])
    )

    logger.debug("Franjas ordenadas por producción (ascendente):")
    for ts, prod in sorted_items:
        logger.debug(f"  - {ts}: {fmt_joules(float(prod))} [Joules]")

    accumulated_production = 0.0
    selected_frames = []

    logger.debug("\n--- Selección de franjas ---")
    for time_frame_str, production_value in sorted_items:
        accumulated_production += float(production_value)
        selected_frames.append(time_frame_str)

        logger.debug(
            f"Seleccionada {time_frame_str} → {fmt_joules(float(production_value))} [Joules] | "
            f"Acumulado: {fmt_joules(accumulated_production / target_demand)} [Joules]"
        )

        if accumulated_production >= target_demand:
            logger.debug("Se alcanzó la demanda objetivo. Deteniendo selección.")
            break

    # Si no se llega al objetivo
    if accumulated_production < target_demand:
        logger.debug(
            f"No se alcanzó la demanda objetivo ({fmt_joules(accumulated_production)} < {fmt_joules(target_demand)})."
        )
        logger.debug("=== Fin del cálculo: demanda NO alcanzada ===")
        return [], -1

    # Conversión a datetime
    selected_frames_dt = [datetime.fromisoformat(ts) for ts in selected_frames]

    logger.debug("\n--- Franjas seleccionadas (datetime) ---")
    for dt in selected_frames_dt:
        logger.debug(f"  * {dt.isoformat()}")

    # Asignación de modos
    logger.debug("\n--- Asignación de modos en temp_mode ---")
    for time in selected_frames_dt:
        temp_mode[time] = type
        logger.debug(f"  {time.isoformat()}  → modo = {type}")

    logger.debug("\n=== Fin del cálculo de plan óptimo ===")
    logger.debug(f"Producción total acumulada: {fmt_joules(accumulated_production)} [Joules]\n")

    return selected_frames_dt, accumulated_production

# Funcio que verifica la producció real i resta a la demanda
def verify_and_adjust_demand(current_demand_to_cover):
    global action
    if action == 1:
        actual_production_last_step = P_bomba_watts * time_step_hours * random.uniform(0.8, 1.2)
    else:
        actual_production_last_step = 0

    dem_remaining = current_demand_to_cover - actual_production_last_step
    return max(0, dem_remaining)

def rodona_15_minuts_avall(dt):
    minuts = (dt.minute // 15) * 15
    return dt.replace(minute=0, second=0, microsecond=0) + timedelta(minutes=minuts)

def get_now_val(curr: dict):
    now = rodona_15_minuts_avall(datetime.now())
    next = now + timedelta(minutes=15)
    for frame in curr.keys():
        frame = datetime.strptime(frame, "%Y-%m-%d %H:%M")
        if now.hour == frame.hour and now.minute <= frame.minute < next.minute and now.day == frame.day:
            return curr[frame.strftime("%Y-%m-%d %H:%M")]
    return 0


def rodona_hora_avall(dt):
    """Rodona datetime al minut 0 de l'hora (p.e. 12:34 -> 12:00)."""
    return dt.replace(minute=0, second=0, microsecond=0)


def get_now_val_2(curr: dict):
    now = rodona_hora_avall(datetime.now())
    left = now - timedelta(minutes=1)
    for frame in curr.keys():
        frame = datetime.strptime(frame, "%Y-%m-%dT%H:%M:%S") - timedelta(days=1)
        if now.hour == frame.hour and frame.minute == 0 and now.day == frame.day or frame.hour == left.hour and frame.minute == 59 and now.day == frame.day:
            return curr[(frame + timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%S") ]
    return 0

def update_predictions(now, system_data, context: str) -> bool:
    """
    Actualiza dem_data, prod_data y rain_data a partir del backend de predicciones.

    Devuelve:
        True  si se han podido actualizar las predicciones.
        False si ha fallado la petición (req is None).
    """
    global dem_data, prod_data, rain_data, last_prediction_update_day

    req = get_req(url, system_data)
    if req is None:
        logger.error(f"{now}: No s'han pogut obtenir prediccions ({context}): req=None.")
        return False

    dem_data = get_dem(req)
    prod_data = get_prod(req)
    rain_data = get_rain(req)
    last_prediction_update_day = now.day

    logger.info(
        f"{now}: Prediccions actualitzades ({context}). "
        f"Dia de darrera actualització: {last_prediction_update_day}."
    )
    return True


def plan_mode(
    now: datetime,
    mode_label: str,          # "HOT" o "COLD"
    dem_series_key: str,      # 'hot_dem' o 'cold_dem'
    prod_series_key: str,     # 'hot' o 'cold'
    start_dem: datetime,
    end_dem: datetime,
    mode_type_int: int        # 1 per HOT, 0 per COLD
) -> None:
    """
    Calcula la demanda en un període i genera el pla òptim de producció
    per a calor o fred, actualitzant les variables globals:
    - selected_time_frames
    - total_predicted_production
    - current_dem_target
    """
    global current_dem_target, selected_time_frames, total_predicted_production

    dem_value = calculate_dem_for_period(dem_data[dem_series_key], start_dem, end_dem)

    selected_time_frames, total_predicted_production = calculate_optimal_production_plan(
        prod_data[prod_series_key],
        dem_value,
        mode_type_int
    )
    current_dem_target = dem_value

    logger.info(
        f"{now}: Mode establert a {mode_label}. "
        f"Demanda {mode_label.lower()} objectiu: {fmt_joules(current_dem_target)} [Joules]. "
        f"Producció prevista: {fmt_joules(total_predicted_production)} [Joules]."
    )

    if total_predicted_production == -1:
        logger.warning(
            f"{now}: No s’ha pogut trobar un pla òptim per a la demanda de "
            f"{mode_label.lower()}. Demanda: {fmt_joules(dem_value)} [Joules]."
        )
    else:
        frames_str = [dt.isoformat(timespec='seconds') for dt in selected_time_frames]
        logger.info(
            f"{now}: Franges horàries seleccionades per a {mode_label.lower()}: {frames_str}"
        )


def get_decision():
    global system_data, last_prediction_update_day, total_predicted_production
    global selected_time_frames, current_dem_target, dem_data, prod_data
    global action, mode, last_decision_row, last_decision

    now = datetime.now().replace(microsecond=0)
    last_decision_row = None
    last_decision = None

    # Valor seguro por defecto (por si algo peta antes de asignar nada)
    safe_response = {"respuesta": "parada"}

    try:
        # 1) Leer datos del sistema
        system_data = get_last_data_from_db()
        logger.info(f"{now}: Nou cicle de control.")
        l = [now]

        # 2) Actualización diaria de predicciones a medianoche
        try:
            if now.day != last_prediction_update_day and now.hour == 0 and now.minute == 0:
                logger.info(f"{now}: Iniciant actualització diària de prediccions.")
                update_predictions(now, system_data, context="actualització diària")
        except Exception:
            logger.exception(f"{now}: Error actualitzant prediccions diàries.")

        # 3) Calcular nou horari de calor (7:00–7:14)
        try:
            if now.hour == 7 and 0 <= now.minute < 15:
                logger.info(f"{now}: Planificant demanda de calor.")
                if update_predictions(now, system_data, context="calor"):
                    start_dem_heat = now.replace(
                        hour=12, minute=0, second=0, microsecond=0
                    )
                    end_dem_heat = (now + timedelta(days=1)).replace(
                        hour=12, minute=0, second=0, microsecond=0
                    )

                    plan_mode(
                        now=now,
                        mode_label="HOT",
                        dem_series_key="hot_dem",
                        prod_series_key="hot",
                        start_dem=start_dem_heat,
                        end_dem=end_dem_heat,
                        mode_type_int=1,
                    )
        except Exception:
            logger.exception(f"{now}: Error planificant demanda de calor.")

        # 4) Calcular nou horari de fred (19:00–19:14)
        try:
            if now.hour == 19 and 0 <= now.minute < 15:
                logger.info(f"{now}: Planificant demanda de fred.")
                if update_predictions(now, system_data, context="fred"):
                    start_dem_cool = (now + timedelta(days=1)).replace(
                        hour=0, minute=0, second=0, microsecond=0
                    )
                    end_dem_cool = (now + timedelta(days=2)).replace(
                        hour=0, minute=0, second=0, microsecond=0
                    )

                    plan_mode(
                        now=now,
                        mode_label="COLD",
                        dem_series_key="cold_dem",
                        prod_series_key="cold",
                        start_dem=start_dem_cool,
                        end_dem=end_dem_cool,
                        mode_type_int=0,
                    )
        except Exception:
            logger.exception(f"{now}: Error planificant demanda de fred.")

        logger.info(
            f"{now}: Lògica de control en temps real. "
            f"Demanda objectiu actual: {fmt_joules(current_dem_target)} [Joules]."
        )

        # 5) Lògica d'acció en temps real (sc/decision.py: la misma lógica, con motivos para el panel)
        d = decision.decide(now, temp_mode, prod_data, current_dem_target, total_predicted_production,
                            action_prev=action, mode_prev=mode, p_bomba_watts=P_bomba_watts,
                            time_step_hours=time_step_hours, min_time_step=min_time_step, cop_min=COP_MIN,
                            rng=random.uniform, pump_energy_units=COP_PUMP_ENERGY_UNITS,
                            frame_key_format=FRAME_KEY_FORMAT, demand_adjust=DEMAND_ADJUST)
        last_decision = d
        for code in d.codes:                       # las mismas líneas de log que antes, para quien las lea
            if code == "insufficient_production":
                logger.warning(f"{now}: Producció insuficient. Bomba OFF.")
            elif code.startswith("in_frame:"):
                logger.info(f"{now}: Franja horària actual ({now.isoformat(timespec='seconds')}) activa per a l'operació.")
            elif code == "frame_prod":
                logger.info(f"{now}: Producció {'calor' if d.frame_mode == 'hot' else 'fred'} prevista en aquesta franja: "
                            f"{fmt_joules(d.frame_prod_j)} [Joules].")
            elif code == "frame_prod_missing":
                logger.info(f"{now}: Sense dades de producció per a la franja actual en mode {d.frame_mode}.")
            elif code.startswith("cop:"):
                joules = f" ({d.pump_energy_wh * 3600:g} J)" if d.pump_energy_units == "J" else ""
                logger.info(f"{now}: Energia consumida per la bomba en aquesta franja: {d.pump_energy_wh:.2f} Wh{joules}.")
                logger.info(f"{now}: COP calculat: {d.cop:.2f}.")
            elif code == "cop_ok":
                logger.info(f"{now}: COP >= {COP_MIN} i hi ha demanda. Bomba ON (action=1). Ttank = 1.")
            elif code in ("cop_low", "no_demand_in_frame"):
                logger.info(f"{now}: COP < {COP_MIN} o no hi ha demanda. Bomba OFF (action=0).")
            elif code == "out_of_frames":
                logger.info(f"{now}: Hora actual fora de franges seleccionades. Bomba OFF.")
            elif code == "demand_adjusted":
                logger.info(f"{now}: Demanda restant ajustada de {fmt_joules(d.demand_before)} [Joules] "
                            f"a {fmt_joules(d.demand_after)} [Joules].")
            elif code == "plan_done":
                logger.info(f"{now}: Demanda coberta/esgotada. Reiniciant pla i bomba OFF.")
            elif code == "no_pending_demand":
                logger.info(f"{now}: No hi ha demanda objectiu pendent. Bomba OFF.")
        action = d.action
        mode = d.mode
        current_dem_target = d.demand_after
        if d.plan_reset:
            selected_time_frames = []
            total_predicted_production = 0.0
        l.append(d.in_time_frame)

        # 6) Guardar en CSV
        l.append(current_dem_target)

        # Acción en text per a log/csv (0=no, 1=yes, 2=parada)
        resp_labels = ["no", "si", "parada"]
        safe_action = 2 if action not in (0, 1, 2) else action
        accion_str = resp_labels[safe_action]

        l.append(accion_str)
        l.append(mode)
        l.append(get_now_val(prod_data['hot']))
        l.append(get_now_val(prod_data['cold']))
        l.append(get_now_val_2(dem_data['hot_dem']))
        l.append(get_now_val_2(dem_data['cold_dem']))

        # La fila se escribe en run_cycle(), junto con el estado del PLC (aplicado o simulado)
        last_decision_row = l

        logger.info(
            f"{now}: Estat final de la bomba per a aquest cicle: "
            f"{'ON' if safe_action == 1 else 'OFF'}."
        )

        response = {"respuesta": resp_labels[safe_action]}
        logger.info(f"RESPONSE: {response}")
        return response

    except Exception:
        # Cualquier error no controlado aquí NO revienta el bucle.
        logger.exception(f"{now}: Error inesperat a get_decision(). Retornant 'parada'.")
        return safe_response

# PLC según config: plc.mode = "shadow" (por defecto, PLCShadow: no se conecta ni escribe) o "real"
plc = make_plc(PLC_CONFIG)

mode_actions = {
    "hot": plc.set_heat_mode,
    "cold": plc.set_cold_mode,
    "parada": plc.set_parada_mode,
    "automatic": plc.set_automatic_mode,
}

def _last_sample(data):
    """Última muestra numérica de system_data['data'] (lo que el control ha leído de WinCC)."""
    try:
        last = data["data"][-1]
    except (KeyError, IndexError, TypeError):
        return {}
    return {k: v for k, v in last.items() if isinstance(v, (int, float)) and not isinstance(v, bool)}


def build_status(plc, now, estado_actual, nuevo_estado, applied_state, control, cover_measured=""):
    """Contenido de state/status.json: lo que el panel necesita de este ciclo."""
    out = cycle_log.state_outputs(applied_state, alarm=plc.alarm_active)
    row = last_decision_row or []
    dec = dict(zip(cycle_log.DECISION_HEADER, row)) if row else {}
    return {
        "written_at": datetime.now().replace(microsecond=0).isoformat(sep=" "),
        "cycle_time": now.isoformat(sep=" "),
        "next_cycle_time": (now + timedelta(minutes=int(ITER_TIME_SEC))).isoformat(sep=" "),
        "execution": plc.execution,
        "paused": bool(control.get("paused")), "paused_by": control.get("paused_by", ""),
        "plc_state_before": estado_actual, "plc_state": applied_state,
        "plc_mode": out["plc_mode"], "cover": out["cover"], "cover_measured": cover_measured,
        "pumps": {"b1": out["pump_b1"], "b2": out["pump_b2"], "ev1": out["ev1"], "ev2": out["ev2"]},
        "inputs": _last_sample(system_data),
        "inputs_samples": len(system_data.get("data", [])) if isinstance(system_data, dict) else 0,
        "forecast": {
            "demand_hot": dem_data.get("hot_dem", {}), "demand_cold": dem_data.get("cold_dem", {}),
            "prod_hot": prod_data.get("hot", {}), "prod_cold": prod_data.get("cold", {}),
            "rain": rain_data, "updated_day": last_prediction_update_day,
        },
        "plan": {"target_demand_j": current_dem_target, "selected_frames": list(selected_time_frames),
                 "predicted_production_j": total_predicted_production},
        "decision": {
            **(last_decision.as_dict() if last_decision else {"reasons": [], "codes": []}),
            "action": dec.get("action", ""), "next_state": nuevo_estado,
            "in_time_frame": dec.get("in_time_frame", None), "mode": dec.get("mode", ""),
            "hot_prod": dec.get("hot_prod", ""), "cold_prod": dec.get("cold_prod", ""),
            "hot_dem": dec.get("hot_dem", ""), "cold_dem": dec.get("cold_dem", ""),
        },
        "schedule": getattr(plc, "schedule", None),
    }


def run_cycle(plc, out_path=OUTPUT_CSV_PATH, state_dir=state_io.STATE_DIR):
    """Un ciclo de control: decisión de la IA, nuevo estado del PLC (aplicado o, en modo sombra,
    solo simulado), una fila en dades.csv y state/status.json para el panel.
    state/control.json (escrito por el panel) puede pausar la aplicación o, en sombra, fijar el estado virtual."""
    global _last_set_state_seq
    control = state_io.read_control(state_dir)

    # 0) Órdenes del panel: estado virtual (solo sombra), una vez por set_state_seq
    seq = int(control.get("set_state_seq") or 0)
    if plc.execution == "shadow" and control.get("set_state") and seq != _last_set_state_seq:
        logger.info(f"PANEL: estado virtual fijado a {control['set_state']} (seq {seq})")
        plc.state = control["set_state"]
        _last_set_state_seq = seq

    # 1) Obtener decision de la logica de control
    respuesta_nn = get_decision()
    respuesta_nn = respuesta_nn["respuesta"]
    logger.info(f"nn_result: {respuesta_nn}")

    # 2) Leer estado actual del PLC (virtual en modo sombra)
    estado_actual, combinaciones = plc.get_system_state()

    # 3) Calcular nuevo estado a partir de la respuesta de la IA. Política "frame_mode" (por defecto): «sí» fija el
    #    modo de la franja activa (frío/calor) en vez de alternar; "toggle": máquina de estados original.
    if last_decision is not None:
        nuevo_estado = decision.next_state(last_decision, estado_actual, plc, policy=NEXT_STATE_POLICY)
    else:
        nuevo_estado = plc.decide_next_state_from_nn(respuesta_nn, estado_actual)

    # AVISO: solo si la IA dice "si" y el estado realmente cambia
    if respuesta_nn == "si" and nuevo_estado != estado_actual:
        if plc.execution == "shadow":
            logger.info(
                f"RCE (SOMBRA) - la IA cambiaría de estado: {estado_actual} -> {nuevo_estado}. "
                f"No se aplica en el PLC."
            )
        else:
            logger.warning(
                f"RCE - CAMBIO DE ESTADO.\n"
                f"Estado anterior: {estado_actual}\n"
                f"Estado nuevo:     {nuevo_estado}\n"
                f"Se procede a aplicar el cambio en el PLC."
            )

    # 4) Escribir nuevo estado en el PLC (PLCShadow solo actualiza su estado virtual); en pausa, no
    if control.get("paused"):
        logger.info(f"PANEL: control en pausa ({control.get('paused_by', '')}); no se aplica {nuevo_estado}")
        applied_state = estado_actual
    else:
        plc.final_write_to_plc_nn_mode(nuevo_estado, combinaciones)
        applied_state = nuevo_estado

    # 5) dades.csv: decisión + estado + tapa (medida con los finales de carrera solo en modo real)
    try:
        cover_measured = plc.read_cover_position() if plc.execution == "real" else ""
    except Exception:
        logger.exception("No se ha podido leer la posición de la tapa")
        cover_measured = ""
    try:
        cycle_log.write_cycle_row(
            out_path, last_decision_row,
            cycle_log.state_columns(plc.execution, estado_actual, applied_state,
                                    cover_measured=cover_measured, alarm=plc.alarm_active),
        )
    except Exception:
        logger.exception(f"Error escribiendo {out_path}")

    # 6) status.json para el panel
    try:
        now = datetime.now().replace(microsecond=0)
        state_io.write_status(state_dir, build_status(plc, now, estado_actual, nuevo_estado, applied_state,
                                                      control, cover_measured))
    except Exception:
        logger.exception("Error escribiendo status.json")
    return applied_state


def stop():
    # Funcion de parada segura del sistema
    if plc.alarm_active:
        logger.error("Alarma detectada -> apagando sistema")
        # Se pueden cerrar puertas antes de desconectar
        plc.close_doors()
        plc.disconnect()

    sys.exit(1)

if __name__ == '__main__':

    # --- Inicio del sistema: carga de datos y conexion con el PLC ---
    now = datetime.now().replace(microsecond=0)
    logger.info(f"{now}: Sistema iniciado. Cargando datos iniciales.")

    # Leer ultimo estado conocido del sistema
    system_data = get_last_data_from_db()
    logger.debug(f"system_data: {system_data}")

    # Conectar con el PLC
    plc.connect()
    mode = plc.get_current_mode()
    logger.info(f"Modo actual PLC: {mode}")

    # Cargar predicciones iniciales (demanda, produccion, lluvia)
    if not update_predictions(now, system_data, context="inicio sistema"):
        logger.error(f"{now}: No se han podido cargar predicciones iniciales. Saliendo.")
        stop()

    # --- Planificacion inicial de la demanda de calor ---
    logger.info(f"{now}: Planificacion inicial de la demanda de calor.")
    start_dem_heat = now.replace(hour=12, minute=0, second=0, microsecond=0)
    end_dem_heat = (now + timedelta(days=1)).replace(
        hour=12, minute=0, second=0, microsecond=0
    )
    logger.info(f"Ventana de planificacion calor: {start_dem_heat} <-> {end_dem_heat}")

    plan_mode(
        now=now,
        mode_label="HOT",
        dem_series_key="hot_dem",
        prod_series_key="hot",
        start_dem=start_dem_heat,
        end_dem=end_dem_heat,
        mode_type_int=1,
    )

    # # Mantener stop aqui para pruebas (igual que en tu codigo original)
    # stop()

    # --- Planificacion inicial de la demanda de frio (solo log informativo) ---
    logger.info(f"{now}: Planificacion inicial de la demanda de frio.")
    start_dem_cool = (now + timedelta(days=1)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    end_dem_cool = (now + timedelta(days=2)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    logger.info(f"Ventana de planificacion frio: {start_dem_cool} <-> {end_dem_cool}")

    dem_f = calculate_dem_for_period(dem_data["cold_dem"], start_dem_cool, end_dem_cool)
    logger.info(f"{now}: Demanda de frio estimada para el periodo: {fmt_joules(dem_f)} [Joules].")

    selected_time_frames_cold, total_predicted_production_cold = (
        calculate_optimal_production_plan(prod_data["cold"], dem_f, 0)
    )

    if dem_f == 0:
        logger.info(f"{now}: No hay demanda de frio en los proximos dias.")
    else:
        logger.info(
            f"{now}: Energia total prevista para cubrir la demanda de frio: "
            f"{fmt_joules(total_predicted_production_cold)} [Joules]."
        )
        if total_predicted_production_cold == -1:
            logger.info(
                f"{now}: No se ha podido encontrar un plan optimo para la demanda de frio. "
                f"Demanda: {fmt_joules(dem_f)} [Joules]."
            )
        else:
            frames_cold_str = [
                dt.isoformat(timespec="seconds")
                for dt in sorted(selected_time_frames_cold)
            ]
            logger.info(
                f"{now}: Franges horaries seleccionadas per al mode COLD (solo informacion): "
                f"{frames_cold_str}"
            )

    # --- Bucle de control principal ---
    while True:
        write_heartbeat()  # latido del sistema
        logger.info("Nuevo ciclo de control")

        run_cycle(plc)

        # Espera entre iteraciones del bucle de control
        sleep(60*15)  # version normal cada 15 minutos
        # sleep(5)        # version rapida para pruebas
