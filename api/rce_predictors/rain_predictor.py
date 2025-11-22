import requests
import pytz
from datetime import datetime, timedelta
from enum import Enum
import pandas as pd
from api.rce_predictors.base_predictor import IDatedPredictor
import logging

logger = logging.getLogger(__name__)


def today(tzinfo: datetime.tzinfo = pytz.timezone("Etc/GMT-2")) -> datetime:
    return datetime.now(tz=tzinfo)


class WeatherPredictor(IDatedPredictor):

    class Period(Enum):
        TODAY_0200 = (0, "0200")
        TODAY_0800 = (0, "0800")
        TODAY_1400 = (0, "1400")
        TODAY_2000 = (0, "2000")
        TOMORROW_0200 = (1, "0200")
        TOMORROW_0800 = (1, "0800")
        TOMORROW_1400 = (1, "1400")
        TOMORROW_2000 = (1, "2000")

        def __init__(self, day_offset: int, hour_str: str):
            self.day_offset = day_offset
            self.hour_str = hour_str

        def to_datetime(self) -> datetime:
            base_date = today() + timedelta(days=self.day_offset)
            hour_val = int(self.hour_str[:2])
            return base_date.replace(hour=hour_val, minute=0, second=0, microsecond=0)

        def iso_format(self) -> str:
            return self.to_datetime().strftime("%Y-%m-%dT%H:00")

    def __init__(
        self,
        date_key: str = "last_update",
        date_format: str = "%Y-%m-%dT%H:%M:%SZ",
        url: str = "https://www.aemet.es/xml/municipios_h/localidad_h_25120.xml",
    ) -> "WeatherPredictor":
        self._date_key = date_key
        self._date_format = date_format
        self._url = url

    def _extern_api_req(self):
        
        # latitud = 41.3851
        # longitud = 2.1734

        latitud = 41.606527
        longitud = 0.623429

        url = (
            f"https://api.open-meteo.com/v1/forecast?"
            f"latitude={latitud}&longitude={longitud}&hourly=precipitation_probability"
            f"&timezone=Europe%2FMadrid"
        )

        try:
            response = requests.get(url)
            response.raise_for_status()
            data = response.json()

            if 'hourly' not in data:
                logger.warning("La respuesta de la API no contiene datos horarios")
                return []

            hours = data["hourly"]["time"]
            precipitation_prob = data["hourly"]["precipitation_probability"]

            today_str = datetime.now().strftime("%Y-%m-%d")
            tomorrow_str = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
            valid_hours = [2, 8, 14, 20]
            pred = []

            for i, time_str in enumerate(hours):
                date_part, hour_part = time_str.split("T")
                hour = int(hour_part.split(":")[0])
                if date_part in [today_str, tomorrow_str] and hour in valid_hours:
                    pred.append((f"{date_part}T{hour:02}:00", precipitation_prob[i]))

            logger.info(f"Predicción API obtenida correctamente")
            return pred

        except requests.exceptions.RequestException as e:
            logger.error(f"Error en la petición API: {e}")
            return []

    def get_future(self):
        try:
            prediction = self._extern_api_req()
            pred_map = dict(prediction)
            pred_dict = {p: float(pred_map.get(p.iso_format(), 0.0)) for p in WeatherPredictor.Period}
            return pred_dict
        except Exception as e:
            logger.error(f"Error al generar predicciones futuras: {e}")
            return {p: 0.0 for p in WeatherPredictor.Period}

    def predict(self) -> pd.DataFrame:
        pred = self.get_future()
        df = pd.DataFrame(
            data={"prediction": [pred.get(p) for p in WeatherPredictor.Period]},
            index=[p.to_datetime().strftime("%Y-%m-%d %H:%M") for p in WeatherPredictor.Period],
        )
        logger.info(f"DataFrame de predicciones generado")
        return df
