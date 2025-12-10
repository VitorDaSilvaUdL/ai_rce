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
    PLC_IP, PLC_RACK, PLC_SLOT, OUTPUT_CSV, ITER_TIME_SEC
)

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

def get_decision_old():
    global system_data, last_prediction_update_day, total_predicted_production
    global selected_time_frames, current_dem_target, dem_data, prod_data
    global action, mode

    now = datetime.now().replace(microsecond=0)

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
                req = get_req(url, system_data)
                if req is None:
                    logger.error(f"{now}: No s'han pogut obtenir prediccions (req=None) en actualització diària.")
                else:
                    dem_data = get_dem(req)
                    prod_data = get_prod(req)
                    rain_data = get_rain(req)
                    last_prediction_update_day = now.day
                    logger.info(f"{now}: Prediccions actualitzades per al dia {last_prediction_update_day}.")
        except Exception:
            logger.exception(f"{now}: Error actualitzant prediccions diàries.")

        # 3) Calcular nou horari de calor (7:00–7:14)
        try:
            if now.hour == 7 and 0 <= now.minute < 15:
                logger.info(f"{now}: Planificant demanda de calor.")
                req = get_req(url, system_data)
                if req is None:
                    logger.error(f"{now}: No s'han pogut obtenir prediccions per a calor (req=None).")
                else:
                    dem_data = get_dem(req)
                    prod_data = get_prod(req)
                    rain_data = get_rain(req)
                    last_prediction_update_day = now.day

                    start_dem_heat = now.replace(hour=12, minute=0, second=0, microsecond=0)
                    end_dem_heat = (now + timedelta(days=1)).replace(hour=12, minute=0, second=0, microsecond=0)
                    dem_c = calculate_dem_for_period(dem_data['hot_dem'], start_dem_heat, end_dem_heat)

                    selected_time_frames, total_predicted_production = calculate_optimal_production_plan(
                        prod_data['hot'], dem_c, 1
                    )
                    current_dem_target = dem_c
                    logger.info(
                        f"{now}: Mode establert a HOT. Demanda calor objectiu: {fmt_joules(current_dem_target)} [Joules]. "
                        f"Producció prevista: {fmt_joules(total_predicted_production)} [Joules]."
                    )
                    if total_predicted_production == -1:
                        logger.warning(
                            f"{now}: No s’ha pogut trobar un pla òptim per a la demanda de calor. Demanda: {fmt_joules(dem_c)} [Joules]."
                        )
                    else:
                        frames_str = [dt.isoformat(timespec='seconds') for dt in selected_time_frames]
                        logger.info(f"{now}: Franges horàries seleccionades per a calor: {frames_str}")
        except Exception:
            logger.exception(f"{now}: Error planificant demanda de calor.")

        # 4) Calcular nou horari de fred (19:00–19:14)
        try:
            if now.hour == 19 and 0 <= now.minute < 15:
                logger.info(f"{now}: Planificant demanda de fred.")
                req = get_req(url, system_data)
                if req is None:
                    logger.error(f"{now}: No s'han pogut obtenir prediccions per a fred (req=None).")
                else:
                    dem_data = get_dem(req)
                    prod_data = get_prod(req)
                    rain_data = get_rain(req)
                    last_prediction_update_day = now.day

                    start_dem_cool = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
                    end_dem_cool = (now + timedelta(days=2)).replace(hour=0, minute=0, second=0, microsecond=0)
                    dem_f = calculate_dem_for_period(dem_data['cold_dem'], start_dem_cool, end_dem_cool)

                    selected_time_frames, total_predicted_production = calculate_optimal_production_plan(
                        prod_data['cold'], dem_f, 0
                    )
                    current_dem_target = dem_f
                    logger.info(
                        f"{now}: Mode establert a COLD. Demanda fred objectiu: {fmt_joules(current_dem_target)} [Joules]. "
                        f"Producció prevista: {fmt_joules(total_predicted_production)} [Joules]."
                    )
                    if total_predicted_production == -1:
                        logger.warning(
                            f"{now}: No s’ha pogut trobar un pla òptim per a la demanda de fred. Demanda: {fmt_joules(dem_f)} [Joules]."
                        )
                    else:
                        frames_str = [dt.isoformat(timespec='seconds') for dt in selected_time_frames]
                        logger.info(f"{now}: Franges horàries seleccionades per a fred: {frames_str}")
        except Exception:
            logger.exception(f"{now}: Error planificant demanda de fred.")

        logger.info(f"{now}: Lògica de control en temps real. Demanda objectiu actual: {fmt_joules(current_dem_target)} [Joules].")

        # 5) Lògica d'acció en temps real
        if total_predicted_production == -1 and current_dem_target > 0:
            l.append(0)
            action = 2
            logger.warning(f"{now}: Producció insuficient. Bomba OFF.")
        else:
            found_active_time_frame = False
            current_time_frame_start = now.replace(
                minute=now.minute - (now.minute % min_time_step), second=0, microsecond=0
            )
            current_time_frame_end = current_time_frame_start + timedelta(minutes=min_time_step)
            logger.info(
                f"{now}: Franja horària actual: {current_time_frame_start.isoformat(timespec='seconds')} "
                f"a {current_time_frame_end.isoformat(timespec='seconds')}"
            )

            for time_frame_dt in temp_mode.keys():
                frame_end_dt = time_frame_dt + timedelta(minutes=min_time_step)
                if time_frame_dt <= now < frame_end_dt:
                    found_active_time_frame = True
                    logger.info(
                        f"{now}: Franja horària actual ({now.isoformat(timespec='seconds')}) activa per a l’operació."
                    )

                    current_frame_prod_value = 0.0
                    current_frame_str = time_frame_dt.isoformat(timespec='seconds')
                    curr_mode = "hot" if temp_mode[time_frame_dt] else "cold"

                    if curr_mode == "hot" and current_frame_str in prod_data['hot']:
                        current_frame_prod_value = float(prod_data['hot'][current_frame_str])
                        logger.info(
                            f"{now}: Producció calor prevista en aquesta franja: {fmt_joules(current_frame_prod_value)} [Joules]."
                        )
                    elif curr_mode == "cold" and current_frame_str in prod_data['cold']:
                        current_frame_prod_value = float(prod_data['cold'][current_frame_str])
                        logger.info(
                            f"{now}: Producció fred prevista en aquesta franja: {fmt_joules(current_frame_prod_value)} [Joules]."
                        )
                    else:
                        logger.warning(
                            f"{now}: Sense dades de producció per a la franja actual en mode {curr_mode}."
                        )

                    energy_consumed_pump_wh = P_bomba_watts * time_step_hours
                    logger.info(
                        f"{now}: Energia que consumiria la bomba si està ON: {fmt_joules(energy_consumed_pump_wh)} [Joules]."
                    )

                    if energy_consumed_pump_wh > 0:
                        COP = current_frame_prod_value / energy_consumed_pump_wh
                        logger.info(f"{now}: COP calculat: {COP}.")
                    else:
                        COP = 0
                        logger.warning(f"{now}: Energia consumida per la bomba és zero, COP=0.")

                    # Aquí podrías meter la condició real de COP si quieres
                    logger.info(f"curr_mode: {curr_mode}")
                    action = 1 if curr_mode != mode else 0
                    mode = curr_mode
                    Ttank = 1
                    logger.info(f"{now}: Bomba {'ON' if action == 1 else 'OFF'}. Ttank = {Ttank}.")
                    break

            l.append(found_active_time_frame)

            if not found_active_time_frame:
                action = 2
                logger.info(f"{now}: Hora actual fora de franges seleccionades. Bomba OFF.")

            if current_dem_target > 0:
                old_dem_target = current_dem_target
                current_dem_target = verify_and_adjust_demand(current_dem_target)
                logger.info(
                    f"{now}: Demanda restant ajustada de {fmt_joules(old_dem_target)} [Joules] "
                    f"a {fmt_joules(current_dem_target)} [Joules]."
                )
                if current_dem_target <= 0:
                    selected_time_frames = []
                    total_predicted_production = 0.0
                    current_dem_target = 0.0
                    action = 0
                    logger.info(f"{now}: Demanda coberta/esgotada. Reiniciant pla i bomba OFF.")
            else:
                action = 0
                logger.info(f"{now}: No hi ha demanda objectiu pendent. Bomba OFF.")

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

        es_nou_fitxer = not os.path.exists("dades.csv") or os.path.getsize("dades.csv") == 0
        capsaleres = [
            'timestamp', 'in_time_frame', 'current_dem_target',
            'action', 'mode', 'hot_prod', 'cold_prod', 'hot_dem', 'cold_dem'
        ]

        logger.info("Write data to dades.csv")
        try:
            with open('dades.csv', mode='a', newline='', encoding='utf-8') as fitxer:
                escriptor = csv.writer(fitxer)
                if es_nou_fitxer:
                    escriptor.writerow(capsaleres)
                escriptor.writerow(l)
        except Exception:
            logger.exception(f"{now}: Error escrivint al CSV dades.csv")

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
    global action, mode

    now = datetime.now().replace(microsecond=0)

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

        # 5) Lògica d'acció en temps real
        if total_predicted_production == -1 and current_dem_target > 0:
            # No hi ha producció suficient per cobrir la demanda actual
            l.append(0)
            action = 2
            logger.warning(f"{now}: Producció insuficient. Bomba OFF.")
        else:
            found_active_time_frame = False

            current_time_frame_start = now.replace(
                minute=now.minute - (now.minute % min_time_step),
                second=0,
                microsecond=0,
            )
            current_time_frame_end = current_time_frame_start + timedelta(
                minutes=min_time_step
            )
            logger.info(
                f"{now}: Franja horària actual: "
                f"{current_time_frame_start.isoformat(timespec='seconds')} "
                f"a {current_time_frame_end.isoformat(timespec='seconds')}"
            )

            # Recorrem les franges planificades (temp_mode) per veure si la franja actual és activa
            for time_frame_dt in temp_mode.keys():
                frame_end_dt = time_frame_dt + timedelta(minutes=min_time_step)
                if time_frame_dt <= now < frame_end_dt:
                    found_active_time_frame = True
                    logger.info(
                        f"{now}: Franja horària actual ({now.isoformat(timespec='seconds')}) "
                        f"activa per a l’operació."
                    )

                    current_frame_prod_value = 0.0
                    current_frame_str = time_frame_dt.isoformat(timespec='seconds')
                    curr_mode = "hot" if temp_mode[time_frame_dt] else "cold"

                    if curr_mode == "hot" and current_frame_str in prod_data['hot']:
                        current_frame_prod_value = float(prod_data['hot'][current_frame_str])
                        logger.info(
                            f"{now}: Producció calor prevista en aquesta franja: "
                            f"{fmt_joules(current_frame_prod_value)} [Joules]."
                        )
                    elif curr_mode == "cold" and current_frame_str in prod_data['cold']:
                        current_frame_prod_value = float(prod_data['cold'][current_frame_str])
                        logger.info(
                            f"{now}: Producció fred prevista en aquesta franja: "
                            f"{fmt_joules(current_frame_prod_value)} [Joules]."
                        )
                    else:
                        logger.warning(
                            f"{now}: Sense dades de producció per a la franja actual en mode {curr_mode}."
                        )

                    energy_consumed_pump_wh = P_bomba_watts * time_step_hours
                    logger.info(
                        f"{now}: Energia consumida per la bomba en aquesta franja: "
                        f"{fmt_joules(energy_consumed_pump_wh)} [Wh]."
                    )

                    COP = 0
                    if energy_consumed_pump_wh > 0:
                        COP = current_frame_prod_value / energy_consumed_pump_wh
                        logger.info(f"{now}: COP calculat: {COP:.2f}.")
                    else:
                        COP = 0
                        logger.warning(
                            f"{now}: Energia consumida per la bomba és zero, COP=0."
                        )

                    # --- DECISIÓN REAL CON COP ---
                    logger.info(f"curr_mode: {curr_mode}")

                    if COP >= COP_MIN and current_dem_target > 0:
                        # Buen rendimiento y aún hay demanda → accion = YES
                        action = 1   # 'yes'
                        Ttank = 1
                        logger.info(
                            f"{now}: COP >= {COP_MIN} i hi ha demanda. "
                            f"Bomba ON (action=1). Ttank = {Ttank}."
                        )
                    else:
                        # COP bajo o no hay demanda → accion = NO (pero no Parada dura)
                        action = 0   # 'no'
                        logger.info(
                            f"{now}: COP < {COP_MIN} o no hi ha demanda. "
                            f"Bomba OFF (action=0)."
                        )

                    # Actualizamos el mode solo para log (no para decidir YES/NO)
                    mode = curr_mode
                    break

            l.append(found_active_time_frame)

            if not found_active_time_frame:
                action = 2
                logger.info(f"{now}: Hora actual fora de franges seleccionades. Bomba OFF.")

            if current_dem_target > 0:
                old_dem_target = current_dem_target
                current_dem_target = verify_and_adjust_demand(current_dem_target)
                logger.info(
                    f"{now}: Demanda restant ajustada de {fmt_joules(old_dem_target)} [Joules] "
                    f"a {fmt_joules(current_dem_target)} [Joules]."
                )
                if current_dem_target <= 0:
                    selected_time_frames = []
                    total_predicted_production = 0.0
                    current_dem_target = 0.0
                    action = 0
                    logger.info(
                        f"{now}: Demanda coberta/esgotada. Reiniciant pla i bomba OFF."
                    )
            else:
                action = 0
                logger.info(f"{now}: No hi ha demanda objectiu pendent. Bomba OFF.")

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

        es_nou_fitxer = not os.path.exists("dades.csv") or os.path.getsize("dades.csv") == 0
        capsaleres = [
            'timestamp', 'in_time_frame', 'current_dem_target',
            'action', 'mode', 'hot_prod', 'cold_prod', 'hot_dem', 'cold_dem'
        ]

        logger.info("Write data to dades.csv")
        try:
            with open('dades.csv', mode='a', newline='', encoding='utf-8') as fitxer:
                escriptor = csv.writer(fitxer)
                if es_nou_fitxer:
                    escriptor.writerow(capsaleres)
                escriptor.writerow(l)
        except Exception:
            logger.exception(f"{now}: Error escrivint al CSV dades.csv")

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

# crearla en tu App
# plc = PLCController(ip="172.17.10.110", rack=0, slot=1)
plc = PLCController(ip="127.0.0.1", rack=0, slot=1)

mode_actions = {
    "hot": plc.set_heat_mode,
    "cold": plc.set_cold_mode,
    "parada": plc.set_parada_mode,
    "automatic": plc.set_automatic_mode,
}

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
    logger.info(f"system_data: {system_data}")

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

        # 1) Obtener decision de la logica de control
        respuesta_nn = get_decision()
        respuesta_nn = respuesta_nn["respuesta"]
        logger.info(f"nn_result: {respuesta_nn}")

        # 2) Leer estado actual del PLC
        estado_actual, combinaciones = plc.get_system_state()

        # 3) Calcular nuevo estado a partir de la respuesta de la IA
        nuevo_estado = plc.decide_next_state_from_nn(respuesta_nn, estado_actual)

        # 4) Escribir nuevo estado en el PLC
        plc.final_write_to_plc_nn_mode(nuevo_estado, combinaciones)

        # Espera entre iteraciones del bucle de control
        # sleep(60*15)  # version normal cada 15 minutos
        # sleep(5)        # version rapida para pruebas
