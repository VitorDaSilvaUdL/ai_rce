# test_verify_and_adjust_demand.py
#
# Tests para verify_and_adjust_demand:
#  - Si action != 1, la demanda no cambia.
#  - Si action == 1 y demanda=0, resultado=0.
#  - Si action == 1 y demanda>0, la nueva demanda está en un rango esperable.

import sc.main as main            # importamos el módulo completo para acceder a globals
from sc.main import verify_and_adjust_demand   # importamos solo la función a probar


def test_sin_accion_la_demanda_no_cambia():
    """
    Cuando action != 1, la bomba está parada, así que no se produce energía
    y la demanda restante debe ser la misma que la de entrada.
    """
    # Creamos/forzamos la variable global 'action' en el módulo main
    main.action = 0  # cualquier valor distinto de 1
    demanda_inicial = 100.0

    demanda_restante = verify_and_adjust_demand(demanda_inicial)

    assert demanda_restante == demanda_inicial


def test_con_accion_y_demanda_cero_sigue_cero():
    """
    Si la demanda a cubrir es 0, aunque la bomba esté ON (action=1),
    el resultado debe seguir siendo 0.
    """
    main.action = 1
    demanda_inicial = 0.0

    demanda_restante = verify_and_adjust_demand(demanda_inicial)

    assert demanda_restante == 0.0


def test_con_accion_la_demanda_disminuye_en_un_rango():
    """
    Cuando action=1, se produce energía:
        actual_production_last_step = P * time_step_hours * random(0.8, 1.2)

    No sabemos el valor exacto del random, pero sí el rango:
        P * h * 0.8 <= producción <= P * h * 1.2

    Verificamos que la demanda restante está dentro del rango esperado:
        [max(0, demanda - P*h*1.2), demanda - P*h*0.8]
    """
    main.action = 1
    demanda_inicial = 1000.0  # un número suficientemente grande

    # Leemos las constantes globales del módulo main
    P = main.P_bomba_watts
    h = main.time_step_hours

    demanda_restante = verify_and_adjust_demand(demanda_inicial)

    # límites teóricos de la demanda restante
    lower = max(0.0, demanda_inicial - P * h * 1.2)
    upper = demanda_inicial - P * h * 0.8

    assert lower <= demanda_restante <= upper
