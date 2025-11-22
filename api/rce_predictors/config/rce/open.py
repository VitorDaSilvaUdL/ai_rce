import requests
from datetime import datetime, timedelta

def get_forecast_from_now_local():
    
    # # Coordenadas (ej. Barcelona)
    # latitude = 41.4
    # longitude = 2.2

    # Lleida EPS
    # 41.606527
    # 0.623429
    latitude = 41.606527
    longitude = 0.623429

    # Hora actual local
    now = datetime.now()
    end_time = now + timedelta(hours=2)

    # API forecast
    url = "https://api.open-meteo.com/v1/forecast"
    params = {
        "latitude": latitude,
        "longitude": longitude,
        "start_date": now.strftime("%Y-%m-%d"),
        "end_date": end_time.strftime("%Y-%m-%d"),
        "hourly": "wind_speed_10m,shortwave_radiation",
        "timezone": "auto"  # para recibir la hora local
    }

    response = requests.get(url, params=params)
    data = response.json()

    # Parsear tiempos ya en hora local
    times = [datetime.fromisoformat(t) for t in data["hourly"]["time"]]
    wind_speeds = data["hourly"]["wind_speed_10m"]
    solar_rads = data["hourly"]["shortwave_radiation"]

    # Construir lista de tuplas con datos horarios
    forecast = list(zip(times, wind_speeds, solar_rads))

    # Interpolar desde ahora hasta ahora + 2 horas en pasos de 15 minutos
    resultados = []
    t = now
    while t <= end_time:
        # Buscar dos puntos entre los cuales interpolar
        for i in range(len(forecast) - 1):
            t0, w0, r0 = forecast[i]
            t1, w1, r1 = forecast[i + 1]
            if t0 <= t <= t1:
                alpha = (t - t0).total_seconds() / (t1 - t0).total_seconds()
                wind = w0 + alpha * (w1 - w0)
                rad = r0 + alpha * (r1 - r0)
                resultados.append({
                    "time": t.strftime("%Y-%m-%d %H:%M"),
                    "wind_speed": round(wind, 2),
                    "solar_radiation": round(rad, 2)
                })
                break
        t += timedelta(minutes=15)

    return resultados

if __name__ == "__main__":
    get_forecast_from_now_local()
