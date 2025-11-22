# Run-time configuration of the application

from dataclasses import dataclass
from os import getenv

import pytz


def assert_msg(var) -> str:
    return f"{var} environment variable not set"


TIMEZONE = pytz.timezone("Etc/GMT-2")
GLOBAL_DATE_FORMAT = "%Y-%m-%d %H:%M"


SAVES_PATH = getenv("SAVES_PATH")
ROUND_PRECISION = int(getenv("ROUND_PRECISION"))

assert SAVES_PATH, assert_msg("SAVES_PATH")
assert ROUND_PRECISION, assert_msg("ROUND_PRECISION")


# ===========================================
# API information
# ===========================================


API_HOST = getenv("API_HOST")
API_PORT = getenv("API_PORT")

assert API_HOST, assert_msg("API_HOST")
assert API_PORT, assert_msg("API_PORT")


# ===========================================
# Redis information for the AEMET scraper
# ===========================================
REDIS_HOST = getenv("REDIS_HOST")
REDIS_PORT = getenv("REDIS_PORT")
REDIS_DATE_KEY = getenv("REDIS_DATE_KEY")
REDIS_DATE_FORMAT = getenv("REDIS_DATE_FORMAT")
AEMET_URL = "https://www.aemet.es/xml/municipios_h/localidad_h_25120.xml"

assert REDIS_HOST, assert_msg("REDIS_HOST")
assert REDIS_PORT, assert_msg("REDIS_PORT")
assert REDIS_DATE_KEY, assert_msg("REDIS_DATE_KEY")
assert REDIS_DATE_FORMAT, assert_msg("REDIS_DATE_FORMAT")


# ===========================================
# Influxdb information for the state API
# ===========================================
INFLUXDB_URL = getenv("INFLUXDB_URL")
INFLUXDB_TOKEN = getenv("INFLUXDB_TOKEN")
INFLUXDB_ORG = getenv("INFLUXDB_ORG")
INFLUXDB_BUCKET = getenv("INFLUXDB_BUCKET")

assert INFLUXDB_URL, assert_msg("INFLUXDB_URL")
assert INFLUXDB_TOKEN, assert_msg("INFLUXDB_TOKEN")
assert INFLUXDB_ORG, assert_msg("INFLUXDB_ORG")
assert INFLUXDB_BUCKET, assert_msg("INFLUXDB_BUCKET")


# ===========================================
# Runtime info path
# ===========================================
RUN_INFO_PATH = getenv("RUN_INFO_PATH")
assert RUN_INFO_PATH, assert_msg("RUN_INFO_PATH")
