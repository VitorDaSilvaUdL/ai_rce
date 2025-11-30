from datetime import datetime
from sc.api_data.api_req import get_data
import pandas as pd
import numpy as np
import json
import logging
logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)

# lect_dir = r"C:\Users\Usuari\Documents\RCE\lecturas0.csv"
# solar_dir = r"C:\Users\Usuari\Documents\RCE\Solarimeter0.csv"
# ir_dir = r"C:\Users\Usuari\Documents\RCE\Pyrgeometer0.csv"

lect_dir = r"C:\Users\PcVIP\OneDrive - udl.cat\Investigacion\CREA\Sistema_Control_Segarra_Riu_Ramon\SC_API-master\CSVs\Lecturas0.csv"
solar_dir = r"C:\Users\PcVIP\OneDrive - udl.cat\Investigacion\CREA\Sistema_Control_Segarra_Riu_Ramon\SC_API-master\CSVs\Solarimeter0.csv"
ir_dir = r"C:\Users\PcVIP\OneDrive - udl.cat\Investigacion\CREA\Sistema_Control_Segarra_Riu_Ramon\SC_API-master\CSVs\Pyrgeometer0.csv"

def get_indices(dfn, n, size):
    last = len(dfn) - 1
    return [last - 3* k * size - n for k in range(24) if last - k * size - n >= 0]

def get_mode(t):
    from datetime import time

    hora_inicio = time(8, 0)  # 7:00
    hora_fin = time(19, 0)

    return "1" if hora_inicio.hour <= t.hour < hora_fin.hour else "2"

# def get_dict(df, n, size):
#     val = get_indices(df, n, size)
#     subset = df.iloc[val].sort_index()
#     return dict(zip(subset["TimeString"], subset["VarValue"]))

def get_dict(df, n, size):
    """
    Extrae un subconjunto de filas según índices y devuelve un dict
    {TimeString -> VarValue}.
    """
    logger.debug(f"get_dict: n={n}, size={size}, df.shape={df.shape}")
    val = get_indices(df, n, size)
    logger.debug(f"get_dict: índices seleccionados={val}")
    subset = df.iloc[val].sort_index()
    # logger.debug(
    #     "get_dict: subset.shape=%s, primeras filas:\n%s",
    #     subset.shape,
    #     subset.head().to_string()
    # )
    logger.debug(
        "get_dict: subset.shape=%s, todas filas:\n%s",
        subset.shape,
        subset.to_string()
    )
    result = dict(zip(subset["TimeString"], subset["VarValue"]))
    logger.debug(f"get_dict: tamaño del diccionario resultante={len(result)}")
    return result

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
    
def get_last_data_from_db_ramon():
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

def get_last_data_from_db_legacy():
    logger.debug("get_last_data_from_db: leyendo CSVs")
    df = pd.read_csv(lect_dir, sep=";", low_memory=False)
    df2 = pd.read_csv(solar_dir, sep=";", low_memory=False)
    df3 = pd.read_csv(ir_dir, encoding="latin-1", sep=";", low_memory=False)

    logger.debug(f"get_last_data_from_db: df.shape={df.shape}")
    logger.debug(f"get_last_data_from_db: df2.shape={df2.shape}")
    logger.debug(f"get_last_data_from_db: df3.shape={df3.shape}")

    diccionario   = get_dict(df, 29, 47) #TempT6_RCEa
    diccionario_2 = get_dict(df, 26, 47) #TempT9_RCEa
    diccionario_3 = get_dict(df, 25, 47) #VelVent_RCEa
    diccionario_4 = get_dict(df2, 0, 3) #IO_SENSOR1_DATA_RCEa
    diccionario_5 = get_dict(df3, 0, 9) #E_FIR, neto, [W/m2]_RCEb 

    logger.debug(f"diccionario hot: len={len(diccionario)}")
    logger.debug(f"diccionario cold: len={len(diccionario_2)}")
    logger.debug(f"diccionario v_vent: len={len(diccionario_3)}")
    logger.debug(f"diccionario solar: len={len(diccionario_4)}")
    logger.debug(f"diccionario ir: len={len(diccionario_5)}")

    general = {
        "hot": diccionario,
        "cold": diccionario_2,
        "v_vent": diccionario_3,
        "solar": diccionario_4,
        "ir": diccionario_5
    }

    seconds_in_day = 86400
    ret = {}
    l = []

    logger.debug(f"get_last_data_from_db: número de claves en 'cold'={len(general['cold'])}")

    for i, h in enumerate(general["cold"].keys()):
        curr = {}
        logger.debug(f"Iteración {i}, timestamp={h}")

        hd = datetime.strptime(h, "%d/%m/%Y %H:%M:%S")
        time_seconds = hd.hour * 3600 + hd.minute * 60 + hd.second
        doy = hd.timetuple().tm_yday

        # Valores de entrada originales
        raw_cold  = general["cold"].get(h)
        raw_hot   = general["hot"].get(h)
        raw_vv    = general["v_vent"].get(h)
        raw_solar = general["solar"].get(h)
        raw_ir    = general["ir"].get(h)

        logger.debug(
            "Valores crudos en h=%s: cold=%r, hot=%r, v_vent=%r, solar=%r, ir=%r",
            h, raw_cold, raw_hot, raw_vv, raw_solar, raw_ir
        )

        curr["cold"]           = safe_float(raw_cold)
        curr["hot"]            = safe_float(raw_hot)
        curr["wind_vel_m_s"]   = safe_float(raw_vv)
        curr["solar_rad_w_m2"] = safe_float(raw_solar)
        curr["ir_rad_w_m2"]    = safe_float(raw_ir)

        curr["reset_cold"] = 7
        curr["reset_hot"] = 16
        curr["mode"] = get_mode(hd)
        curr["day_sin"] = np.sin(2 * np.pi * time_seconds / seconds_in_day)
        curr["day_cos"] = np.cos(2 * np.pi * time_seconds / seconds_in_day)
        curr["year_sin"] = np.sin(2 * np.pi * doy / 365)
        curr["year_cos"] = np.cos(2 * np.pi * doy / 365)

        logger.debug(
            "Curr en h=%s: cold=%s, hot=%s, wind=%s, solar=%s, ir=%s, mode=%s",
            h,
            curr["cold"],
            curr["hot"],
            curr["wind_vel_m_s"],
            curr["solar_rad_w_m2"],
            curr["ir_rad_w_m2"],
            curr["mode"],
        )

        l.append(curr)

        # Para no explotar el log, puedes cortar tras X iteraciones de debug:
        # if i > 20:
        #     logger.debug("... más filas omitidas en log ...")
        #     break

    ret["data"] = l
    logger.debug(f"get_last_data_from_db: total registros en ret['data']={len(l)}")
    return ret



