# test_calculate_optimal_production_plan.py
#
# Tests para comprobar:
#  - Selección correcta de franjas mínimas para cubrir la demanda.
#  - Caso en el que la demanda NO se puede cubrir.
#  - Que funcione aunque los valores vengan como strings (se hace float()).

from datetime import datetime
from sc.main import calculate_optimal_production_plan, temp_mode


def test_plan_optimo_cubre_demanda_con_dos_franjas():
    """
    Tenemos 3 franjas con producciones 10, 20 y 30.
    Demanda objetivo = 25.
    Lo óptimo es coger 10 + 20 = 30 (las dos primeras).
    """
    available_prod = {
        "2025-01-01T00:00:00": 10.0,
        "2025-01-01T00:15:00": 20.0,
        "2025-01-01T00:30:00": 30.0,
    }
    target_demand = 25.0
    tipo_modo = 1  # HOT

    selected_frames, total_prod = calculate_optimal_production_plan(
        available_prod, target_demand, tipo_modo
    )

    # Deben haberse elegido 2 franjas
    assert len(selected_frames) == 2
    # Producción acumulada = 30
    assert total_prod == 30.0

    # Comprobamos que temp_mode se ha llenado con el tipo correcto
    for dt in selected_frames:
        assert temp_mode[dt] == tipo_modo


def test_demanda_demasiado_grande_devuelve_menos_uno():
    """
    La demanda es mayor que la suma de toda la producción disponible.
    En este caso la función debe devolver ([], -1).
    """
    available_prod = {
        "2025-01-01T00:00:00": 5.0,
        "2025-01-01T00:15:00": 5.0,
    }
    # Demanda imposible de cubrir
    target_demand = 20.0

    selected_frames, total_prod = calculate_optimal_production_plan(
        available_prod, target_demand, type=0
    )

    assert selected_frames == []
    assert total_prod == -1


def test_orden_correcto_con_valores_string():
    """
    Los valores vienen como strings (por cómo se leen a veces del JSON),
    pero la función convierte a float y debe ordenar correctamente:
    primero 1.0, luego 5.0, luego 10.0.
    """
    available_prod = {
        "2025-01-01T00:00:00": "10.0",
        "2025-01-01T00:15:00": "1.0",
        "2025-01-01T00:30:00": "5.0",
    }
    target_demand = 6.0  # 1.0 + 5.0 = 6.0

    selected_frames, total_prod = calculate_optimal_production_plan(
        available_prod, target_demand, type=1
    )

    # Deben coger primero las de 1.0 y 5.0
    assert len(selected_frames) == 2
    assert total_prod == 6.0

    # Y el primer timestamp debe ser el de 1.0
    assert selected_frames[0] == datetime.fromisoformat("2025-01-01T00:15:00")
