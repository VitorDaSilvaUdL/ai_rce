import os
import joblib
import matplotlib as mpl
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import tensorflow as tf
from datetime import datetime, timedelta
from api.rce_predictors.base_predictor import IDatedPredictor
from api.rce_predictors.config.pipelines import MultiPipeline
from api.rce_predictors.config.window_predictor import WindowPredictor
from api.rce_predictors.config.rce.fut import get_fut_val
import logging


mpl.rcParams["figure.figsize"] = (8, 6)
mpl.rcParams["axes.grid"] = False

logger = logging.getLogger(__name__)

# Carreguem els escaladors guardats durant entrenament
y_path = os.path.join(os.path.dirname(__file__), r'keras/y_scaler.pkl')
y_scaler = joblib.load(y_path)
x_path = os.path.join(os.path.dirname(__file__), r'keras/x_scaler.pkl')
x_scaler = joblib.load(x_path)
logger.info("Loaded scalers")


class TemperaturePredictor(IDatedPredictor):
    def __init__(
        self,
        window_predictor: WindowPredictor,
        postprocess_pipelines_filepath: str,
    ):
        self._predictor = window_predictor
        self._postprocess_pipelines = MultiPipeline().load_config(
            postprocess_pipelines_filepath
        )

    def __repr__(self) -> str:
        return "\n\t".join(
            [
                f"Class: {self.__class__.__name__}",
                f"{self._predictor}",
            ]
        )

    def predict(
        self,
        parameters: pd.DataFrame,
        do_plot=False,
        plot_path="",
    ):
        df = pd.DataFrame([entry.dict() for entry in parameters.data])
        input_width = self._predictor.input_width
        label_columns = self._predictor.label_columns

        current_window = df.iloc[-input_width:].copy()

        original_shape = current_window.shape
        current_scaled = x_scaler.transform(current_window.values.reshape(-1, original_shape[1]))
        current_window = pd.DataFrame(
            current_scaled.reshape(original_shape),
            columns=df.columns
        )

        fut_val = get_fut_val()

        predictions = []
        date = datetime.now()

        logger.info("START Temperature Prediction")

        for i in range(192):
            input_array = np.expand_dims(current_window.values, axis=0)
            tensor = tf.convert_to_tensor(input_array, dtype=tf.float32)

            pred = self._predictor.model.predict(tensor, verbose=0).squeeze()
            predictions.append(pred)
            new_row = current_window.iloc[-1].copy()
            for i, col in enumerate(label_columns):
                new_row[col] = pred[i]

            curr = fut_val[i]
            year = date.year
            seconds_in_day = 24 * 60 * 60
            time_seconds = date.hour * 3600 + date.minute * 60 + date.second
            new_row["solar_rad_w_m2"] = curr["solar_radiation"]
            new_row["ir_rad_w_m2"] = curr["radiation_infrared"]
            new_row["wind_vel_m_s"] = curr["wind_speed"]
            new_row["day_sin"] = np.sin(2 * np.pi * time_seconds / seconds_in_day)
            new_row["day_cos"] = np.cos(2 * np.pi * time_seconds / seconds_in_day)
            new_row["year_sin"] = np.sin(2 * np.pi * year / 365)
            new_row["year_cos"] = np.cos(2 * np.pi * year / 365)
            new_row["mode"] = parameters.data[0].mode
            new_row["reset_cold"] = parameters.data[0].reset_cold
            new_row["reset_hot"] = parameters.data[0].reset_hot
            date = date + timedelta(minutes=15)
            current_window = pd.concat([current_window, pd.DataFrame([new_row])], ignore_index=True)
            current_window = current_window.iloc[1:]

        predictions = np.array(predictions)
        pred_desnormalitzades = y_scaler.inverse_transform(predictions)
        df_preds = pd.DataFrame(pred_desnormalitzades, columns=label_columns)
        print(df_preds["hot"])

        s = df_preds['hot']
        df_preds['hot'] = s[0] + 2.75 * (s - s[0])

        return df_preds


def load(json_path):
    import json
    with open(json_path, "r") as f:
        return json.load(f)
