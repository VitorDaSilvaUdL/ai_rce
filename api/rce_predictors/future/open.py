import requests
from datetime import datetime, timedelta
import math
from api.rce_predictors.future.nasa import nasa_url

saved_ir = {}
last_update = None

def estimate_longwave_ir(temp_C, rh):
    """Estimació de radiació infraroja descendente (W/m²)"""
    Ta = temp_C + 273.15  # K
    ea = rh / 100 * 6.11 * 10**(7.5 * temp_C / (237.3 + temp_C))
    epsilon = 1.24 * (ea / Ta)**(1/7)
    sigma = 5.67e-8
    return epsilon * sigma * Ta**4

def get_forecast_24h():
    global saved_ir, last_update
    # lat, lon = 41.4, 2.2  #Preguntar porque se usa estas diferentes lat y lon
    
    # lat=41.6176
    # lon=0.6200

    # Lleida EPS
    lat=41.606527
    lon=0.623429
    
    now = datetime.utcnow()
    next_hour = (now + timedelta(hours=1)).replace(minute=0, second=0, microsecond=0)

    url = "https://api.open-meteo.com/v1/forecast"
    params = {
        "latitude": lat,
        "longitude": lon,
        "hourly": "temperature_2m,relative_humidity_2m,surface_pressure,windspeed_10m,shortwave_radiation",
        "forecast_days": 2,  # <-- Pedimos 2 días para asegurar 24h
        "timezone": "UTC"
    }
    data = requests.get(url, params=params, timeout=30).json()

    temps = data["hourly"]["temperature_2m"]
    hums = data["hourly"]["relative_humidity_2m"]
    pres = data["hourly"]["surface_pressure"]
    wind = data["hourly"]["windspeed_10m"]
    solar = data["hourly"]["shortwave_radiation"]
    times = [datetime.strptime(t, "%Y-%m-%dT%H:%M") for t in data["hourly"]["time"]]

    # índice de la próxima hora
    start_idx = next(i for i, t in enumerate(times) if t >= next_hour)
    n_hours = 24  # siempre queremos 24 horas
    ir_list = nasa_url()
    if len(ir_list) > 1:
        saved_ir = ir_list
        last_update = datetime.now().day
        with open("data.txt", "w") as text:
            text.write(str(ir_list))

    resultados = []
    print(f"\n VITOR times: {times} ")
    print(f"\n VITOR n_hours: {n_hours} ")
    print(f"\n VITOR start_idx: {start_idx} ")
    print(f"\n VITOR ir_list: {ir_list} ")

    for i in range(n_hours):
        dt = times[start_idx + i]
        temp = temps[start_idx + i]
        rh = hums[start_idx + i]

        # Decidir IR
        if (len(ir_list) == 1 and not saved_ir) or (last_update and last_update != datetime.now().day):
            ir = estimate_longwave_ir(temp, rh)
        elif len(ir_list) == 1 and saved_ir:
            ir = saved_ir
        else:
            ir = ir_list[start_idx + i]

        # Codificación cíclica
        seconds_in_day = dt.hour * 3600 + dt.minute * 60
        day_frac = seconds_in_day / 86400
        day_sin = math.sin(2 * math.pi * day_frac)
        day_cos = math.cos(2 * math.pi * day_frac)

        day_of_year = dt.timetuple().tm_yday
        year_frac = day_of_year / 365.0
        year_sin = math.sin(2 * math.pi * year_frac)
        year_cos = math.cos(2 * math.pi * year_frac)

        resultados.append({
            "temperature": round(temp,1),
            "humidity": round(rh,1),
            "pressure": round(pres[start_idx + i],1),
            "solar_rad": round(solar[start_idx + i],1),
            "ir_rad": round(ir,1),
            "v_wind": round(wind[start_idx + i],1),
            "day_sin": round(day_sin,6),
            "day_cos": round(day_cos,6),
            "year_sin": round(year_sin,6),
            "year_cos": round(year_cos,6)
        })

    return resultados

if __name__ == "__main__":
    datos = get_forecast_24h()
    for i, r in enumerate(datos):
        print(i+1 ,"->", r)
