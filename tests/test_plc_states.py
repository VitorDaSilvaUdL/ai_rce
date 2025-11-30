import pytest
from unittest.mock import MagicMock
from sc.plc_controller import PLCController

"""
TODO: PREGUNTAR AL ALBERT
-------------------------------------

Durante la revisión de PLCController hemos detectado que en las secuencias
_sequence_tancar y _sequence_obrir se utilizan también los siguientes bits:

    - DB503.DBX12.2  → activación inversa de la Tapa Horizontal (TH)
    - DB504.DBX12.2  → activación inversa de la Tapa Vertical (TV)

Estos bits NO representan un “STOP”, sino la orden inversa del actuador,
es decir:

    ActManMarxaInvTH = DB503.DBX12.2
    ActManMarxaInvTV = DB504.DBX12.2

Sin embargo, estos dos booleanos NO se leen actualmente en read_actuators_state(),
donde solamente se devuelven:

    - ActManTH        (DBX12.0)
    - ActManMarxaTH   (DBX12.1)
    - ActManTV        (DBX12.0)
    - ActManMarxaTV   (DBX12.1)

PREGUNTA:
¿Debemos añadir también ActManMarxaInvTH y ActManMarxaInvTV al estado leído
por read_actuators_state() para representar correctamente el estado completo
de las tapas? 

De este modo el estado de cada tapa sería simétrico al de EV1 y EV2, que sí
incorporan su tercer bit para modos de funcionamiento (marxa / paro / inverso).

"""

@pytest.fixture
def plc(monkeypatch):
    """
    PLCController en modo test con read_bool mockeado.
    """
    plc = PLCController("127.0.0.1", test_mode=True)

    # Mock general: por defecto todo False
    monkeypatch.setattr(plc, "read_bool", lambda *args, **kwargs: False)

    return plc


def test_read_actuators_state_all_false(plc):
    """
    Si todo es False → todos los actuadores deben estar en modo Automático.
    """
    state = plc.read_actuators_state()

    # 14 valores esperados
    assert len(state) == 14

    # Todos False
    assert all(v is False for v in state.values())


def test_get_system_state_automatic(plc, monkeypatch):
    """
    Verifica que la combinación legal (A,A,A,A,A,A) se detecta como AutomaticMode.
    """

    # B1, B2, EV1, EV2, TH, TV = todos automáticos
    mapping = {
        # B1
        ('ActManB1', 500, 12, 0): False,
        ('ActManMarxaB1', 500, 12, 1): False,

        # B2
        ('ActManB2', 501, 12, 0): False,
        ('ActManMarxaB2', 501, 12, 1): False,

        # EV1
        ('ActManEV1', 300, 10, 0): False,
        ('ActManMarxaEV1', 300, 10, 1): False,
        ('ActManStopEV1', 300, 10, 2): False,

        # EV2
        ('ActManEV2', 301, 10, 0): False,
        ('ActManMarxaEV2', 301, 10, 1): False,
        ('ActManStopEV2', 301, 10, 2): False,

        # TH
        ('ActManTH', 503, 12, 0): False,
        ('ActManMarxaTH', 503, 12, 1): False,

        # TV
        ('ActManTV', 504, 12, 0): False,
        ('ActManMarxaTV', 504, 12, 1): False,
    }

    def fake_read_bool(db, byte, bit):
        for (name, dbn, by, bi), val in mapping.items():
            if db == dbn and byte == by and bit == bi:
                return val
        return False

    monkeypatch.setattr(plc, "read_bool", fake_read_bool)

    estado, _ = plc.get_system_state()
    assert estado == "AutomaticMode"


def test_coldmode1_detection(plc, monkeypatch):
    """
    ColdMode1 = ("A", "MM", "MM", "MP", "MM", "MM")
    Verificación directa del mapeo SystemCase.
    """

    # B1:A  → (False,False)
    # B2:MM → (True,True)
    # EV1:MM → (True,True,False)
    # EV2:MP → (True,False,True)
    # TH:MM → (True,True)
    # TV:MM → (True,True)

    seq = [
        False, False,     # B1
        True, True,       # B2
        True, True, False,# EV1
        True, False, True,# EV2
        True, True,       # TH
        True, True        # TV
    ]

    def fake_read_bool(db, byte, bit):
        # retorna según el orden de lectura esperado por read_actuators_state
        order = [
            (500, 12, 0), (500, 12, 1),
            (501, 12, 0), (501, 12, 1),
            (300, 10, 0), (300, 10, 1), (300, 10, 2),
            (301, 10, 0), (301, 10, 1), (301, 10, 2),
            (503, 12, 0), (503, 12, 1),
            (504, 12, 0), (504, 12, 1),
        ]
        idx = order.index((db, byte, bit))
        return seq[idx]

    monkeypatch.setattr(plc, "read_bool", fake_read_bool)

    estado, _ = plc.get_system_state()
    assert estado == "ColdMode1"

