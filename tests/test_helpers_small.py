# tests/test_helpers_small.py
#
# Tests pequeños/mínimos para las funciones auxiliares de sc.main:
#   - rodona_15_minuts_avall
#   - rodona_hora_avall
#   - get_now_val
#   - get_now_val_2
#   - stop
#
# La idea es que sean fáciles de explicar:
#   * Entrada muy simple
#   * Llamada a la función
#   * assert sobre el resultado esperado
#
# Usamos pytest y monkeypatch donde hace falta (para controlar datetime.now y el plc).

from datetime import datetime, timedelta
import pytest

import sc.main as main
from sc.main import (
    rodona_15_minuts_avall,
    rodona_hora_avall,
    get_now_val,
    get_now_val_2,
    stop,
)


# --------------------------------------------------------------------
# rodona_15_minuts_avall
# --------------------------------------------------------------------

def test_rodona_15_minuts_avall_minuto_7_va_a_00():
    """
    14:07 debe redondear a 14:00 (baja al múltiplo de 15 anterior).
    """
    dt = datetime(2025, 1, 1, 14, 7, 0)
    res = rodona_15_minuts_avall(dt)
    assert res == datetime(2025, 1, 1, 14, 0, 0)


def test_rodona_15_minuts_avall_minuto_15_se_mantiene():
    """
    14:15 ya es múltiplo de 15 → se queda en 14:15.
    """
    dt = datetime(2025, 1, 1, 14, 15, 0)
    res = rodona_15_minuts_avall(dt)
    assert res == datetime(2025, 1, 1, 14, 15, 0)


def test_rodona_15_minuts_avall_minuto_59_va_a_45():
    """
    14:59 baja al múltiplo de 15 anterior → 14:45.
    """
    dt = datetime(2025, 1, 1, 14, 59, 0)
    res = rodona_15_minuts_avall(dt)
    assert res == datetime(2025, 1, 1, 14, 45, 0)


# --------------------------------------------------------------------
# rodona_hora_avall
# --------------------------------------------------------------------

def test_rodona_hora_avall_14_59_va_a_14_00():
    """
    14:59 debe bajar a 14:00.
    """
    dt = datetime(2025, 1, 1, 14, 59, 0)
    res = rodona_hora_avall(dt)
    assert res == datetime(2025, 1, 1, 14, 0, 0)


def test_rodona_hora_avall_ya_redondeado_no_cambia():
    """
    14:00 se mantiene en 14:00.
    """
    dt = datetime(2025, 1, 1, 14, 0, 0)
    res = rodona_hora_avall(dt)
    assert res == datetime(2025, 1, 1, 14, 0, 0)


def test_rodona_hora_avall_media_noche():
    """
    00:30 debe bajar a 00:00.
    """
    dt = datetime(2025, 1, 1, 0, 30, 0)
    res = rodona_hora_avall(dt)
    assert res == datetime(2025, 1, 1, 0, 0, 0)


# --------------------------------------------------------------------
# get_now_val
# --------------------------------------------------------------------

def test_get_now_val_devuelve_valor_de_la_franja(monkeypatch):
    """
    Fijamos ahora=2025-01-01 12:07.
    rodona_15_minuts_avall → 12:00.
    Definimos un diccionario con una entrada en esa franja (12:05).
    La función debe devolver el valor asociado.
    """

    class FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2025, 1, 1, 12, 7, 0)

    # Sustituimos datetime en main por nuestra clase con now() fijo
    monkeypatch.setattr(main, "datetime", FixedDateTime)

    curr = {
        "2025-01-01 12:05": 42.0,
    }

    res = get_now_val(curr)
    assert res == 42.0


def test_get_now_val_sin_coincidencias_devuelve_cero(monkeypatch):
    """
    Si no hay ninguna franja que coincida con la ventana actual,
    la función debe devolver 0.
    """

    class FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2025, 1, 1, 10, 0, 0)

    monkeypatch.setattr(main, "datetime", FixedDateTime)

    curr = {
        "2025-01-01 09:00": 10.0,  # fuera de la franja 10:00–10:15
    }

    res = get_now_val(curr)
    assert res == 0


def test_get_now_val_varias_claves_misma_franja(monkeypatch):
    """
    Si hay varias claves dentro de la misma franja de 15 minutos,
    la función devolverá el valor de la primera que encuentre.
    Dado que los diccionarios preservan el orden de inserción en Python 3.7+,
    será la primera clave del diccionario literal.
    """

    class FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2025, 1, 1, 12, 1, 0)

    monkeypatch.setattr(main, "datetime", FixedDateTime)

    curr = {
        "2025-01-01 12:00": 1.0,
        "2025-01-01 12:10": 2.0,
    }

    res = get_now_val(curr)
    assert res == 1.0  # primera coincidencia en la franja


