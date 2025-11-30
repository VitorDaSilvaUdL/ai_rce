import sys
import asyncio
import os
import random
import json
from datetime import datetime, timedelta
from time import sleep
import csv
from contextlib import asynccontextmanager

from sc.api_data.api_req import get_req, get_data
from sc.utils.getters import get_prod, get_dem, get_rain
from fastapi import FastAPI
from sc.utils.read_data import get_last_data_from_db
from .plc_controller import PLCController

from sc.config import (
    PREDICT_URL, P_BOMBA_WATTS, MIN_TIME_STEP,
    PLC_IP, PLC_RACK, PLC_SLOT, OUTPUT_CSV, ITER_TIME_SEC
)

import logging
logger = logging.getLogger(__name__)

logger.setLevel(logging.DEBUG)

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

plc = PLCController(ip=PLC_IP, rack=PLC_RACK, slot=PLC_SLOT)

prod_data = {}
dem_data = {}
rain_data = {}
temp_mode = {}

system_data = {}

last_prediction_update_day = None

current_dem_target = 0.0
selected_time_frames = []
total_predicted_production = 0.0

# Funció per calcular la demanda
def calculate_dem_for_period(df_demand, start_dt, end_dt):
    dem = 0.0
    current_dt = start_dt
    while current_dt < end_dt:
        dt_str = current_dt.replace(second=0, microsecond=0).isoformat(timespec='seconds')
        if dt_str in df_demand:
            dem += float(df_demand[dt_str])
        current_dt += timedelta(minutes=1)
    return dem


# Funció per calcular les franjes horaries optimes
def calculate_optimal_production_plan(available_prod, target_demand, type):
    """
    Calcula el plan óptimo de producción seleccionando las franjas horarias
    más eficientes para cubrir una demanda energética objetivo.

    Parámetros:
        available_prod (dict[str → float]):
            Diccionario {timestamp_iso: producción_prevista_wh}.
        target_demand (float):
            Energía total que se desea cubrir (Wh).
        type (int):
            Tipo de modo a asignar en cada franja seleccionada:
                1 = HOT,   0 = COLD.

    Funcionamiento:
        1. Ordena las franjas por producción ascendente (de menor a mayor).
        2. Va acumulando producción hasta alcanzar o superar la demanda objetivo.
        3. Convierte las franjas seleccionadas a objetos datetime.
        4. Asigna el modo (HOT/COLD) en el diccionario global `temp_mode`
           para cada franja seleccionada.
        5. Devuelve las franjas elegidas y la producción acumulada.

    Retorna:
        (list[datetime], float):
            - Lista de franjas seleccionadas.
            - Producción total acumulada.
        Si no se alcanza la demanda:
            ([], -1)
    """
    sorted_prod = dict(sorted(available_prod.items(), key=lambda item: item[1]))

    accumulated_production = 0.0
    selected_frames = []
    for time_frame_str, production_value in sorted_prod.items():
        accumulated_production += float(production_value)
        selected_frames.append(time_frame_str)
        if accumulated_production >= target_demand:
            break

    if accumulated_production < target_demand:
        return [], -1

    selected_frames_dt = [datetime.fromisoformat(ts) for ts in selected_frames]

    for time in selected_frames_dt:
        temp_mode[time] = type
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


# Funció de test
# @app.get("/now_on")
def get_now_on():
    now = datetime.now().replace(microsecond=0)
    print(selected_time_frames)
    for time_frame_dt in temp_mode.keys():
        print(f"Check {time_frame_dt} <= {now} < {time_frame_dt + timedelta(minutes=min_time_step)}")
        if time_frame_dt <= now < time_frame_dt + timedelta(minutes=min_time_step):
            return {"yes": "hot" if temp_mode[time_frame_dt] else "cold"}
    return "no"

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
                        f"{now}: Mode establert a HOT. Demanda calor objectiu: {current_dem_target:.2f} Wh. "
                        f"Producció prevista: {total_predicted_production:.2f} Wh."
                    )
                    if total_predicted_production == -1:
                        logger.warning(
                            f"{now}: No s’ha pogut trobar un pla òptim per a la demanda de calor. Demanda: {dem_c:.2f} Wh."
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
                        f"{now}: Mode establert a COLD. Demanda fred objectiu: {current_dem_target:.2f} Wh. "
                        f"Producció prevista: {total_predicted_production:.2f} Wh."
                    )
                    if total_predicted_production == -1:
                        logger.warning(
                            f"{now}: No s’ha pogut trobar un pla òptim per a la demanda de fred. Demanda: {dem_f:.2f} Wh."
                        )
                    else:
                        frames_str = [dt.isoformat(timespec='seconds') for dt in selected_time_frames]
                        logger.info(f"{now}: Franges horàries seleccionades per a fred: {frames_str}")
        except Exception:
            logger.exception(f"{now}: Error planificant demanda de fred.")

        logger.info(f"{now}: Lògica de control en temps real. Demanda objectiu actual: {current_dem_target:.2f} Wh.")

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
                            f"{now}: Producció calor prevista en aquesta franja: {current_frame_prod_value:.2f} Wh."
                        )
                    elif curr_mode == "cold" and current_frame_str in prod_data['cold']:
                        current_frame_prod_value = float(prod_data['cold'][current_frame_str])
                        logger.info(
                            f"{now}: Producció fred prevista en aquesta franja: {current_frame_prod_value:.2f} Wh."
                        )
                    else:
                        logger.warning(
                            f"{now}: Sense dades de producció per a la franja actual en mode {curr_mode}."
                        )

                    energy_consumed_pump_wh = P_bomba_watts * time_step_hours
                    logger.info(
                        f"{now}: Energia que consumiria la bomba si està ON: {energy_consumed_pump_wh:.2f} Wh."
                    )

                    if energy_consumed_pump_wh > 0:
                        COP = current_frame_prod_value / energy_consumed_pump_wh
                        logger.info(f"{now}: COP calculat: {COP:.2f}.")
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
                    f"{now}: Demanda restant ajustada de {old_dem_target:.2f} Wh "
                    f"a {current_dem_target:.2f} Wh."
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
        resp_labels = ["no", "yes", "parada"]
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
    
    if plc.alarm_active:
        logger.error("Alarma detectada → apagando sistema")
        plc.close_doors()  # si quieres
        plc.disconnect()
    
    sys.exit(1)

