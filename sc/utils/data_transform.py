import pandas as pd


def unify_data(df: dict):
    dem = {}
    data = list(df.keys())
    for i in range(8):
        index = 15 * i
        curr_i = data[index]
        curr_v = None
        for j in range(15):
            if index + j >= len(data):
                break
            key = data[index + j]
            value = df[key]
            if curr_v is None or value > curr_v:
                curr_v = value
        dem[curr_i] = curr_v
    return dem