# --------------------------------------------------------------------
# get_now_val_2
# --------------------------------------------------------------------

def test_get_now_val_2_caso_frontal_en_hora_en_punto(monkeypatch):
    """
    Fijamos ahora=2025-01-01 12:34 → rodona_hora_avall → 12:00.
    En curr las claves están un día por delante (como en las predicciones),
    así que para que coincida la condición:
        frame = clave - 1 día
    debe tener misma hora y minuto=0 que 'now'.
    """

    class FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2025, 1, 1, 12, 34, 0)

    monkeypatch.setattr(main, "datetime", FixedDateTime)

    # clave 2025-01-02T12:00:00 → frame=2025-01-01T12:00:00
    curr = {
        "2025-01-02T12:00:00": 99.0,
    }

    res = get_now_val_2(curr)
    assert res == 99.0


def test_get_now_val_2_sin_coincidencias_devuelve_cero(monkeypatch):
    """
    Si ninguna clave cumple las condiciones (ni a la hora en punto ni a left:59),
    la función debe devolver 0.
    """

    class FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2025, 1, 1, 8, 0, 0)

    monkeypatch.setattr(main, "datetime", FixedDateTime)

    curr = {
        "2025-01-02T10:00:00": 1.0,
    }

    res = get_now_val_2(curr)
    assert res == 0


def test_get_now_val_2_caso_minuto_59_anterior(monkeypatch):
    """
    Probamos la segunda parte de la condición:
    se compara también con left = now - 1 minuto.
    Fijamos now=2025-01-01 12:00 → left=11:59.
    Queremos una clave que, al restar un día, dé frame=2025-01-01 11:59.
    """

    class FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2025, 1, 1, 12, 0, 0)

    monkeypatch.setattr(main, "datetime", FixedDateTime)

    # clave 2025-01-02T11:59:00 → frame=2025-01-01T11:59:00
    curr = {
        "2025-01-02T11:59:00": 55.0,
    }

    res = get_now_val_2(curr)
    assert res == 55.0


# --------------------------------------------------------------------
# stop
# --------------------------------------------------------------------

def test_stop_con_alarma_llama_a_plc_y_hace_exit(monkeypatch):
    """
    Si plc.alarm_active es True:
      - Debe llamarse a close_doors() y disconnect()
      - Debe lanzar SystemExit(1)
    """

    class FakePLC:
        def __init__(self):
            self.alarm_active = True
            self.closed = False
            self.disconnected = False

        def close_doors(self):
            self.closed = True

        def disconnect(self):
            self.disconnected = True

    fake_plc = FakePLC()
    monkeypatch.setattr(main, "plc", fake_plc)

    with pytest.raises(SystemExit) as excinfo:
        stop()

    assert excinfo.value.code == 1
    assert fake_plc.closed is True
    assert fake_plc.disconnected is True


def test_stop_sin_alarma_no_llama_a_plc_pero_exit(monkeypatch):
    """
    Si plc.alarm_active es False:
      - No se deben llamar close_doors() ni disconnect()
      - Pero igualmente debe lanzar SystemExit(1), según la implementación actual.
    """

    class FakePLC:
        def __init__(self):
            self.alarm_active = False
            self.closed = False
            self.disconnected = False

        def close_doors(self):
            self.closed = True

        def disconnect(self):
            self.disconnected = True

    fake_plc = FakePLC()
    monkeypatch.setattr(main, "plc", fake_plc)

    with pytest.raises(SystemExit) as excinfo:
        stop()

    assert excinfo.value.code == 1
    assert fake_plc.closed is False
    assert fake_plc.disconnected is False


def test_stop_siempre_lanza_system_exit(monkeypatch):
    """
    Test genérico: independientemente del estado,
    stop() siempre termina el programa con SystemExit(1).
    """
    class FakePLC:
        def __init__(self, alarm_active):
            self.alarm_active = alarm_active

        def close_doors(self):
            pass

        def disconnect(self):
            pass

    monkeypatch.setattr(main, "plc", FakePLC(alarm_active=True))

    with pytest.raises(SystemExit) as excinfo:
        stop()

    assert excinfo.value.code == 1
