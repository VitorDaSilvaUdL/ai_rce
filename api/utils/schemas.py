from typing import List

from pydantic import BaseModel


class Entry(BaseModel):
    cold: float
    hot: float
    reset_cold: float
    reset_hot: float
    mode: float
    wind_vel_m_s: float
    solar_rad_w_m2: float
    ir_rad_w_m2: float
    day_sin: float
    day_cos: float
    year_sin: float
    year_cos: float


class EntryList(BaseModel):
    data: List[Entry]