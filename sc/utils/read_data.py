from datetime import datetime
from sc.api_data.api_req import get_data
import pandas as pd
import numpy as np
import json

lect_dir = r"C:\Users\Usuari\Documents\RCE\lecturas0.csv"
solar_dir = r"C:\Users\Usuari\Documents\RCE\Solarimeter0.csv"
ir_dir = r"C:\Users\Usuari\Documents\RCE\Pyrgeometer0.csv"

# lect_dir = r"C:\Users\PcVIP\OneDrive - udl.cat\Investigacion\CREA\Sistema_Control_Segarra_Riu_Ramon\SC_API-master\CSVs\Lecturas0.csv"
# solar_dir = r"C:\Users\PcVIP\OneDrive - udl.cat\Investigacion\CREA\Sistema_Control_Segarra_Riu_Ramon\SC_API-master\CSVs\Solarimeter0.csv"
# ir_dir = r"C:\Users\PcVIP\OneDrive - udl.cat\Investigacion\CREA\Sistema_Control_Segarra_Riu_Ramon\SC_API-master\CSVs\Pyrgeometer0.csv"

def get_indices(dfn, n, size):
    last = len(dfn) - 1
    return [last - 3* k * size - n for k in range(24) if last - k * size - n >= 0]

def get_mode(t):
    from datetime import time

    hora_inicio = time(8, 0)  # 7:00
    hora_fin = time(19, 0)

    return "1" if hora_inicio.hour <= t.hour < hora_fin.hour else "2"

def get_dict(df, n, size):
    val = get_indices(df, n, size)
    subset = df.iloc[val].sort_index()
    return dict(zip(subset["TimeString"], subset["VarValue"]))

def safe_float(value, default=np.nan):
    """
    Convierte distintos tipos (str con coma, float, int, vacío, NaN, etc.)
    en float de forma segura. Si no se puede, devuelve 'default'.
    """
    if value is None:
        return default

    # Si ya es numérico (float, int, np.number...)
    if isinstance(value, (int, float, np.floating, np.integer)):
        return float(value)

    # Si es string u otro tipo
    try:
        s = str(value).strip()
        if s == "" or s.lower() in {"nan", "none"}:
            return default
        s = s.replace(",", ".")
        return float(s)
    except Exception:
        return default
    
def get_last_data_from_db():
    df = pd.read_csv(lect_dir, sep=";",low_memory=False)
    df2 = pd.read_csv(solar_dir, sep=";",low_memory=False)
    df3 = pd.read_csv(ir_dir, encoding="latin-1", sep=";",low_memory=False)

    diccionario = get_dict(df, 29, 47)
    diccionario_2 = get_dict(df, 26, 47)
    diccionario_3 = get_dict(df, 25, 47)
    diccionario_4 = get_dict(df2, 0, 3)
    diccionario_5 = get_dict(df3, 0, 9)

    general = {
        "hot": diccionario,
        "cold": diccionario_2,
        "v_vent": diccionario_3,
        "solar": diccionario_4,
        "ir": diccionario_5
    }

    seconds_in_day = 24 * 60 * 60

    ret = {}
    l = []
    for h in general["cold"].keys():
        curr = {}
        hd = datetime.strptime(h, "%d/%m/%Y %H:%M:%S")
        time_seconds = hd.hour * 3600 + hd.minute * 60 + hd.second
        doy = hd.timetuple().tm_yday

        # curr["cold"] = float(general["cold"][h].replace(",", "."))
        # curr["hot"] = float(general["hot"][h].replace(",", "."))
        # curr["wind_vel_m_s"] = float(general["v_vent"][h].replace(",", "."))
        # curr["solar_rad_w_m2"] = float(general["solar"][h].replace(",", "."))
        # curr["ir_rad_w_m2"] = float(general["ir"][h].replace(",", "."))

        curr["cold"] = safe_float(general["cold"].get(h))
        curr["hot"] = safe_float(general["hot"].get(h))
        curr["wind_vel_m_s"] = safe_float(general["v_vent"].get(h))
        curr["solar_rad_w_m2"] = safe_float(general["solar"].get(h))
        curr["ir_rad_w_m2"] = safe_float(general["ir"].get(h))

        curr["reset_cold"] = 7
        curr["reset_hot"] = 16
        curr["mode"] = get_mode(hd)
        curr["day_sin"] = np.sin(2 * np.pi * time_seconds / seconds_in_day)
        curr["day_cos"] = np.cos(2 * np.pi * time_seconds / seconds_in_day)
        curr["year_sin"] = np.sin(2 * np.pi * doy / 365)
        curr["year_cos"] = np.cos(2 * np.pi * doy / 365)
        l.append(curr)
    ret["data"] = l
    return ret

if __name__ == "__main__":
    import json
    ret = get_last_data_from_db()
    with open("prova.json", "w", encoding="utf-8") as f:
        json.dump(ret, f, indent=4, ensure_ascii=False)