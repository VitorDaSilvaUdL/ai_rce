"""Functions to parse the output predictions"""

import datetime


def future_times(init_date: str, quarters: int) -> list[str]:
    init_dt = datetime.datetime.strptime(init_date, "%Y-%m-%d %H:%M")

    return [
        (init_dt + datetime.timedelta(minutes=i * 15)).strftime("%Y-%m-%d %H:%M")
        for i in range(quarters)
    ]


def rain(
    rain_pred,
) -> dict:
    return rain_pred


def temp(
    temp_pred,
    future: list[str],
    column_info: dict,
    temperature_label_columns: list[str],
) -> dict:
    tem = {
        k: {
            time: str(temp_pred.iloc[time_index][int(column_info[k])])
            for time_index, time in enumerate(future)
        }
        for k in ["hot", "cold"]
    }
    return tem


def prod(
    prod_pred,
    future: list[str],
    temperature_label_columns: list[str],
) -> dict:
    return {
        k: {
            time: str(prod_pred.iloc[time_index][k])
            for time_index, time in enumerate(future)
        }
        for k in temperature_label_columns
    }


def dema(d_pred, future: list[str]) -> dict:
    return d_pred.to_dict()
