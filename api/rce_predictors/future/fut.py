from open import get_forecast_24h
from nasa import get_ir

if __name__ == '__main__':
    a = get_forecast_24h()
    b = get_ir()

    res = []
    for open_data, nasa_data in zip(a, b):
        time = open_data['time']
        wind_speed = open_data['wind_speed']
        solar_radiation = open_data['solar_radiation']
        radiation_infrared = nasa_data['predicted_radiation_infrared']

        res.append({'time': time, 'wind_speed':wind_speed, 'solar_radiation': solar_radiation, 'radiation_infrared': radiation_infrared})

    for n in res:
        print(n)