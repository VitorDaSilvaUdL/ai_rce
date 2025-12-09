# test_calculate_dem_for_period.py
#
# Tests unitarios mínimos para la función calculate_dem_for_period.
# La idea es comprobar:
#  - Caso básico con datos cada minuto.
#  - Intervalo con huecos (timestamps que faltan).
#  - Intervalo vacío (start == end).

from datetime import datetime
from sc.main import calculate_dem_for_period


def test_demanda_basica_3_minutos():
    """
    Caso sencillo:
    Tenemos demanda de 1.0 unidad en tres minutos consecutivos.
    Esperamos que la suma sea 3.0.
    """
    df_demand = {
        "2025-01-01T00:00:00": 1.0,
        "2025-01-01T00:01:00": 1.0,
        "2025-01-01T00:02:00": 1.0,
    }

    start = datetime(2025, 1, 1, 0, 0, 0)
    end = datetime(2025, 1, 1, 0, 3, 0)  # exclusivo

    dem = calculate_dem_for_period(df_demand, start, end)

    assert dem == 3.0


def test_demanda_con_huecos_en_el_diccionario():
    """
    El intervalo abarca 5 minutos, pero solo hay datos en 2 de ellos.
    La función debe sumar SOLO los que existen.
    """
    df_demand = {
        "2025-01-01T00:00:00": 2.0,
        "2025-01-01T00:03:00": 3.5,
        # faltan 00:01, 00:02 y 00:04
    }

    start = datetime(2025, 1, 1, 0, 0, 0)
    end = datetime(2025, 1, 1, 0, 5, 0)

    dem = calculate_dem_for_period(df_demand, start, end)

    # Solo 2.0 + 3.5
    assert dem == 5.5


def test_intervalo_vacio_da_cero():
    """
    Si start == end, no hay minutos que recorrer,
    por lo tanto la demanda total debe ser 0.0.
    """
    df_demand = {
        "2025-01-01T00:00:00": 10.0,
    }

    start = datetime(2025, 1, 1, 0, 0, 0)
    end = datetime(2025, 1, 1, 0, 0, 0)

    dem = calculate_dem_for_period(df_demand, start, end)

    assert dem == 0.0
