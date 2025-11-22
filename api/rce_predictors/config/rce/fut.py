from .open import get_forecast_from_now_local
from .nasa import get_ir

def get_fut_val():
    a = get_forecast_from_now_local()
    b = get_ir()

    res = []
    for open_data, nasa_data in zip(a, b):
        time = open_data['time']
        wind_speed = open_data['wind_speed']
        solar_radiation = open_data['solar_radiation']
        radiation_infrared = nasa_data['predicted_radiation_infrared']

        res.append({'time': time, 'wind_speed':wind_speed, 'solar_radiation': solar_radiation, 'radiation_infrared': radiation_infrared})

    return res

if __name__ == '__main__':
    print(get_fut_val())