def test_coldmode2_detection(plc, monkeypatch):
    """
    ColdMode2 = ("MM", "MM", "MM", "MP", "MM", "MM")
    Verificación directa del mapeo SystemCase.
    """

    # B1:MM → (True,True)
    # B2:MM → (True,True)
    # EV1:MM → (True,True,False)
    # EV2:MP → (True,False,True)
    # TH:MM → (True,True)
    # TV:MM → (True,True)

    seq = [
        True, True,        # B1
        True, True,        # B2
        True, True, False, # EV1
        True, False, True, # EV2
        True, True,        # TH
        True, True         # TV
    ]

    def fake_read_bool(db, byte, bit):
        order = [
            (500, 12, 0), (500, 12, 1),
            (501, 12, 0), (501, 12, 1),
            (300, 10, 0), (300, 10, 1), (300, 10, 2),
            (301, 10, 0), (301, 10, 1), (301, 10, 2),
            (503, 12, 0), (503, 12, 1),
            (504, 12, 0), (504, 12, 1),
        ]
        idx = order.index((db, byte, bit))
        return seq[idx]

    monkeypatch.setattr(plc, "read_bool", fake_read_bool)

    estado, _ = plc.get_system_state()
    assert estado == "ColdMode2"


def test_heatmode1_detection(plc, monkeypatch):
    """
    HeatMode1 = ("A", "MP", "MP", "MM", "MP", "MP")
    Verificación directa del mapeo SystemCase.
    """

    # B1:A  → (False,False)
    # B2:MP → (True,False)
    # EV1:MP → (True,False,True)
    # EV2:MM → (True,True,False)
    # TH:MP → (True,False)
    # TV:MP → (True,False)

    seq = [
        False, False,       # B1
        True, False,        # B2
        True, False, True,  # EV1
        True, True, False,  # EV2
        True, False,        # TH
        True, False         # TV
    ]

    def fake_read_bool(db, byte, bit):
        order = [
            (500, 12, 0), (500, 12, 1),
            (501, 12, 0), (501, 12, 1),
            (300, 10, 0), (300, 10, 1), (300, 10, 2),
            (301, 10, 0), (301, 10, 1), (301, 10, 2),
            (503, 12, 0), (503, 12, 1),
            (504, 12, 0), (504, 12, 1),
        ]
        idx = order.index((db, byte, bit))
        return seq[idx]

    monkeypatch.setattr(plc, "read_bool", fake_read_bool)

    estado, _ = plc.get_system_state()
    assert estado == "HeatMode1"

def test_heatmode2_detection(plc, monkeypatch):
    """
    HeatMode2 = ("MM", "MP", "MP", "MM", "MP", "MP")
    Verificación directa del mapeo SystemCase.
    """

    # B1:MM → (True,True)
    # B2:MP → (True,False)
    # EV1:MP → (True,False,True)
    # EV2:MM → (True,True,False)
    # TH:MP → (True,False)
    # TV:MP → (True,False)

    seq = [
        True, True,         # B1
        True, False,        # B2
        True, False, True,  # EV1
        True, True, False,  # EV2
        True, False,        # TH
        True, False         # TV
    ]

    def fake_read_bool(db, byte, bit):
        order = [
            (500, 12, 0), (500, 12, 1),
            (501, 12, 0), (501, 12, 1),
            (300, 10, 0), (300, 10, 1), (300, 10, 2),
            (301, 10, 0), (301, 10, 1), (301, 10, 2),
            (503, 12, 0), (503, 12, 1),
            (504, 12, 0), (504, 12, 1),
        ]
        idx = order.index((db, byte, bit))
        return seq[idx]

    monkeypatch.setattr(plc, "read_bool", fake_read_bool)

    estado, _ = plc.get_system_state()
    assert estado == "HeatMode2"

def test_parada_detection(plc, monkeypatch):
    """
    Parada = ("MP", "MP", "MP", "MM", "MP", "MP")
    Verificación directa del mapeo SystemCase.
    """

    # B1:MP → (True,False)
    # B2:MP → (True,False)
    # EV1:MP → (True,False,True)
    # EV2:MM → (True,True,False)
    # TH:MP → (True,False)
    # TV:MP → (True,False)

    seq = [
        True, False,       # B1
        True, False,       # B2
        True, False, True, # EV1
        True, True, False, # EV2
        True, False,       # TH
        True, False        # TV
    ]

    def fake_read_bool(db, byte, bit):
        # retorna según el orden de lectura esperado por read_actuators_state
        order = [
            (500, 12, 0), (500, 12, 1),
            (501, 12, 0), (501, 12, 1),
            (300, 10, 0), (300, 10, 1), (300, 10, 2),
            (301, 10, 0), (301, 10, 1), (301, 10, 2),
            (503, 12, 0), (503, 12, 1),
            (504, 12, 0), (504, 12, 1),
        ]
        idx = order.index((db, byte, bit))
        return seq[idx]

    monkeypatch.setattr(plc, "read_bool", fake_read_bool)

    estado, _ = plc.get_system_state()
    assert estado == "Parada"


