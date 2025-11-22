import requests
from datetime import datetime, timedelta, timezone
import pytz
import math
from api.rce_predictors.future.open import get_forecast_24h
def demanda(lat=41.6176, lon=0.6200):
    """
    Obtiene predicciones de temperatura, humedad, presión y radiación solar
    cada hora durante 24h a partir de la próxima hora redondeada,
    y devuelve variables cíclicas day_sin, day_cos, year_sin, year_cos.
    """
    lat = 41.606527
    lon = 0.623429

    # Llamada a Open-Meteo incluyendo radiación solar
    url = (
        f"https://api.open-meteo.com/v1/forecast?"
        f"latitude={lat}&longitude={lon}&hourly="
        f"temperature_2m,relative_humidity_2m,pressure_msl,shortwave_radiation"
    )
    resp = requests.get(url).json()

    dem = get_forecast_24h()

    # Datos horarios
    horas = resp["hourly"]["time"]
    temps = resp["hourly"]["temperature_2m"]
    humedades = resp["hourly"]["relative_humidity_2m"]
    presiones = resp["hourly"]["pressure_msl"]
    radiacion = resp["hourly"]["shortwave_radiation"]

    # Hora actual en UTC
    ahora = datetime.now(timezone.utc)
    # Siguiente hora redondeada
    proxima_hora = (ahora + timedelta(hours=1)).replace(minute=0, second=0, microsecond=0)

    # Buscar índice de esa próxima hora en la lista de Open-Meteo
    start_idx = horas.index(proxima_hora.strftime("%Y-%m-%dT%H:00"))
    end_idx = start_idx + 24  # 24 horas desde esa hora

    # Zona horaria de España
    tz_spain = pytz.timezone("Europe/Madrid")

    resultados = []
    for i in range(start_idx, end_idx):
        dt_utc = datetime.fromisoformat(horas[i]).replace(tzinfo=timezone.utc)
        dt_local = dt_utc.astimezone(tz_spain)

        # --- Codificación cíclica ---
        # Hora del día
        seconds_in_day = dt_local.hour * 3600 + dt_local.minute * 60 + dt_local.second
        day_frac = seconds_in_day / 86400
        day_sin = math.sin(2 * math.pi * day_frac)
        day_cos = math.cos(2 * math.pi * day_frac)

        # Día del año
        day_of_year = dt_local.timetuple().tm_yday
        year_frac = day_of_year / 365.0
        year_sin = math.sin(2 * math.pi * year_frac)
        year_cos = math.cos(2 * math.pi * year_frac)

        resultados.append({
            "temp_C": round(temps[i], 1),
            "humedad_%": humedades[i],
            "presion_hPa": presiones[i],
            "radiacion_Wm2": round(radiacion[i], 1),
            "day_sin": day_sin,
            "day_cos": day_cos,
            "year_sin": year_sin,
            "year_cos": year_cos
        })
    print(dem[0], resultados[0])
    return resultados


def main():
    datos = demanda()
    print("Predicciones hora a hora (España) con codificación cíclica y radiación solar:\n")
    for d in datos:
        print(
            f"day_sin: {d['day_sin']:.3f}, day_cos: {d['day_cos']:.3f}, "
            f"year_sin: {d['year_sin']:.3f}, year_cos: {d['year_cos']:.3f} -> "
            f"Temp: {d['temp_C']} °C, Humedad: {d['humedad_%']} %, "
            f"Presión: {d['presion_hPa']} hPa, Radiación: {d['radiacion_Wm2']} W/m²"
        )


if __name__ == "__main__":
    main()