# ---------------------------------------------------------------------------
# Mapa lógico de variables -> VarName(s) reales + CSV donde buscar
#   source: ruta completa al CSV
#   varnames: lista de posibles nombres en la columna VarName
# ---------------------------------------------------------------------------
VARIABLE_SOURCES = {
    "hot": {
        "varnames": ["TempT6_RCEa", "TempT6_RCEa_v2"],
        "source": lect_dir,   # temperatura caliente
    },
    "cold": {
        "varnames": ["TempT9_RCEa", "TempT9_RCEa_v2"],
        "source": lect_dir,   # temperatura fría
    },
    "v_vent": {
        "varnames": ["VelVent_RCEa", "VelVent_RCEa_v2"],
        "source": lect_dir,   # velocidad viento
    },
    "solar": {
        "varnames": ["IO_SENSOR1_DATA_RCEa"],
        "source": solar_dir,  # radiación solar
    },
    "ir": {
        "varnames": ["E_FIR, neto, [W/m2]_RCEb"],
        "source": ir_dir,     # radiación IR neta
    },
}

def get_last_data_from_db():
    logger.info("get_last_data_from_db: leyendo CSVs")
    df = pd.read_csv(lect_dir, sep=";", low_memory=False)
    df2 = pd.read_csv(solar_dir, sep=";", low_memory=False)
    df3 = pd.read_csv(ir_dir, encoding="latin-1", sep=";", low_memory=False)

    logger.debug(f"get_last_data_from_db: df.shape={df.shape}")
    logger.debug(f"get_last_data_from_db: df2.shape={df2.shape}")
    logger.debug(f"get_last_data_from_db: df3.shape={df3.shape}")

    # Cache de dataframes por ruta (source en VARIABLE_SOURCES)
    df_cache = {
        lect_dir: df,
        solar_dir: df2,
        ir_dir: df3,
    }

    # Construir diccionarios por VarName usando VARIABLE_SOURCES
    general = {}
    for logical_name, cfg in VARIABLE_SOURCES.items():
        src_path = cfg["source"]
        varnames = cfg["varnames"]

        df_src = df_cache[src_path]

        logger.debug(
            "Construyendo diccionario para '%s' desde CSV='%s' con varnames=%s",
            logical_name,
            src_path,
            varnames,
        )
        general[logical_name] = build_var_dict_from_names(df_src, varnames, n_samples=24)
        logger.debug(
            "Diccionario '%s': len=%d",
            logical_name,
            len(general[logical_name]),
        )

    seconds_in_day = 24 * 60 * 60
    ret = {}
    l = []

    # Usamos las claves de 'cold' como referencia temporal
    cold_keys = list(general["cold"].keys())
    logger.debug(f"get_last_data_from_db: número de claves en 'cold'={len(cold_keys)}")

    for i, h in enumerate(cold_keys):
        curr = {}
        logger.debug(f"Iteración {i}, timestamp={h}")

        hd = datetime.strptime(h, "%d/%m/%Y %H:%M:%S")
        time_seconds = hd.hour * 3600 + hd.minute * 60 + hd.second
        doy = hd.timetuple().tm_yday

        raw_cold = general["cold"].get(h)
        raw_hot = general["hot"].get(h)
        raw_vv = general["v_vent"].get(h)
        raw_solar = general["solar"].get(h)
        raw_ir = general["ir"].get(h)

        logger.debug(
            "Valores crudos en h=%s: cold=%r, hot=%r, v_vent=%r, solar=%r, ir=%r",
            h,
            raw_cold,
            raw_hot,
            raw_vv,
            raw_solar,
            raw_ir,
        )

        curr["cold"] = safe_float(raw_cold)
        curr["hot"] = safe_float(raw_hot)
        curr["wind_vel_m_s"] = safe_float(raw_vv)
        curr["solar_rad_w_m2"] = safe_float(raw_solar)
        curr["ir_rad_w_m2"] = safe_float(raw_ir)

        curr["reset_cold"] = 7
        curr["reset_hot"] = 16
        curr["mode"] = get_mode(hd)
        curr["day_sin"] = np.sin(2 * np.pi * time_seconds / seconds_in_day)
        curr["day_cos"] = np.cos(2 * np.pi * time_seconds / seconds_in_day)
        curr["year_sin"] = np.sin(2 * np.pi * doy / 365)
        curr["year_cos"] = np.cos(2 * np.pi * doy / 365)

        logger.debug(
            "Curr en h=%s: cold=%s, hot=%s, wind=%s, solar=%s, ir=%s, mode=%s",
            h,
            curr["cold"],
            curr["hot"],
            curr["wind_vel_m_s"],
            curr["solar_rad_w_m2"],
            curr["ir_rad_w_m2"],
            curr["mode"],
        )

        l.append(curr)

    ret["data"] = l
    logger.debug(f"get_last_data_from_db: total registros en ret['data']={len(l)}")
    return ret