def test_get_current_mode_cold_from_coldmode1(plc, monkeypatch):
    """
    get_current_mode() debe devolver 'cold' cuando SystemCase = ColdMode1.
    ColdMode1 = ("A", "MM", "MM", "MP", "MM", "MM")
    """

    # B1:A  → (False,False)
    # B2:MM → (True,True)
    # EV1:MM → (True,True,False)
    # EV2:MP → (True,False,True)
    # TH:MM → (True,True)
    # TV:MM → (True,True)

    seq = [
        False, False,      # B1
        True, True,        # B2
        True, True, False, # EV1
        True, False, True, # EV2
        True, True,        # TH
        True, True         # TV
    ]

    def fake_read_bool(db, byte, bit):
        order = [
            (500, 12, 0), (500, 12, 1),
            (501, 12, 0), (501, 12, 1),
            (300, 10, 0), (300, 10, 1), (300, 10, 2),
            (301, 10, 0), (301, 10, 1), (301, 10, 2),
            (503, 12, 0), (503, 12, 1),
            (504, 12, 0), (504, 12, 1),
        ]
        idx = order.index((db, byte, bit))
        return seq[idx]

    monkeypatch.setattr(plc, "read_bool", fake_read_bool)

    mode = plc.get_current_mode()
    assert mode == "cold"


def test_get_current_mode_hot_from_heatmode1(plc, monkeypatch):
    """
    get_current_mode() debe devolver 'hot' cuando SystemCase = HeatMode1.
    HeatMode1 = ("A", "MP", "MP", "MM", "MP", "MP")
    """

    # B1:A  → (False,False)
    # B2:MP → (True,False)
    # EV1:MP → (True,False,True)
    # EV2:MM → (True,True,False)
    # TH:MP → (True,False)
    # TV:MP → (True,False)

    seq = [
        False, False,       # B1
        True, False,        # B2
        True, False, True,  # EV1
        True, True, False,  # EV2
        True, False,        # TH
        True, False         # TV
    ]

    def fake_read_bool(db, byte, bit):
        order = [
            (500, 12, 0), (500, 12, 1),
            (501, 12, 0), (501, 12, 1),
            (300, 10, 0), (300, 10, 1), (300, 10, 2),
            (301, 10, 0), (301, 10, 1), (301, 10, 2),
            (503, 12, 0), (503, 12, 1),
            (504, 12, 0), (504, 12, 1),
        ]
        idx = order.index((db, byte, bit))
        return seq[idx]

    monkeypatch.setattr(plc, "read_bool", fake_read_bool)

    mode = plc.get_current_mode()
    assert mode == "hot"


def test_get_current_mode_automatic_from_automaticmode(plc, monkeypatch):
    """
    get_current_mode() debe devolver 'automatic' cuando SystemCase = AutomaticMode.
    AutomaticMode = ("A", "A", "A", "A", "A", "A")
    """

    # B1:A  → (False,False)
    # B2:A  → (False,False)
    # EV1:A → (False,False,False)
    # EV2:A → (False,False,False)
    # TH:A → (False,False)
    # TV:A → (False,False)

    seq = [
        False, False,      # B1
        False, False,      # B2
        False, False, False,  # EV1
        False, False, False,  # EV2
        False, False,      # TH
        False, False       # TV
    ]

    def fake_read_bool(db, byte, bit):
        order = [
            (500, 12, 0), (500, 12, 1),
            (501, 12, 0), (501, 12, 1),
            (300, 10, 0), (300, 10, 1), (300, 10, 2),
            (301, 10, 0), (301, 10, 1), (301, 10, 2),
            (503, 12, 0), (503, 12, 1),
            (504, 12, 0), (504, 12, 1),
        ]
        idx = order.index((db, byte, bit))
        return seq[idx]

    monkeypatch.setattr(plc, "read_bool", fake_read_bool)

    mode = plc.get_current_mode()
    assert mode == "automatic"


def test_get_current_mode_parada_from_parada(plc, monkeypatch):
    """
    get_current_mode() debe devolver 'parada' cuando SystemCase = Parada.
    Parada = ("MP", "MP", "MP", "MM", "MP", "MP")
    """

    # B1:MP → (True,False)
    # B2:MP → (True,False)
    # EV1:MP → (True,False,True)
    # EV2:MM → (True,True,False)
    # TH:MP → (True,False)
    # TV:MP → (True,False)

    seq = [
        True, False,       # B1
        True, False,       # B2
        True, False, True, # EV1
        True, True, False, # EV2
        True, False,       # TH
        True, False        # TV
    ]

    def fake_read_bool(db, byte, bit):
        order = [
            (500, 12, 0), (500, 12, 1),
            (501, 12, 0), (501, 12, 1),
            (300, 10, 0), (300, 10, 1), (300, 10, 2),
            (301, 10, 0), (301, 10, 1), (301, 10, 2),
            (503, 12, 0), (503, 12, 1),
            (504, 12, 0), (504, 12, 1),
        ]
        idx = order.index((db, byte, bit))
        return seq[idx]

    monkeypatch.setattr(plc, "read_bool", fake_read_bool)

    mode = plc.get_current_mode()
    assert mode == "parada"