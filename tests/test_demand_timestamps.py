"""Hours of the demand forecast. get_forecast_24h() (api/rce_predictors/future/open.py) encodes each UTC hour of the
forecast as day_sin/day_cos/year_sin/year_cos, rounded to 6 decimals; the demand predictor turns them back into the
hour that labels each prediction. The old decoder truncated and gave some days one day later (on 2026-10-22 the hours
of 2026-10-23 came back as 2026-10-24, so the cold demand of "tomorrow" summed 0.0) and some hours one minute earlier
(09:59). sc plans with its local clock, so the labels are local time."""
import math
from datetime import datetime, timedelta, timezone

import pytest

import sc.main as main
from api.rce_predictors.future import timekeys


def encode_like_open_py(dt):
    """The arithmetic of get_forecast_24h(), copied: the inputs of the model must not change."""
    seconds_in_day = dt.hour * 3600 + dt.minute * 60
    day_frac = seconds_in_day / 86400
    day_of_year = dt.timetuple().tm_yday
    year_frac = day_of_year / 365.0
    return {"day_sin": round(math.sin(2 * math.pi * day_frac), 6), "day_cos": round(math.cos(2 * math.pi * day_frac), 6),
            "year_sin": round(math.sin(2 * math.pi * year_frac), 6), "year_cos": round(math.cos(2 * math.pi * year_frac), 6)}


def local(dt_utc):
    return dt_utc.replace(tzinfo=timezone.utc).astimezone().replace(tzinfo=None)


@pytest.mark.parametrize("year", [2026, 2027, 2028])
def test_every_hour_of_the_year_comes_back_as_itself(year):
    """All hours of non-leap and leap years (except 31 December of a leap year, day 366, which the encoding folds onto
    1 January)."""
    t, end, wrong = datetime(year, 1, 1), datetime(year, 12, 31), []
    while t < end:
        got = timekeys.decode_utc(**encode_like_open_py(t), ref=t - timedelta(hours=3))
        if got != t:
            wrong.append((t, got))
        t += timedelta(hours=1)
    assert wrong == []


def test_the_day_of_the_laboratory_failure():
    ref = datetime(2026, 10, 22, 9, 8)                                  # 11:08 in Lleida, UTC clock
    hours = [datetime(2026, 10, 22, 10) + timedelta(hours=i) for i in range(24)]
    got = [timekeys.decode_utc(**encode_like_open_py(h), ref=ref) for h in hours]
    assert got == hours
    assert sum(1 for g in got if g.date().isoformat() == "2026-10-23") == 10


def test_year_change():
    ref = datetime(2026, 12, 31, 20, 30)
    hours = [datetime(2026, 12, 31, 21) + timedelta(hours=i) for i in range(24)]
    assert [timekeys.decode_utc(**encode_like_open_py(h), ref=ref) for h in hours] == hours


def test_index_is_local_time():
    rows = [encode_like_open_py(datetime(2026, 10, 22, 10) + timedelta(hours=i)) for i in range(24)]
    idx = timekeys.local_index(rows, now_utc=datetime(2026, 10, 22, 9, 8))
    assert idx == [local(datetime(2026, 10, 22, 10) + timedelta(hours=i)) for i in range(24)]


def test_label_sums_two_utc_hours_that_share_a_local_hour():
    """End of summer time (Spain, 25 October 2026): 00:00 and 01:00 UTC are both 02:00 local; the demand of both is kept."""
    import pandas as pd
    hours = [datetime(2026, 10, 24, 23) + timedelta(hours=i) for i in range(4)]
    if local(hours[1]) != local(hours[2]):
        pytest.skip("the clock of this PC has no summer time change on that night")
    rows = [encode_like_open_py(h) for h in hours]
    df = pd.DataFrame({"cold_dem": [1.0, 2.0, 3.0, 4.0], "hot_dem": [10.0, 20.0, 30.0, 40.0]})
    out = timekeys.label(df, rows, now_utc=datetime(2026, 10, 24, 22, 30))
    assert list(out.index) == [local(hours[0]), local(hours[1]), local(hours[3])]
    assert list(out["cold_dem"]) == [1.0, 5.0, 4.0] and list(out["hot_dem"]) == [10.0, 50.0, 40.0]


def test_cold_demand_of_tomorrow_is_found_by_sc():
    """End to end with sc's own window sum: the hours of tomorrow are no longer lost."""
    rows = [encode_like_open_py(datetime(2026, 10, 22, 10) + timedelta(hours=i)) for i in range(24)]
    idx = timekeys.local_index(rows, now_utc=datetime(2026, 10, 22, 9, 8))
    series = {t.isoformat(timespec="seconds"): 1.3e6 for t in idx}
    tomorrow = datetime(2026, 10, 23)
    expected = sum(1 for t in idx if tomorrow <= t < tomorrow + timedelta(days=1)) * 1.3e6
    assert expected > 0
    assert main.calculate_dem_for_period(series, tomorrow, tomorrow + timedelta(days=1)) == pytest.approx(expected)


def test_demand_of_the_current_hour(monkeypatch):
    class FakeDT(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 10, 22, 11, 20)
    monkeypatch.setattr(main, "datetime", FakeDT)
    dem = {"2026-10-22T10:00:00": 4.0, "2026-10-22T11:00:00": 5.0, "2026-10-23T11:00:00": 6.0}
    assert main.get_now_val_2(dem) == 5.0
    assert main.get_now_val_2({"2026-10-22T12:00:00": 1.0}) == 0