if __name__ == '__main__':

    # --- Planificació inicial de calor i fred ---
    now = datetime.now().replace(microsecond=0)

    print(f"{now}: Sistema iniciat. Carregant dades inicials.")
    
    system_data = get_last_data_from_db()
    print(f"system_data: {system_data}")

    stop()
   
    plc.connect()

    mode = plc.get_current_mode()
    print(f"Actual mode: {mode}")

    req = get_req(url, system_data)
    logger.debug(f"Request response data:{req}")
    dem_data = get_dem(req) #demanda
    prod_data = get_prod(req) #energy-production
    rain_data = get_rain(req) #rain-prediction

    logger.debug(f"dem_data:{req}")
    logger.debug(f"prod_data:{req}")
    logger.debug(f"rain_data:{req}")

    last_prediction_update_day = datetime.now().day
    print(f"{datetime.now()}: Prediccions inicials carregades. Dia de darrera actualització: {last_prediction_update_day}")

   
    # Calor
    print(f"{now}: Planificació inicial de la demanda de calor.")
    start_dem_heat = now.replace(hour=12, minute=0, second=0, microsecond=0)
    end_dem_heat = (now + timedelta(days=1)).replace(hour=12, minute=0, second=0, microsecond=0)
    dem_c = calculate_dem_for_period(dem_data['hot_dem'], start_dem_heat, end_dem_heat)

    selected_time_frames, total_predicted_production = calculate_optimal_production_plan(prod_data['hot'], dem_c, 1)
    print(f"sorted(selected_time_frames): {sorted(selected_time_frames)}")
    current_dem_target = dem_c

    if current_dem_target == 0:
        print(f"{now}: Model Establert a HOT: No hi ha demanda de calor en els propers dies.")
    else:
        print(f"{now}: Mode establert a HOT. Demanda de calor objectiu: {current_dem_target:.2f} Wh. Producció prevista per cobrir-la: {total_predicted_production:.2f} Wh.")
        if total_predicted_production == -1:
            print(f"{now}: No s’ha pogut trobar un pla òptim per a la demanda de calor. Demanda: {dem_c:.2f} Wh.")
        else:
            print(f"{now}: Franges horàries seleccionades per a calor: {[dt.isoformat(timespec='seconds') for dt in sorted(selected_time_frames)]}")

    print(f"{now}: Planificació inicial de la demanda de fred.")
    start_dem_cool = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    end_dem_cool = (now + timedelta(days=2)).replace(hour=0, minute=0, second=0, microsecond=0)
    dem_f = calculate_dem_for_period(dem_data['cold_dem'], start_dem_cool, end_dem_cool)

    selected_time_frames_cold, total_predicted_production_cold = calculate_optimal_production_plan(prod_data['cold'], dem_f,0)
    if dem_f == 0:
        print(f"{now}: No hi ha demanda de fred en els propers dies.")
    else:
        print(f"{now}: Demanda de fred estimada: {dem_f:.2f} Wh. Producció prevista per cobrir-la: {total_predicted_production_cold:.2f} Wh.")
        if total_predicted_production_cold == -1:
            print(f"{now}: No s’ha pogut trobar un pla òptim per a la demanda de fred. Demanda: {dem_f:.2f} Wh.")
        else:
            print(f"{now}: Franges horàries seleccionades per a fred (referència futura): {[dt.isoformat(timespec='seconds') for dt in sorted(selected_time_frames_cold)]}")
    

    # --- Bucle de control principal ---
    while True:
        logger.info(f"New cicle")
        respuesta_nn = get_decision()
        respuesta_nn = respuesta_nn["respuesta"]
        print(f"nn_result: {respuesta_nn}")

        # Se procesa la respuesta y se pone en minúsculas. 
        # EstadoDict = json.loads(nn_result)
        # respuesta = EstadoDict.get("respuesta", "").lower()

        # 1) Leer estado actual
        estado_actual, combinaciones = plc.get_system_state()

        # 2) Calcular nuevo estado
        nuevo_estado = plc.decide_next_state_from_nn(respuesta_nn, estado_actual)

        # 3) Escribir nuevo estado en PLC
        plc.final_write_to_plc_nn_mode(nuevo_estado, combinaciones)


        # print(f"{now}: Estat final de la bomba per a aquest cicle: {'ON' if action == 1 else 'OFF'}.")
        # sleep(60*15)
        sleep(5)
