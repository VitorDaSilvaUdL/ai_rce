import os
from sc.utils import read_data as rd

def test_get_last_data_from_db_with_test_csvs(monkeypatch):
    """
    Verifica que get_last_data_from_db():
      - lee los ficheros lect_test/solar_test/ir_test
      - construye la estructura 'data'
      - devuelve valores numéricos coherentes
    """

    base = os.path.join(os.path.dirname(__file__), "data")

    # Redirigimos las rutas de read_data.py a tus CSV de test
    monkeypatch.setattr(rd, "lect_dir", os.path.join(base, "lect_test.csv"))
    monkeypatch.setattr(rd, "solar_dir", os.path.join(base, "solar_test.csv"))
    monkeypatch.setattr(rd, "ir_dir", os.path.join(base, "ir_test.csv"))

    fake_sources = {
        "hot": {
            "varnames": ["TempT6_RCEa", "TempT6_RCEa_v2"],
            "source":  os.path.join(base, "lect_test.csv"),   # temperatura caliente
        },
        "cold": {
            "varnames": ["TempT9_RCEa", "TempT9_RCEa_v2"],
            "source":  os.path.join(base, "lect_test.csv"),   # temperatura fría
        },
        "v_vent": {
            "varnames": ["VelVent_RCEa", "VelVent_RCEa_v2"],
            "source": os.path.join(base, "lect_test.csv"),   # velocidad viento
        },
        "solar": {
            "varnames": ["IO_SENSOR1_DATA_RCEa"],
            "source": os.path.join(base, "solar_test.csv"),  # radiación solar
        },
        "ir": {
            "varnames": ["E_FIR, neto, [W/m2]_RCEb"],
            "source": os.path.join(base, "ir_test.csv"),     # radiación IR neta
        },
    }

    monkeypatch.setattr(rd, "VARIABLE_SOURCES", fake_sources)

    # Ejecutar la función
    result = rd.get_last_data_from_db()

    # Debe existir la clave 'data'
    assert "data" in result
    data = result["data"]
    assert isinstance(data, list)
    assert len(data) > 0  # al menos un registro

    # Cogemos el último registro (el más reciente según tu lógica)
    last = data[-1]

    # Claves mínimas que esperamos
    for key in ["cold", "hot", "wind_vel_m_s", "solar_rad_w_m2", "ir_rad_w_m2",
                "reset_cold", "reset_hot", "mode",
                "day_sin", "day_cos", "year_sin", "year_cos"]:
        assert key in last, f"Falta la clave {key} en el registro final"

    # Comprobar tipos numéricos
    assert isinstance(last["cold"], float)
    assert isinstance(last["hot"], float)
    assert isinstance(last["wind_vel_m_s"], float)
    assert isinstance(last["solar_rad_w_m2"], float)
    assert isinstance(last["ir_rad_w_m2"], float)

    # Trigonometría razonable
    assert -1.0 <= last["day_sin"] <= 1.0
    assert -1.0 <= last["day_cos"] <= 1.0
    assert -1.0 <= last["year_sin"] <= 1.0
    assert -1.0 <= last["year_cos"] <= 1.0


def test_get_last_data_from_db_expected_values(monkeypatch):
    """
    Test más estricto:
    comprueba que el último timestamp y sus valores coinciden con lo que
    esperamos exactamente en los CSV de prueba.
    """

    base = os.path.join(os.path.dirname(__file__), "data")

    monkeypatch.setattr(rd, "lect_dir", os.path.join(base, "lect_test.csv"))
    monkeypatch.setattr(rd, "solar_dir", os.path.join(base, "solar_test.csv"))
    monkeypatch.setattr(rd, "ir_dir", os.path.join(base, "ir_test.csv"))

    fake_sources = {
        "hot": {
            "varnames": ["TempT6_RCEa", "TempT6_RCEa_v2"],
            "source":  os.path.join(base, "lect_test.csv"),   # temperatura caliente
        },
        "cold": {
            "varnames": ["TempT9_RCEa", "TempT9_RCEa_v2"],
            "source":  os.path.join(base, "lect_test.csv"),   # temperatura fría
        },
        "v_vent": {
            "varnames": ["VelVent_RCEa", "VelVent_RCEa_v2"],
            "source": os.path.join(base, "lect_test.csv"),   # velocidad viento
        },
        "solar": {
            "varnames": ["IO_SENSOR1_DATA_RCEa"],
            "source": os.path.join(base, "solar_test.csv"),  # radiación solar
        },
        "ir": {
            "varnames": ["E_FIR, neto, [W/m2]_RCEb"],
            "source": os.path.join(base, "ir_test.csv"),     # radiación IR neta
        },
    }

    monkeypatch.setattr(rd, "VARIABLE_SOURCES", fake_sources)

    result = rd.get_last_data_from_db()
    data = result["data"]
    last = data[-1]

    # ⚠️ RELLENA ESTOS VALORES con lo que en realidad hay en tus CSV de test
    # por ejemplo, si el último instante es "11/11/2025 12:42:57" y:
    #   hot  = 22.27
    #   cold = 8.36
    # etc., pon esos números aquí.
    EXPECTED_HOT = 22.27286     
    EXPECTED_COLD = 8.362811     
    EXPECTED_WIND = 0.2669271     # <-- ejemplo
    EXPECTED_SOLAR = 0.0     # <-- ejemplo
    EXPECTED_IR = 33.0       # <-- ejemplo

    # Aquí comparamos con cierta tolerancia por si hay comas/decimales
    assert abs(last["hot"]  - EXPECTED_HOT)  < 1e-3
    assert abs(last["cold"] - EXPECTED_COLD) < 1e-3
    assert abs(last["wind_vel_m_s"] - EXPECTED_WIND) < 1e-3
    assert abs(last["solar_rad_w_m2"] - EXPECTED_SOLAR) < 1e-3
    assert abs(last["ir_rad_w_m2"] - EXPECTED_IR) < 1e-3