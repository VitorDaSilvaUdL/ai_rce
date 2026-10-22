import datetime
import os
import logging

import joblib
import pandas as pd
from api.rce_predictors.base_predictor import IDatedPredictor
import tensorflow as tf
import numpy as np
from api.rce_predictors.future.open import get_forecast_24h
from api.rce_predictors.future import timekeys

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

class DemandPredictor(IDatedPredictor):

    def __init__(self, demand_path: str, date_format: str) -> None:
        self._demand = None


    def predict(self) -> pd.DataFrame:
        logger.info("START demand prediction")
        parameters = pd.DataFrame(get_forecast_24h())
        X_scaled = x_scaler.transform(parameters)
        y_pred = evaluate_model(model, X_scaled, y_scaler)
        # each prediction labelled with its forecast hour, in local time (timekeys.py: the old decoding truncated and
        # gave half of the days one day later)
        df_y = timekeys.label(pd.DataFrame(y_pred, columns=columns), parameters.to_dict("records"))
        logger.info("END demand prediction")
        return df_y

if __name__ == "__main__":
    print(DemandPredictor.predict(None))