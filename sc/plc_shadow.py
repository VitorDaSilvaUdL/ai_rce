"""Shadow mode: the control loop runs unchanged but nothing reaches the PLC.

PLCShadow keeps the decision logic of PLCController (decide_next_state_from_nn, schedules) and replaces every
PLC read or write: the system state is virtual (as if each decision had been applied), the weekly schedule
comes from the configuration instead of DB80/DB83, and any attempt to read or write a PLC address raises
RuntimeError. No snap7 client is ever created.
"""
from sc.logger import get_logger
from sc.plc_controller import PLCController, CONFIG_ACTUADORES
from sc.cycle_log import combination_of

logger = get_logger(__name__)   # sc.log, like the rest of the control system

DEFAULT_SCHEDULE = {"heat_start": "08:00:00", "heat_end": "14:00:00", "parada_end": "16:00:00"}

# DB80/DB83 offsets read by decision_none_or_parada, Monday..Friday
_HEAT_START = {62, 70, 78, 86, 94}          # DB80
_HEAT_END = {66, 74, 82, 90, 98}            # DB80
_PARADA_END = {62, 70, 78, 86, 94}          # DB83

_BOOLS = {   # actuator label -> bools, inverse of CONFIG_ACTUADORES
    name: {label: bools for bools, label in table.items() if label}
    for name, table in CONFIG_ACTUADORES.items()
}
_ORDER = ["B1", "B2", "EV1", "EV2", "TapaHoritzontal", "TapaVertical"]


def _blocked(*_args, **_kwargs):
    raise RuntimeError("shadow mode: PLC reads and writes are disabled")


class PLCShadow(PLCController):
    execution = "shadow"

    def __init__(self, initial_state="Parada", schedule=None, name="PLC-shadow"):
        super().__init__(ip="", name=name)
        self.state = initial_state
        sched = {**DEFAULT_SCHEDULE, **(schedule or {})}
        self.schedule = {k: self._hhmmss(v) for k, v in sched.items()}

    @staticmethod
    def _hhmmss(value):
        parts = [int(p) for p in str(value).split(":")] + [0, 0]
        return f"{parts[0]:02}:{parts[1]:02}:{parts[2]:02}"

    # --- connection: nothing to connect -------------------------------------------------------------
    def connect(self):
        logger.info("SOMBRA: sin conexión con el PLC; estado virtual inicial %s", self.state)

    def disconnect(self):
        pass

    def is_connected(self):
        return False

    def ensure_connected(self):
        pass

    # --- every PLC access is blocked ------------------------------------------------------------------
    read_bool = _blocked
    _write_bool_db = _blocked
    read_alarms = _blocked
    set_cold_mode = _blocked
    set_heat_mode = _blocked
    set_parada_mode = _blocked
    set_automatic_mode = _blocked
    _write_none_mode = _blocked
    _sequence_obrir = _blocked
    _sequence_tancar = _blocked
    start_alarm_monitor = _blocked

    def any_alarm_active(self):
        return False

    def close_doors(self):
        logger.info("SOMBRA: no se cierran tapas (sin PLC)")

    # --- virtual state ----------------------------------------------------------------------------------
    def read_actuators_state(self):
        combination = combination_of(self.state)
        bools = []
        for name, label in zip(_ORDER, combination or ("A",) * 6):
            bools.extend(_BOOLS[name][label])
        keys = ["ActManB1", "ActManMarxaB1", "ActManB2", "ActManMarxaB2",
                "ActManEV1", "ActManMarxaEV1", "ActManStopEV1", "ActManEV2", "ActManMarxaEV2", "ActManStopEV2",
                "ActManTH", "ActManMarxaTH", "ActManTV", "ActManMarxaTV"]
        return dict(zip(keys, bools))

    def get_system_state(self):
        _, bools = super().get_system_state()
        return self.state, bools

    def read_hour(self, db_number, direccion):
        """Weekly schedule from the configuration, zero-padded so string comparisons hold."""
        if db_number == 80 and direccion in _HEAT_START:
            return self.schedule["heat_start"]
        if db_number == 80 and direccion in _HEAT_END:
            return self.schedule["heat_end"]
        if db_number == 83 and direccion in _PARADA_END:
            return self.schedule["parada_end"]
        raise RuntimeError(f"shadow mode: no schedule for DB{db_number}.{direccion}")

    def read_cover_position(self):
        return ""          # nothing is measured in shadow mode

    def final_write_to_plc_nn_mode(self, estado, combinaciones_bool):
        if estado != self.state:
            logger.info("SOMBRA: se aplicaría %s (antes %s). No se escribe en el PLC.", estado, self.state)
        if estado is not None:
            self.state = estado


def make_plc(plc_config):
    """PLCShadow or PLCController according to plc_config["mode"] ("shadow" by default)."""
    mode = plc_config.get("mode", "shadow")
    if mode == "shadow":
        shadow = plc_config.get("shadow", {})
        return PLCShadow(initial_state=shadow.get("initial_state", "Parada"), schedule=shadow.get("schedule"))
    if mode == "real":
        return PLCController(ip=plc_config["ip"], rack=plc_config.get("rack", 0), slot=plc_config.get("slot", 1))
    raise ValueError(f"plc.mode must be 'shadow' or 'real', not {mode!r}")
