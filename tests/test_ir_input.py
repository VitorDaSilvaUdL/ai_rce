"""Infrared input of the model: the net IR of the single pyrgeometer (unit A). The unit-B tags of Pyrgeometer0.csv are
not a measurement (constant 33 W/m2 in the lab files); the original control read them."""
from sc import config
from sc.utils import read_data
from panel import simfull


def test_ir_varname_by_unit():
    assert read_data.ir_varname("RCEa") == "E_FIR, neto, [W/m2]_RCEa"
    assert read_data.ir_varname("RCEb") == "E_FIR, neto, [W/m2]_RCEb"


def test_control_reads_unit_a_by_default():
    assert config.IR_UNIT == "RCEa"
    assert read_data.VARIABLE_SOURCES["ir"]["varnames"] == ["E_FIR, neto, [W/m2]_RCEa"]


def test_legacy_config_keeps_unit_b():
    import json
    for env in ("prod", "test"):
        cfg = json.load(open(config.PROJECT_DIR / "sc" / f"config.{env}.json", encoding="utf-8"))
        assert cfg["control"]["ir_unit"] == "RCEa" and cfg["control"]["_antiguo"]["ir_unit"] == "RCEb"


def test_full_simulator_uses_the_same_ir_as_the_control():
    assert simfull.SAMPLE_VARS["ir_rad_w_m2"] == ("ir_net_w_m2", "RCE-a")
