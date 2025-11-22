import requests
from datetime import datetime, timedelta

def nasa_url():
    # Coordenadas para Madrid
    # lat = 40.4168
    # lon = -3.7038

    # Lleida EPS
    # 41.606527
    # 0.623429
    lat = 41.606527
    lon = 0.623429

    today = (datetime.now() - timedelta(days=365)).strftime("%Y%m%d")
    tomorrow = (datetime.now() - timedelta(days=364)).strftime("%Y%m%d")

    start = today
    end = tomorrow

    # Definir la URL para la API de NASA POWER
    url_nasa = f"https://power.larc.nasa.gov/api/temporal/daily/point?parameters=ALLSKY_SFC_LW_DWN&community=RE&longitude={lon}&latitude={lat}&start={start}&end={end}&format=JSON"

    # Hacer la solicitud a la API de NASA
    response_nasa = requests.get(url_nasa)

    # Verificar si la solicitud fue exitosa
    if response_nasa.status_code == 200:
        data_nasa = response_nasa.json()

        # Extraer y mostrar la radiación infrarroja
        if 'properties' in data_nasa:
            radiation_infrared = data_nasa['properties']['parameter']['ALLSKY_SFC_LW_DWN']

            # Crear una lista para almacenar los datos
            resultados_radiacion = []

            # Iterar sobre el diccionario 'radiation_infrared'
            for date, value in radiation_infrared.items():
                nd = datetime.strptime(date, "%Y%m%d") + timedelta(days=365)

                resultado = {
                    "date": nd.strftime("%Y%m%d"),
                    "radiation_infrared": value
                }
                resultados_radiacion.append(resultado)
            return resultados_radiacion
        else:
            print("No se encontraron los datos de radiación infrarroja en la respuesta de NASA.")
    else:
        print(f"Error al hacer la solicitud a NASA. Código de respuesta: {response_nasa.status_code}")

    return 0

def get_val(a):
    import numpy as np
    from datetime import datetime, timedelta

    mu = a['radiation_infrared']
    sigma = 0.5
    interval_minutes = 15
    num_intervals = 8

    now = datetime.now()

    predicciones = []

    for i in range(num_intervals):
        tiempo_pred = now + timedelta(minutes=i * interval_minutes)
        valor_simulado = np.random.normal(mu, sigma)
        predicciones.append({
            "datetime": tiempo_pred.strftime("%Y-%m-%d %H:%M"),
            "predicted_radiation_infrared": round(max(valor_simulado, 0), 2)  # No puede ser negativa
        })

    return predicciones

def get_ir():
    return get_val(nasa_url()[0])

if __name__ == "__main__":
    print(get_ir())