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


def fmt_joules(value: float) -> str:
    """
    Formatea Joules con separador de miles y versión abreviada.
    Ej:
        73866678.99 → "73,866,678.99 J (73.87M)"
    """
    # Parte 1: separador de miles
    formatted = f"{value:,.2f}"

    # Parte 2: abreviación
    abs_val = abs(value)
    if abs_val >= 1e9:
        short = f"{value/1e9:.2f}G"
    elif abs_val >= 1e6:
        short = f"{value/1e6:.2f}M"
    elif abs_val >= 1e3:
        short = f"{value/1e3:.2f}k"
    else:
        short = f"{value:.2f}"

    return f"{formatted} J ({short})"