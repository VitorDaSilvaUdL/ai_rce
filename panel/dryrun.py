"""Dry-run of the manual commands: the exact sequence of PLC writes a command would make, without a PLC.

RecordingPLC subclasses PLCController and replaces every PLC access: writes are recorded as steps in plain
language, waits are recorded instead of slept, the horizontal cover is simulated (it reaches the requested position
when its motor bit is on) and the rain/wind alarm is a parameter. The real sequences of plc_controller.py run
unchanged, so what the page shows is what set_cold_mode(), _sequence_obrir(), ... would really do.
"""
from sc.plc_controller import PLCController
from sc.plc_shadow import _BOOLS, _ORDER
from sc.cycle_log import combination_of, state_outputs

ACTUATORS = {500: "Bomba B1", 501: "Bomba B2", 300: "Válvula EV1", 301: "Válvula EV2",
             503: "Tapa horizontal", 504: "Tapa vertical"}
BIT_MEANING = {(10, 0): "manual", (10, 1): "marcha", (10, 2): "paro", (12, 0): "manual", (12, 1): "marcha",
               (12, 2): "marcha inversa"}
ADDRESSES = {"B1": [(500, 12, 0), (500, 12, 1)], "B2": [(501, 12, 0), (501, 12, 1)],
             "EV1": [(300, 10, 0), (300, 10, 1), (300, 10, 2)], "EV2": [(301, 10, 0), (301, 10, 1), (301, 10, 2)],
             "TapaHoritzontal": [(503, 12, 0), (503, 12, 1)], "TapaVertical": [(504, 12, 0), (504, 12, 1)]}
KEYS = ["ActManB1", "ActManMarxaB1", "ActManB2", "ActManMarxaB2", "ActManEV1", "ActManMarxaEV1", "ActManStopEV1",
        "ActManEV2", "ActManMarxaEV2", "ActManStopEV2", "ActManTH", "ActManMarxaTH", "ActManTV", "ActManMarxaTV"]

COMMANDS = {
    "cold": ("Modo frío", "set_cold_mode"),
    "heat": ("Modo calor", "set_heat_mode"),
    "parada": ("Parada", "set_parada_mode"),
    "automatic": ("Automático", "set_automatic_mode"),
    "open_cover": ("Abrir tapa", "_sequence_obrir"),
    "close_cover": ("Cerrar tapa", "_sequence_tancar"),
    "test_cover": ("Prueba de tapa (abrir y cerrar)", None),
}
COVER_OF_STATE = {"ColdMode1": "open", "ColdMode2": "open"}


class RecordingPLC(PLCController):
    execution = "dryrun"

    def __init__(self, state="Parada", alarm=False, cover=None):
        super().__init__(ip="", name="PLC-dryrun")
        self.steps = []
        self.aborted = False
        self.alarm = alarm
        self.cover = cover or COVER_OF_STATE.get(state, "closed")
        self.bits = {(503, 12, 2): False, (504, 12, 2): False}
        combination = combination_of(state) or ("A",) * 6
        for name, label in zip(_ORDER, combination):
            for addr, value in zip(ADDRESSES[name], _BOOLS[name][label]):
                self.bits[addr] = value

    # --- no PLC
    def connect(self):
        pass

    def disconnect(self):
        pass

    def ensure_connected(self):
        pass

    def start_alarm_monitor(self, poll_s=1.0):
        pass

    def any_alarm_active(self):
        if self.alarm:
            self.steps.append({"kind": "blocked", "text": "Apertura bloqueada: alarma de lluvia o viento activa"})
            self.aborted = True
        return self.alarm

    # --- recorded accesses
    def _write_bool_db(self, db_number, byte_index, bit_index, value):
        self.bits[(db_number, byte_index, bit_index)] = bool(value)
        meaning = BIT_MEANING.get((byte_index, bit_index), f"bit {byte_index}.{bit_index}")
        self.steps.append({"kind": "write", "db": db_number, "byte": byte_index, "bit": bit_index, "value": bool(value),
                           "text": f"{ACTUATORS.get(db_number, f'DB{db_number}')} → {meaning}: {'ON' if value else 'OFF'}"})

    def read_bool(self, db_number, byte_index, bit_index):
        if (db_number, byte_index, bit_index) == (10, 0, 0):
            return self.cover == "open"
        if (db_number, byte_index, bit_index) == (10, 0, 1):
            return self.cover == "closed"
        return self.bits.get((db_number, byte_index, bit_index), False)

    def read_actuators_state(self):
        bools = []
        for name in _ORDER:
            bools.extend(self.bits[a] for a in ADDRESSES[name])
        return dict(zip(KEYS, bools))

    def sleep_with_progress(self, seconds, desc="Esperando"):
        self.steps.append({"kind": "wait", "seconds": seconds, "text": f"Esperar {seconds} s — {desc}"})

    def wait_for_th_position(self, expected, timeout_s=60.0, poll_s=0.5):
        target = "open" if expected == "open" else "closed"
        word = "abierta" if target == "open" else "cerrada"
        moving = self.bits.get((503, 12, 1)) or self.bits.get((503, 12, 2))
        if moving:
            self.cover = target
            text = f"Tapa horizontal: esperar a que esté {word} (máx {int(timeout_s)} s)"
        else:
            text = f"Tapa horizontal: comprobar que parte de la posición {word}"
        ok = self.cover == target
        self.steps.append({"kind": "wait", "seconds": None if not moving else int(timeout_s), "text": text, "ok": ok})
        if not ok:
            self.aborted = True
            self.steps.append({"kind": "abort", "text": f"Secuencia abortada: la tapa no está {word}"})
        return ok

    def read_cover_position(self):
        return self.cover


def run(command, state="Parada", alarm=False, cover=None):
    """Steps and resulting state of a manual command, simulated."""
    if command not in COMMANDS:
        raise ValueError(f"comando desconocido: {command}")
    label, method = COMMANDS[command]
    plc = RecordingPLC(state=state, alarm=alarm, cover=cover)
    cover_before = plc.cover
    if command == "test_cover":
        plc._sequence_obrir()
        if not plc.aborted:
            plc.steps.append({"kind": "note", "text": "Tapa abierta: ahora se cierra"})
            plc._sequence_tancar()
    else:
        getattr(plc, method)()
    state_after, _ = plc.get_system_state()
    outputs = state_outputs(state_after)
    return {
        "command": command, "label": label, "state_before": state, "state_after": state_after,
        "cover_before": cover_before, "cover_after": plc.cover, "aborted": plc.aborted,
        "writes": sum(1 for s in plc.steps if s["kind"] == "write"), "steps": plc.steps,
        "pumps": {"b1": outputs["pump_b1"], "b2": outputs["pump_b2"], "ev1": outputs["ev1"], "ev2": outputs["ev2"]},
        "plc_mode": outputs["plc_mode"],
    }