def build_var_dict_from_names(df: pd.DataFrame, varnames, n_samples: int = 24):
    if isinstance(varnames, str):
        varnames = [varnames]

    logger.debug(f"build_var_dict_from_names: varnames={varnames}, n_samples={n_samples}")

    if "VarName" not in df.columns:
        logger.warning("build_var_dict_from_names: columna 'VarName' no encontrada en df")
        return {}

    mask = df["VarName"].isin(varnames)
    subset = df[mask]

    # 🔹 Ordenar por tiempo creciente
    if "Time_ms" in subset.columns:
        subset = subset.sort_values("Time_ms")
    else:
        subset = subset.sort_index()

    # 🔹 QUEDARSE CON LOS ÚLTIMOS n_samples (LOS MÁS RECIENTES)
    subset = subset.tail(n_samples)

    # logger.debug(
    #     "build_var_dict_from_names: subset final shape=%s, últimas filas:\n%s",
    #     subset.shape,
    #     subset.tail(5).to_string()
    # )

    logger.debug(
        "build_var_dict_from_names: subset final shape=%s, todas filas:\n%s",
        subset.shape,
        subset.to_string()
    )

    result = dict(zip(subset["TimeString"], subset["VarValue"]))
    return result

def build_var_dict_from_names(
    df: pd.DataFrame,
    varnames,
    n_samples: int = 24,
    step_every: int = 3,  # seleccionar 1 de cada 3
):
    """
    Devuelve los últimos n_samples valores de una variable,
    seleccionando 1 de cada 'step_every', PERO empezando desde
    el final (las muestras más recientes).

    Patrón (desde el final):
        último -> contar
        anterior -> skip
        anterior -> skip
        anterior -> contar
        ...
    """

    if isinstance(varnames, str):
        varnames = [varnames]

    logger.debug(
        "build_var_dict_from_names: varnames=%s, n_samples=%d, step_every=%d",
        varnames, n_samples, step_every
    )

    if "VarName" not in df.columns or "TimeString" not in df.columns:
        logger.warning("build_var_dict_from_names: columnas requeridas no encontradas en df")
        return {}

    # Filtrar por VarName
    subset = df[df["VarName"].isin(varnames)]

    if subset.empty:
        logger.warning(f"build_var_dict_from_names: sin datos para {varnames}")
        return {}

    # Ordenar por tiempo ascendente
    if "Time_ms" in subset.columns:
        subset = subset.assign(_time_ms=subset["Time_ms"].apply(lambda v: safe_float(v)))
        subset = subset.sort_values("_time_ms")
    else:
        subset = subset.sort_index()

    # Índices 0..N-1 en orden temporal
    subset = subset.reset_index(drop=True)
    total = len(subset)

    # 🔹 Construir índices desde el FINAL hacia atrás, saltando step_every
    #    ej. total=10, step_every=3 → [9, 6, 3, 0]
    indices_desc = list(range(total - 1, -1, -step_every))

    # Nos quedamos con los primeros n_samples de esa secuencia (los más recientes)
    chosen_idx = indices_desc[:n_samples]

    # Para devolver en orden cronológico, reordenamos esos índices
    chosen_idx = sorted(chosen_idx)

    selected = subset.iloc[chosen_idx]

    logger.debug(
        "build_var_dict_from_names: seleccionadas %d filas tras step_every=%d desde el final:\n%s",
        len(selected),
        step_every,
        selected.to_string()
    )

    return dict(zip(selected["TimeString"], selected["VarValue"]))
