"""Hours of the hourly forecast used by the demand model.

get_forecast_24h() (open.py) encodes each UTC hour of the forecast as day_sin/day_cos/year_sin/year_cos, rounded to
6 decimals, and the demand predictor turns them back into the hour that labels each prediction. Truncating that
decoding (int) gave some days one day later (on 2026-10-22 the hours of 2026-10-23 came back as 2026-10-24, so the
cold demand of the next day summed 0.0 in sc) and some hours one minute earlier (09:59). Here the decoding rounds,
the year is the one closest to the time of the forecast, and the labels are local time, the clock that sc uses for
its planning windows and the production forecast uses for its keys.

Limit of the encoding (not of this module): day 366 of a leap year (31 December) is folded onto 1 January.
"""
import math
from datetime import datetime, timedelta, timezone


def decode_utc(day_sin, day_cos, year_sin, year_cos, ref):
    """UTC hour encoded by get_forecast_24h(). ref (UTC, naive) chooses the year: the candidate closest to it."""
    year_frac = (math.atan2(year_sin, year_cos) / (2 * math.pi)) % 1.0
    day_of_year = round(year_frac * 365) or 365
    day_frac = (math.atan2(day_sin, day_cos) / (2 * math.pi)) % 1.0
    minutes = round(day_frac * 1440) % 1440
    candidates = [datetime(y, 1, 1) + timedelta(days=day_of_year - 1, minutes=minutes)
                  for y in (ref.year - 1, ref.year, ref.year + 1)]
    return min(candidates, key=lambda c: abs(c - ref))


def utc_to_local(dt):
    """Naive UTC -> naive local time of this PC."""
    return dt.replace(tzinfo=timezone.utc).astimezone().replace(tzinfo=None)


def local_index(rows, now_utc=None):
    """Local hour of each forecast row (dicts or pandas rows with day_sin, day_cos, year_sin, year_cos)."""
    ref = now_utc or datetime.now(timezone.utc).replace(tzinfo=None)
    return [utc_to_local(decode_utc(r["day_sin"], r["day_cos"], r["year_sin"], r["year_cos"], ref)) for r in rows]


def label(df, rows, now_utc=None):
    """df (one row per forecast row) indexed by local hour. When summer time ends two UTC hours share one local
    hour: their values are added, so no demand is lost."""
    out = df.copy()
    out.index = local_index(rows, now_utc)
    return out.groupby(level=0, sort=False).sum()
