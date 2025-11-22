import os
from pathlib import Path

import requests
import json
import numpy as np
from datetime import datetime
import pandas as pd

headers = {
    "Content-Type": "application/json"
}

# def get_req2(url, data):
#     response = requests.post(url, json=data, headers=headers)
#     print(response.status_code)
#     return response.json()

def get_req(url, data, retries=3, timeout=30):
    for attempt in range(1, retries + 1):
        try:
            response = requests.post(url, json=data, headers=headers, timeout=timeout)

            # Imprimir código de estado
            print(f"[HTTP {response.status_code}] intento {attempt}/{retries}")

            # Respuesta correcta
            if response.ok:
                try:
                    return response.json()
                except ValueError:
                    print("Error: respuesta no es JSON válido.")
                    return None

            else:
                print(f"Servidor respondió con error HTTP {response.status_code}")

        except requests.exceptions.Timeout:
            print(f"Timeout en intento {attempt}/{retries}")

        except requests.exceptions.ConnectionError:
            print(f"Error de conexión en intento {attempt}/{retries}")

        except Exception as e:
            print(f"Error inesperado en intento {attempt}/{retries}: {e}")

    # Si se acabaron los reintentos
    print("No se pudo obtener respuesta del servidor.")
    return None


def get_data():
    basedir = Path(__file__).parent
    with open(basedir / "prova.json") as p:
        j = json.load(p)
    return j

def drop_columns_from_dicts(data: list) -> list:
    for elem in data:
        for key in ['day_of_week', 'year', 'Amb_temp_C', 'P_W', 'Sunrise_min', 'Sunset_min', 'Flow_rate_kg_h', 'In_RCE_temp_C', 'Out_RCE_temp_C']:
            if key in elem:
                del elem[key]
    return data

def rearrange_dict(data: list) -> list:
    new_l = []
    desired_order = [
        "cold",
        "hot",
        "wind_vel_m_s",
        "solar_rad_w_m2",
        "ir_rad_w_m2",
        "day_sin",
        "day_cos",
        "year_sin",
        "year_cos"
    ]
    for elem in data:
        ordered_data = {key: elem[key] for key in desired_order}
        new_l.append(ordered_data)
    return new_l
def process_data(data: list) -> list:
    min_year = min([elem['datetime'].year for elem in data])

    for elem in data:
        elem.pop("", None)

        seconds_in_day = 24 * 60 * 60
        time_seconds = elem['datetime'].hour * 3600 + elem['datetime'].minute * 60 + elem['datetime'].second
        elem['day_sin'] = np.sin(2 * np.pi * time_seconds / seconds_in_day)
        elem['day_cos'] = np.cos(2 * np.pi * time_seconds / seconds_in_day)

        elem['day_of_year'] = elem['datetime'].dayofyear
        elem['year_sin'] = np.sin(2 * np.pi * elem['day_of_year'] / 365)
        elem['year_cos'] = np.cos(2 * np.pi * elem['day_of_year'] / 365)

        elem['hot'] = float(elem.get('Hot_tank_temp_C', 0))  # Asegurarse de que el valor sea float
        elem['cold'] = float(elem.get('Cold_tank_temp_C', 0))  # Asegurarse de que el valor sea float
        elem['wind_vel_m_s'] = float(elem.get('Wind_vel_m_s', 0))  # Asegurarse de que el valor sea float
        elem['solar_rad_w_m2'] = float(elem.get('Solar_rad_W_m2', 0))  # Asegurarse de que el valor sea float
        elem['ir_rad_w_m2'] = float(elem.get('IR_rad_W_m2', 0))  # Asegurarse de que el valor sea float

        for key in ['Hot_tank_temp_C', 'Cold_tank_temp_C', 'Wind_vel_m_s', 'Solar_rad_W_m2', 'IR_rad_W_m2']:
            if key in elem:
                del elem[key]

    data = drop_columns_from_dicts(data)
    data = rearrange_dict(data)
    return data


def read_csv():
    import csv
    import json
    fecha_objetivo = datetime(1900, 7, 1, 16, 37, 0)
    n = 24

    df = pd.read_csv("cleansed.csv")
    df["datetime"] = pd.to_datetime(df["datetime"])

    df = df[df["datetime"] < fecha_objetivo].tail(24)

    df = df.to_dict(orient="records")

    df = process_data(df)
    out = {"data": df}
    json_data2 = json.dumps(out, indent=4)

    with open('test.json', 'w', encoding='utf-8') as json_file:
        json_file.write(json_data2)

if __name__ == '__main__':
    read_csv()