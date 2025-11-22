
import datetime
import os
import logging

import joblib
import pandas as pd
from api.rce_predictors.base_predictor import IDatedPredictor
import tensorflow as tf
import numpy as np
from api.rce_predictors.future.open import get_forecast_24h

GLOBAL_DATE_FORMAT = "%Y-%m-%d %H:%M"

logger = logging.getLogger(__name__)

y_path = os.path.join(os.path.dirname(__file__), r'keras/dem_y_scaler.pkl')
y_scaler = joblib.load(y_path)
x_path = os.path.join(os.path.dirname(__file__), r'keras/dem_x_scaler.pkl')
x_scaler = joblib.load(x_path)
model_path = os.path.join(os.path.dirname(__file__), r'keras/modelo_dem_fin.keras')
model = tf.keras.models.load_model(model_path, compile=False)
logger.info("Loaded demand model")

columns = ['cold_dem', 'hot_dem']

def _to_datetime(date):
    date, time = date.strip().split("  ")
    month, day = list(map(int, date.split("/")))
    hour, minute, _ = list(map(int, time.split(":")))
    return datetime.datetime(
        datetime.datetime.now().year,
        month=month,
        day=day,
    ) + datetime.timedelta(hours=hour, minutes=minute)

def evaluate_model(model, X_scaled, scaler_y):
    y_pred_scaled = model.predict(X_scaled)
    y_pred = scaler_y.inverse_transform(y_pred_scaled)

    for i in range(len(y_pred)):
        for j in range(len(y_pred[i])):
            if y_pred[i][j] < 0:
                y_pred[i][j] = 0

    return y_pred

def read_data():
    return pd.read_csv(os.path.join(os.path.dirname(__file__), r'keras/ex.txt'), sep=",", decimal=".")

def features_to_datetime(row, year=datetime.datetime.now().year):
    # Día del año a partir de year_sin, year_cos
    year_angle = np.arctan2(row['year_sin'], row['year_cos'])
    day_of_year = int((year_angle % (2*np.pi)) / (2*np.pi) * 365) + 1

    # Hora del día a partir de day_sin, day_cos
    day_angle = np.arctan2(row['day_sin'], row['day_cos'])
    hour_of_day = int((day_angle % (2*np.pi)) / (2*np.pi) * 24)
    minute_of_hour = int((((day_angle % (2*np.pi)) / (2*np.pi) * 24 - hour_of_day) * 60))

    # Construir datetime
    date = datetime.datetime(year, 1, 1) + datetime.timedelta(days=day_of_year-1, hours=hour_of_day, minutes=minute_of_hour)
    return date

class DemandPredictor(IDatedPredictor):

    def __init__(self, demand_path: str, date_format: str) -> None:
        self._demand = None


    def predict(self) -> pd.DataFrame:
        logger.info("START demand prediction")
        parameters = pd.DataFrame(get_forecast_24h())
        X_scaled = x_scaler.transform(parameters)
        y_pred = evaluate_model(model, X_scaled, y_scaler)
        df_y = pd.DataFrame(y_pred, columns=columns)
        index = [features_to_datetime(row, year=datetime.datetime.now().year)
             for _, row in parameters.iterrows()]
        df_y.index = index
        logger.info("END demand prediction")
        return df_y

if __name__ == "__main__":
    print(DemandPredictor.predict(None))