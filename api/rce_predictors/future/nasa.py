import requests
import numpy as np
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

def nasa_url():
    # Coordenadas para Madrid
    # lat = 40.4168
    # lon = -3.7038

    # Lleida EPS
    # 41.606527
    # 0.623429
    lat = 41.606527
    lon = 0.623429

    # # Desde hoy hasta mañana
    # today = (datetime.now() + timedelta(hours=1)).strftime("%Y%m%d")
    # tomorrow = (datetime.now() + timedelta(days=1)).strftime("%Y%m%d")

     # Fecha actual en UTC
    now_utc = datetime.now(timezone.utc)
    now_madrid = datetime.now(ZoneInfo("Europe/Madrid"))

    # Base: hace 365 días en MADRID
    base_date = (now_madrid - timedelta(days=365)).date()

    # Siguiente día
    next_date = base_date + timedelta(days=1)

    today = base_date.strftime("%Y%m%d")
    tomorrow = next_date.strftime("%Y%m%d")

    url_nasa = f"https://power.larc.nasa.gov/api/temporal/daily/point?parameters=ALLSKY_SFC_LW_DWN&community=RE&longitude={lon}&latitude={lat}&start={today}&end={tomorrow}&format=JSON"

    response_nasa = requests.get(url_nasa)
    if response_nasa.status_code == 200:
        data_nasa = response_nasa.json()
        if 'properties' in data_nasa and 'parameter' in data_nasa['properties']:
            radiation_infrared = data_nasa['properties']['parameter']['ALLSKY_SFC_LW_DWN']
            resultados_radiacion = []
            for date, value in radiation_infrared.items():
                nd = datetime.strptime(date, "%Y%m%d")
                resultados_radiacion.append({
                    "date": nd.strftime("%Y-%m-%d"),
                    "radiation_infrared": value
                })
            return resultados_radiacion
        else:
            print("No se encontraron datos de radiación infrarroja.")
    else:
        print(f"Error en la solicitud a NASA: {response_nasa.status_code}")
    return []

def get_val(daily_data, interval_hours=1):
    now = datetime.now()
    end_time = datetime.now() + timedelta(days=1)
    predicciones = []

    while now < end_time:
        # Obtener el valor del día actual
        day_value = next((d['radiation_infrared'] for d in daily_data if d['date'] == now.strftime("%Y-%m-%d")), 0)
        valor_simulado = np.random.normal(day_value, 0.5)
        predicciones.append({
            "datetime": now.strftime("%Y-%m-%d %H:%M"),
            "predicted_radiation_infrared": round(max(valor_simulado, 0), 2)
        })
        now += timedelta(hours=interval_hours)

    return predicciones

if __name__ == "__main__":
    daily_data = nasa_url()
    predicciones = get_val(daily_data)
    for p in predicciones:
        print(p)
