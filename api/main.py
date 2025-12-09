import csv
import os
import logging
from fastapi import FastAPI
import datetime
import tensorflow as tf
import pandas as pd
from logging.handlers import RotatingFileHandler
from api.rce_predictors.config.window_predictor import WindowPredictor
from api.rce_predictors.rain_predictor import WeatherPredictor
from api.rce_predictors.temperature_predictor import TemperaturePredictor
from api.utils.schemas import EntryList
from api.utils.out import output, structure
from api.utils import loaders
from api.rce_predictors.production_predictor import ProductionPredictor
from api.rce_predictors.demand_predictor import DemandPredictor
from api.rce_predictors.config.rce.specs import RceSpecs
app = FastAPI()

log_dir = os.path.join(os.getcwd(), "logs")
os.makedirs(log_dir, exist_ok=True)
log_file = os.path.join(log_dir, "data.log")

log_config = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "default": {
            "()": "logging.Formatter",
            "fmt": "%(asctime)s [%(levelname)s] %(name)s:%(lineno)d - %(message)s",
            "datefmt": "%Y-%m-%d %H:%M:%S",
        }
    },
    "handlers": {
        "file": {
            "class": "logging.handlers.RotatingFileHandler",
            "formatter": "default",
            "filename": log_file,
            "maxBytes": 5_000_000,
            "backupCount": 3,
            "level": "INFO",
        },
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "default",
            "level": "DEBUG",
        },
    },
    "loggers": {
        "uvicorn": {"handlers": ["file", "console"], "level": "INFO", "propagate": False},
        "uvicorn.error": {"handlers": ["file", "console"], "level": "INFO", "propagate": False},
        "uvicorn.access": {"handlers": ["file", "console"], "level": "INFO", "propagate": False},
        "main": {"handlers": ["file", "console"], "level": "DEBUG", "propagate": False},
    },
    "root": {"handlers": ["file", "console"], "level": "DEBUG"},
}

logger = logging.getLogger(__name__)
logger.info("Logger inicializado")


# Endpoint de predicció
@app.post("/predict")
def predict(data: EntryList):
    logger.info("Nova crida a la API")
    out = output.OutputBuilder()
    run_config = loaders.load_json()

    if len(data.data) != run_config["temperature"]["input"]:
        logger.warning("Datos de entrada con longitud inesperada")
        return out.add_exception(
            output.unexpected_data_length(
                actual=len(data.data),
                expected=run_config["temperature"]["input"],
                fetched=True,
            )
        ).build()

    _future = structure.future_times(
        datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
        run_config["temperature"]["output"],
    )

    # Variable que guarda els temps futurs de 15 min a 15 min
    _large_future = structure.future_times(
        datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
        192
    )

    # Part de predicció de temps atmosfèric
    _rain_predictor = rain_predictor()
    r_pred = _rain_predictor.predict()

    # Part de predicció de temperatura dels tancs
    logger.info(f"VITOR: entrada predictor: {data}")
    _temp_predictor = temp_predictor(run_config["temperature"])
    t_pred = _temp_predictor.predict(data, do_plot=False)

    # Part de predicció de demanda
    _dema_predictor = dema_predictor(run_config["demand"])
    d_pred = _dema_predictor.predict()

    # Part de predicció de producció
    _prod_predictor = prod_predictor(run_config["rce-specs"])

    df = pd.DataFrame([entry.dict() for entry in data.data])
    compare_row = pd.DataFrame(
        df[run_config["temperature"]["labels"]].iloc[-1].T.to_dict(),
        columns=run_config["temperature"]["labels"],
        index=[0],
    )
    p_pred = _prod_predictor.predict(
        pd.concat([compare_row, t_pred], ignore_index=True)
    ).round(decimals=2)

    # Construir el json resultant fusionant totes les prediccions
    try:
        out.add_data("rain-prediction", structure.rain(r_pred))\
           .add_data("demand", structure.dema(d_pred, _future))\
           .add_data("energy-production", structure.prod(
               p_pred, _large_future, run_config["temperature"]["labels"]
           ))\
           .add_data("tank-temperature", structure.temp(
               t_pred, _large_future,
               run_config["temperature"]["column-indices"],
               run_config["temperature"]["labels"]
           ))
        logger.info("Predicción completada correctamente")
    except Exception as e:
        logger.error(f"Error al construir la salida: {e}")
        out.add_exception(f"Error when building output: {e}")

    return out.build()

# Crear predictor de pluja
def rain_predictor() -> WeatherPredictor:
    return WeatherPredictor()

# Crear predictor de temperatura
def temp_predictor(temperature_config: dict) -> TemperaturePredictor:
    base_dir = os.path.dirname(os.path.abspath(__file__))
    return TemperaturePredictor(
        window_predictor=WindowPredictor(
            model=tf.keras.models.load_model(
                os.path.join(base_dir, "rce_predictors", "keras", temperature_config["model-name"]),
                compile=False
            ),
            input_width=int(temperature_config["input"]),
            label_width=int(temperature_config["output"]),
            shift=int(temperature_config["shift"]),
            column_indices=temperature_config["column-indices"],
            label_columns=temperature_config["labels"],
        ),
        postprocess_pipelines_filepath=os.path.join(
            "rce_predictors", "config", "config-preprocess.json"
        ),
    )

# Crear predictor de producci
def prod_predictor(rce_specs: dict) -> ProductionPredictor:
    return ProductionPredictor(RceSpecs(**rce_specs))

# Crear predictor de demanda
def dema_predictor(demand_config: dict) -> DemandPredictor:
    base_dir = os.path.dirname(os.path.abspath(__file__))
    return DemandPredictor(
        demand_path=os.path.join(base_dir, demand_config["filepath"]),
        date_format="%Y-%m-%d %H:%M",
    )


if __name__ == "__main__":
    import uvicorn

    logger.info("Inicialización API predictiva")
    uvicorn.run(app, host="localhost", port=8000, log_config=log_config)
