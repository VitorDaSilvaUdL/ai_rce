import time
import threading
import logging
import datetime

import snap7
from snap7.util import set_bool, get_bool, get_dword
from snap7.types import Areas
from tqdm import tqdm

import logging
logging.getLogger("snap7").setLevel(logging.CRITICAL)

logging.basicConfig(
    level=logging.DEBUG,
    # format="%(asctime)s - %(message)s",
    format="%(asctime)s [%(levelname)s] %(filename)s:%(lineno)d - %(funcName)s() - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)

# ------------------- Mapeos de estados (portados de RadInterface) -------------------
# Cada tupla representa el estado de los actuadores del sistema en este orden:
# (B1, B2, EV1, EV2, TH, TV)
#
# Cada código significa:
#   A  = Automático (el PLC controla el actuador)
#   MP = Manual Paro  (manual stop, el actuador está parado en manual)
#   MM = Manual Marxa (manual run, el actuador está en marcha en manual)
#
# Ejemplo: ('MP', 'MP', 'MP', 'MM', 'MP', 'MP')
#   B1  -> MP  (bomba 1 parada en manual)
#   B2  -> MP  (bomba 2 parada en manual)
#   EV1 -> MP  (electroválvula 1 parada en manual)
#   EV2 -> MM  (electroválvula 2 en marcha manual)
#   TH  -> MP  (tapa horizontal parada en manual)
#   TV  -> MP  (tapa vertical parada en manual)
'''
Ejemplo para ColdMode
ColdMode1 → bombas en automático + B2 en marcha manual
ColdMode2 → bombas todas en marcha manual

Ejemplo para HeatMode
HeatMode1 → secuencia de apertura normal
HeatMode2 → motores ya en movimiento o EVs ya activos
'''
SystemCase = {
    # Modo nocturno
    ("A", "MM", "MM", "MP", "MM", "MM"): "ColdMode1",
    ("MM", "MM", "MM", "MP", "MM", "MM"): "ColdMode2",

     ("A",  "MM", "MM", "MP", "MP", "MP"): "ColdMode1",  # ← NUEVA
     ("MM", "MM", "MM", "MP", "MP", "MP"): "ColdMode2",  # ← NUEVA

    # Modo diurno
    ("A", "MP", "MP", "MM", "MP", "MP"): "HeatMode1",
    ("MM", "MP", "MP", "MM", "MP", "MP"): "HeatMode2",

    # Modo Automático
    ("A", "A", "A", "A", "A", "A"): "AutomaticMode",

    # Modo Parada
    ("MP", "MP", "MP", "MM", "MP", "MP"): "Parada"
}

# Configuración de cada actuador según combinación de bools
CONFIG_ACTUADORES = {
    "B1": {
        (True, True): "MM",
        (True, False): "MP",
        (False, False): "A",
        (False, True): None,
    },
    "B2": {
        (True, True): "MM",
        (True, False): "MP",
        (False, False): "A",
        (False, True): None,
    },
    "EV1": {
        (True, True, False): "MM",
        (True, False, True): "MP",
        (False, False, False): "A",
    },
    "EV2": {
        (True, True, False): "MM",
        (True, False, True): "MP",
        (False, False, False): "A",
    },
    "TapaHoritzontal": {
        (True, True): "MM",
        (True, False): "MP",
        (False, False): "A",
        (False, True): None,
    },
    "TapaVertical": {
        (True, True): "MM",
        (True, False): "MP",
        (False, False): "A",
        (False, True): None,
    },
}


class PLCController:
    """
    Controlador de alto nivel del PLC.

    - Gestiona conexión.
    - Lee estados de actuadores.
    - Reconstruye el estado lógico del sistema (ColdMode, HeatMode, Automatic, Parada).
    - Aplica modos (frío, calor, parada, automático).
    - Implementa la lógica de decisión de estado que antes estaba en RadInterface.
    """

    TEST_MODE = False  # por defecto producción

    # Lista de alarmas: (nombre, db_number, byte_index, bit_index)
    ALARM_BITS = [
        ("lluvia", 808, 160, 2),  # DB808.DBX160.2
        ("viento", 808, 42, 2),   # DB808.DBX42.2
    ]

    def __init__(self, ip: str, rack: int = 0, slot: int = 1, name: str = "PLC", test_mode: bool = False) -> None:
        self.ip = ip
        self.rack = rack
        self.slot = slot
        self.name = name
        self.client: snap7.client.Client | None = None

        # Estado de alarmas
        self.alarm_active = False
        self._alarm_thread: threading.Thread | None = None
        self._alarm_stop_event = threading.Event()

        self._client_lock = threading.Lock()

        # if not PLCController.TEST_MODE:
        #     PLCController.TEST_MODE = test_mode
        # if PLCController.TEST_MODE:
        #     logging.warning("PLC working in TEST_MODE. TEST_MODE=ON")
        

    # ------------------- conexión -------------------

    def connect(self) -> None:
        """Conecta al PLC si no está ya conectado."""
        with self._client_lock:
            if self.client is None:
                self.client = snap7.client.Client()

            if not self.client.get_connected():
                self.client.connect(self.ip, self.rack, self.slot)

            if not self.client.get_connected():
                raise RuntimeError(f"No se pudo conectar al PLC {self.name} ({self.ip})")

        logging.info(f"Conectado al PLC {self.name} ({self.ip})")

        # INICIO AUTOMÁTICO DEL MONITOR DE ALARMAS
        self.start_alarm_monitor()

    def disconnect(self) -> None:
        """Desconecta del PLC (si está conectado)."""
        with self._client_lock:
            if self.client is not None and self.client.get_connected():
                        
                #PARADA AUTOMÁTICA DEL MONITOR DE ALARMAS
                self.stop_alarm_monitor()

                if self.client:
                    self.client.disconnect()
                    self.client.destroy()
                    self.client = None
                
            logging.info(f"Desconectado del PLC {self.name} ({self.ip})")

    def is_connected(self) -> bool:
        return self.client is not None and self.client.get_connected()

    def ensure_connected(self) -> None:
        """Reconecta automáticamente si hiciera falta."""
        if not self.is_connected():
            self.connect()

    # ------------------- helpers de lectura -------------------

    def read_bool(self, db_number: int, byte_index: int, bit_index: int) -> bool:
        """Lee un booleano de un DB."""
        self.ensure_connected()
        
        if PLCController.TEST_MODE:
            # En modo test no esperamos realmente
            logging.info(f"[TEST] Simulating read operation: {[db_number, byte_index, bit_index]}.")
            return True
        
        with self._client_lock:
            data = self.client.db_read(db_number, byte_index, 1)
        return get_bool(data, 0, bit_index)

    def read_hour(self, db_number: int, direccion: int) -> str:
        """
        Lee una hora almacenada como DWORD en ms y la devuelve como 'H:MM:SS'.

        Equivalente a readHour de RadInterface.
        """
        self.ensure_connected()

        if PLCController.TEST_MODE:
            # En modo test no esperamos realmente
            logging.info(f"[TEST] Simulating read_hour operation: {[db_number, direccion]}.")
            now = datetime.now()
            return f"{now.hour}:{now.minute:02}:{now.second:02}"
            
        with self._client_lock:
            raw = self.client.read_area(Areas.DB, db_number, direccion, 4)
        data = get_dword(raw, 0)

        segundo = int(data / 1000) % 60   # de ms a seg
        minuto = int(data / 60000) % 60   # de ms a min
        hora = int(data // 3600000) % 60  # de ms a hora

        tiempo = f"{hora}:{minuto:02}:{segundo:02}"
        return tiempo
    
        # ------------------- helpers de lectura de alarmas -------------------

    def read_alarms(self) -> dict:
        """
        Lee todas las alarmas configuradas en ALARM_BITS.
        Devuelve un dict {nombre_alarma: bool_activa}.
        """
        alarms = {}
        for name, db, byte, bit in self.ALARM_BITS:
            alarms[name] = self.read_bool(db, byte, bit)
        return alarms

    def any_alarm_active(self) -> bool:
        """Devuelve True si alguna alarma está activa."""
        alarms = self.read_alarms()
        return any(alarms.values())

    # ------------------- monitor de alarmas en background -------------------

    def _alarm_monitor_loop(self, poll_s: float = 1.0) -> None:
        """
        Hilo en background que vigila las alarmas de lluvia/viento.
        Si se activa alguna:
            - marca alarm_active = True
            - fuerza el cierre de la puerta (TH/TV) llamando a _sequence_tancar
        Mientras haya alarmas activas no se permitirá abrir la puerta.
        """
        logging.info("Inicio del hilo de monitorización de alarmas (lluvia/viento).")

        if(PLCController.TEST_MODE):
            poll_s=30 #30s if it is a test

        while not self._alarm_stop_event.is_set():
            try:
                alarms = self.read_alarms()
                active = any(alarms.values())

                if active and not self.alarm_active:
                    nombres = ", ".join([n for n, v in alarms.items() if v])
                    logging.warning(
                        f"Alarma(s) activada(s): {nombres}. "
                        "Forzando cierre de puerta TH/TV y bloqueando apertura."
                    )
                    self.alarm_active = True
                    # Lanzamos el cierre en otro hilo para no bloquear el monitor
                    self.run_async(self._sequence_tancar)

                elif not active and self.alarm_active:
                    logging.info("Alarmas desactivadas. Se permite de nuevo la apertura de la puerta.")
                    self.alarm_active = False

            except Exception:
                logging.exception("Error en el hilo de monitorización de alarmas.")

            time.sleep(poll_s)

        logging.info("Hilo de monitorización de alarmas detenido.")

    def start_alarm_monitor(self, poll_s: float = 1.0) -> None:
        """
        Arranca el hilo en background que vigila las alarmas.
        Debe llamarse después de connect().
        """
        if self._alarm_thread and self._alarm_thread.is_alive():
            return

        self._alarm_stop_event.clear()
        self._alarm_thread = threading.Thread(
            target=self._alarm_monitor_loop,
            args=(poll_s,),
            daemon=True,
        )
        self._alarm_thread.start()
        logging.info("Monitor de alarmas iniciado.")

    def stop_alarm_monitor(self) -> None:
        """Detiene el hilo de monitorización de alarmas."""
        self._alarm_stop_event.set()

    
    # ------------------- helpers de lectura específicos TH -------------------

    def is_th_open(self) -> bool:
        """
        Devuelve True si la tapa horizontal (TH) está abierta.
        Usa el final de carrera FcONHorizontal: DB10.DBX0.0
        """
        return self.read_bool(10, 0, 0)

    def is_th_closed(self) -> bool:
        """
        Devuelve True si la tapa horizontal (TH) está cerrada.
        Usa el final de carrera FcOFFHorizontal: DB10.DBX0.1
        """
        return self.read_bool(10, 0, 1)

    def wait_for_th_position(self, expected: str, timeout_s: float = 60.0, poll_s: float = 0.5) -> bool:
        """
        Espera a que la tapa horizontal (TH) llegue a la posición indicada.

        expected:
            "open"  -> esperar a FcONHorizontal (tapa abierta)
            "close" -> esperar a FcOFFHorizontal (tapa cerrada)

        Devuelve:
            True si llega a tiempo, False si se supera el timeout.
        """
        start = time.time()

        if expected == "open":
            check = self.is_th_open
            pos_str = "abierta"
        else:
            check = self.is_th_closed
            pos_str = "cerrada"

        while time.time() - start < timeout_s:
            if check():
                logging.info(f"Tapa horizontal TH en posición {pos_str}.")
                return True

            if PLCController.TEST_MODE:
                # En modo test no esperamos realmente
                logging.info(f"[TEST] Simulando TH {pos_str}.")
                return True

            time.sleep(poll_s)

        logging.error(f"Timeout esperando tapa horizontal TH {pos_str} (>{timeout_s}s).")
        return False

    def read_actuators_state(self) -> dict:
        """
        Devuelve un diccionario con el estado booleano de cada actuador.
        Equivalente al EstadoActuadores de RadInterface, pero con bools.
        """
        return {
            # B1 [DB500]
            "ActManB1": self.read_bool(500, 12, 0),
            "ActManMarxaB1": self.read_bool(500, 12, 1),

            # B2 [DB501]
            "ActManB2": self.read_bool(501, 12, 0),
            "ActManMarxaB2": self.read_bool(501, 12, 1),

            # EV1 [DB300]
            "ActManEV1": self.read_bool(300, 10, 0),
            "ActManMarxaEV1": self.read_bool(300, 10, 1),
            "ActManStopEV1": self.read_bool(300, 10, 2),

            # EV2 [DB301]
            "ActManEV2": self.read_bool(301, 10, 0),
            "ActManMarxaEV2": self.read_bool(301, 10, 1),
            "ActManStopEV2": self.read_bool(301, 10, 2),

            # Tapa Horizontal [DB503]
            "ActManTH": self.read_bool(503, 12, 0),
            "ActManMarxaTH": self.read_bool(503, 12, 1),

            # Tapa Vertical [DB504]
            "ActManTV": self.read_bool(504, 12, 0),
            "ActManMarxaTV": self.read_bool(504, 12, 1),
        }

    # ------------------- reconstrucción del estado lógico -------------------

    def _state_stablisher(self, act: dict):
        """
        Replica StateStablisher pero usando bools directamente.

        Devuelve:
            combination (tuple): estados ["A","MM","MM","MP","MM","MM"]
            valores_booleanos (list[bool]): lista plana de los bools en el mismo orden
        """
        orden = [
            'ActManB1', 'ActManMarxaB1',
            'ActManB2', 'ActManMarxaB2',
            'ActManEV1', 'ActManMarxaEV1', 'ActManStopEV1',
            'ActManEV2', 'ActManMarxaEV2', 'ActManStopEV2',
            'ActManTH', 'ActManMarxaTH',
            'ActManTV', 'ActManMarxaTV'
        ]

        valores_booleanos = [act[key] for key in orden]

        # Desempaquetamos según la misma estructura que en RadInterface
        EstadoB1 = CONFIG_ACTUADORES["B1"].get((valores_booleanos[0], valores_booleanos[1]))
        EstadoB2 = CONFIG_ACTUADORES["B2"].get((valores_booleanos[2], valores_booleanos[3]))
        EstadoEV1 = CONFIG_ACTUADORES["EV1"].get((valores_booleanos[4], valores_booleanos[5], valores_booleanos[6]))
        EstadoEV2 = CONFIG_ACTUADORES["EV2"].get((valores_booleanos[7], valores_booleanos[8], valores_booleanos[9]))
        EstadoTH = CONFIG_ACTUADORES["TapaHoritzontal"].get((valores_booleanos[10], valores_booleanos[11]))
        EstadoTV = CONFIG_ACTUADORES["TapaVertical"].get((valores_booleanos[12], valores_booleanos[13]))

        combination = (EstadoB1, EstadoB2, EstadoEV1, EstadoEV2, EstadoTH, EstadoTV)
        return combination, valores_booleanos

    def _check_system_type(self, combination):
        """
        Replica CheckSystemType: mapea la combinación a un estado global.
        """
        estado_sistema = SystemCase.get(combination)
        return estado_sistema  # puede ser None

    def get_system_state(self):
        """
        Lee el PLC, reconstruye la combinación de estados y devuelve:
            (EstadoSistema, lista_bool)

        EstadoSistema puede ser:
            'ColdMode1', 'ColdMode2', 'HeatMode1', 'HeatMode2',
            'AutomaticMode', 'Parada' o None.
        """
        act = self.read_actuators_state()
        comb, valores = self._state_stablisher(act)
        estado = self._check_system_type(comb)
        logging.info(f"Estado PLC detectado: {estado} (comb: {comb})")
        return estado, valores
    
    def get_current_mode(self) -> str:
        """
        Devuelve el modo actual del sistema en formato simple:
            'hot', 'cold', 'parada' o 'automatic'.

        Se basa en el estado lógico que devuelve get_system_state():
            - HeatMode1 / HeatMode2 -> 'hot'
            - ColdMode1 / ColdMode2 -> 'cold'
            - Parada                -> 'parada'
            - AutomaticMode         -> 'automatic'
        Si no se reconoce el estado, devuelve 'parada' por seguridad.
        """
        estado_sistema, _ = self.get_system_state()

        if estado_sistema in ("HeatMode1", "HeatMode2"):
            return "hot"
        elif estado_sistema in ("ColdMode1", "ColdMode2"):
            return "cold"
        elif estado_sistema == "AutomaticMode":
            return "automatic"
        elif estado_sistema == "Parada":
            return "parada"
        else:
            logging.warning(f"Modo PLC no reconocido en get_current_mode: {estado_sistema}")
            return "parada"

    # ------------------- helpers de escritura -------------------

    def _write_bool_db(self, db_number: int, byte_index: int, bit_index: int, value: bool) -> None:
        """Escribe un bit en un DB del PLC."""
        self.ensure_connected()

        if PLCController.TEST_MODE:
                # En modo test no esperamos realmente
                logging.info(f"[TEST] Simulating write operation: {[Areas.DB, db_number, byte_index, value]}.")
                return True
        
        with self._client_lock:
            data = self.client.read_area(Areas.DB, db_number, byte_index, 1)
            set_bool(data, 0, bit_index, value)
            self.client.write_area(Areas.DB, db_number, byte_index, data)

    # ------------------- helper de espera con tqdm -------------------

    @staticmethod
    def sleep_with_progress(seconds: int, desc: str = "Esperando") -> None:
        """
        Espera 'seconds' segundos mostrando una barra de progreso en consola.
        Bloquea el hilo actual, pero no la aplicación entera si se ejecuta en un thread aparte.
        """
        if PLCController.TEST_MODE:
            print(f"[TEST] Skip sleep ({seconds}s): {desc}")
            return

        for _ in tqdm(range(seconds), desc=desc, unit="s"):
            time.sleep(1)

    # ------------------- modos de funcionamiento básicos -------------------

    def set_automatic_mode(self) -> None:
        """Equivalente a WriteAutomaticMode."""
        logging.info("set_automatic_mode called.")

        # B1: Automatic
        self._write_bool_db(500, 12, 0, False)
        self._write_bool_db(500, 12, 1, False)
        # B2: Automatic
        self._write_bool_db(501, 12, 0, False)
        self._write_bool_db(501, 12, 1, False)
        # EV1: Automatic
        self._write_bool_db(300, 10, 0, False)
        self._write_bool_db(300, 10, 1, False)
        self._write_bool_db(300, 10, 2, False)
        # EV2: Automatic
        self._write_bool_db(301, 10, 0, False)
        self._write_bool_db(301, 10, 1, False)
        self._write_bool_db(301, 10, 2, False)
        # Tapa Horizontal: Automatic
        self._write_bool_db(503, 12, 0, False)
        self._write_bool_db(503, 12, 1, False)
        # Tapa Vertical: Automatic
        self._write_bool_db(504, 12, 0, False)
        self._write_bool_db(504, 12, 1, False)

    def _sequence_tancar(self) -> None:
        """Secuencia de cierre de tapas (tancar)."""
        logging.debug("_sequence_tancar called.")

        # Comprobar que la tapa horizontal está realmente abierta para arrancar
        if not self.wait_for_th_position("open", timeout_s=5):
            logging.error(
                "TH no ha llegado a fin de carrera de cerrada. "
                "Se aborta el movimiento de TV invers para evitar que quede enganchado."
            )
            return 

        # Tapa Vertical: ManualMarxa
        self._write_bool_db(504, 12, 0, True)
        self._write_bool_db(504, 12, 1, True)
        self._write_bool_db(504, 12, 2, False)

        self.sleep_with_progress(45, "Motor (45s)")

        # Tapa Horizontal: ManualParo
        self._write_bool_db(503, 12, 0, True)
        self._write_bool_db(503, 12, 1, False)
        self._write_bool_db(503, 12, 2, True)

        # self.sleep_with_progress(200, "Pistones invers (200s)")
        
        # Comprobar que la tapa horizontal está realmente cerrada
        if not self.wait_for_th_position("close", timeout_s=200.0):
            logging.error(
                "TH no ha llegado a fin de carrera de cerrada. "
                "Se aborta el movimiento de TV invers para evitar que quede enganchado."
            )
            return  # no bajamos pistones (TV invers)
        
        # Tapa Horizontal: ManualParo
        self._write_bool_db(503, 12, 0, True)
        self._write_bool_db(503, 12, 1, False)
        self._write_bool_db(503, 12, 2, False)
        
        self.sleep_with_progress(5, "Pistones (5s)")

        # Tapa Vertical: ManualMarxa INVERS
        self._write_bool_db(504, 12, 0, True)
        self._write_bool_db(504, 12, 1, False)
        self._write_bool_db(504, 12, 2, True) 

    def _sequence_obrir(self) -> None:
        """Secuencia de apertura de tapas (obrir)."""
        logging.debug("_sequence_obrir called.")

        #  Si hay alarma activa, no permitimos abrir
        if self.alarm_active or self.any_alarm_active():
            logging.warning(
                "Intento de abrir la puerta (TH) con alarma de lluvia/viento activa. "
                "Operación bloqueada."
            )
            return

        # Comprobar que la tapa horizontal está realmente abierta para arrancar
        if not self.wait_for_th_position("close", timeout_s=5):
            logging.error(
                "TH no ha llegado a fin de carrera de cerrada. "
                "Se aborta el movimiento de TV invers para evitar que quede enganchado."
            )
            return

        # 1) Tapa Vertical: ManualMarxa (subir pistones)
        self._write_bool_db(504, 12, 0, True)
        self._write_bool_db(504, 12, 1, True)
        self._write_bool_db(504, 12, 2, False)

        self.sleep_with_progress(45, "Motor (45s)")

        # 2) Tapa Horizontal: ManualMarxa (abrir tapa horizontal)
        self._write_bool_db(503, 12, 0, True)
        self._write_bool_db(503, 12, 1, True)
        self._write_bool_db(503, 12, 2, False)

        # self.sleep_with_progress(200, "Pistones invers (200s)")

        # 4) NUEVO: esperar a que la tapa horizontal esté realmente ABIERTA
        if not self.wait_for_th_position("open", timeout_s=200):
            logging.error(
                "TH no ha llegado al final de carrera de abierta. "
                "Se aborta la secuencia para evitar problemas mecánicos."
            )
            return
        
        logging.info("TH está completamente abierta. Continuando con secuencia de TV.")

        # 3) Parar el motor de TH
        self._write_bool_db(503, 12, 0, True)
        self._write_bool_db(503, 12, 1, False)
        self._write_bool_db(503, 12, 2, False)

        self.sleep_with_progress(5, "Pistones (5s)")

        # 5) Tapa Vertical: ManualParo (bajar pistones / invertir TV)
        self._write_bool_db(504, 12, 0, True)
        self._write_bool_db(504, 12, 1, False)
        self._write_bool_db(504, 12, 2, True)

        logging.info("Secuencia de apertura completada.")

    def set_cold_mode(self) -> None:
        """Equivalente a WriteColdMode (ColdMode1)."""
        logging.info("set_cold_mode called.")

        # B1: Automatic
        self._write_bool_db(500, 12, 0, False)
        self._write_bool_db(500, 12, 1, False)

        # B2: ManualMarxa
        self._write_bool_db(501, 12, 0, True)
        self._write_bool_db(501, 12, 1, True)

        # EV1: ManualMarxa
        self._write_bool_db(300, 10, 0, True)
        self._write_bool_db(300, 10, 1, True)
        self._write_bool_db(300, 10, 2, False)

        # EV2: ManualParo
        self._write_bool_db(301, 10, 0, True)
        self._write_bool_db(301, 10, 1, False)
        self._write_bool_db(301, 10, 2, True)

        # # Tapa Horizontal: ManualMarxa
        # self._write_bool_db(503, 12, 0, True)
        # self._write_bool_db(503, 12, 1, True)

        # # Tapa Vertical: ManualMarxa
        # self._write_bool_db(504, 12, 0, True)
        # self._write_bool_db(504, 12, 1, True)

        self._sequence_obrir()

    def set_test(self) -> None:
        """Equivalente a WriteHeatMode (HeatMode1)."""
        logging.info("set_heat_mode called.")

        # B1: Automatic
        self._write_bool_db(500, 12, 0, False)

    def set_heat_mode(self) -> None:
        """Equivalente a WriteHeatMode (HeatMode1)."""
        logging.info("set_heat_mode called.")

        # B1: Automatic
        self._write_bool_db(500, 12, 0, False)
        self._write_bool_db(500, 12, 1, False)

        # B2: ManualParo
        self._write_bool_db(501, 12, 0, True)
        self._write_bool_db(501, 12, 1, False)

        # EV1: ManualParo
        self._write_bool_db(300, 10, 0, True)
        self._write_bool_db(300, 10, 1, False)
        self._write_bool_db(300, 10, 2, True)

        # EV2: ManualMarxa
        self._write_bool_db(301, 10, 0, True)
        self._write_bool_db(301, 10, 1, True)
        self._write_bool_db(301, 10, 2, False)

        self._sequence_tancar()

    def set_parada_mode(self) -> None:
        """Equivalente a WriteParadaMode."""
        logging.info("set_parada_mode called.")

        # B1: MarxaParo
        self._write_bool_db(500, 12, 0, True)
        self._write_bool_db(500, 12, 1, False)

        # B2: MarxaParo
        self._write_bool_db(501, 12, 0, True)
        self._write_bool_db(501, 12, 1, False)

        # EV1: MarxaParo
        self._write_bool_db(300, 10, 0, True)
        self._write_bool_db(300, 10, 1, False)
        self._write_bool_db(300, 10, 2, True)

        # EV2
        self._write_bool_db(301, 10, 0, True)
        self._write_bool_db(301, 10, 1, True)
        self._write_bool_db(301, 10, 2, False)

        # Tapa Horizontal
        self._write_bool_db(503, 12, 0, True)
        self._write_bool_db(503, 12, 1, False)

        # Tapa Vertical
        self._write_bool_db(504, 12, 0, True)
        self._write_bool_db(504, 12, 1, False)

        self._sequence_tancar()

    # ------------------- modo "None" (escritura directa de combinación) -------------------

    def _write_none_mode(self, combinaciones_bool):
        """
        Equivalente a WriteNoneMode: escribe la combinación de bools tal cual
        en los DBs, respetando el orden del array de 14 bools.
        """
        # B1
        self._write_bool_db(500, 12, 0, combinaciones_bool[0])
        self._write_bool_db(500, 12, 1, combinaciones_bool[1])

        # B2
        self._write_bool_db(501, 12, 0, combinaciones_bool[2])
        self._write_bool_db(501, 12, 1, combinaciones_bool[3])

        # EV1
        self._write_bool_db(300, 10, 0, combinaciones_bool[4])
        self._write_bool_db(300, 10, 1, combinaciones_bool[5])
        self._write_bool_db(300, 10, 2, combinaciones_bool[6])

        # EV2
        self._write_bool_db(301, 10, 0, combinaciones_bool[7])
        self._write_bool_db(301, 10, 1, combinaciones_bool[8])
        self._write_bool_db(301, 10, 2, combinaciones_bool[9])

        # Tapa Horizontal
        self._write_bool_db(503, 12, 0, combinaciones_bool[10])
        self._write_bool_db(503, 12, 1, combinaciones_bool[11])

        # Tapa Vertical
        self._write_bool_db(504, 12, 0, combinaciones_bool[12])
        self._write_bool_db(504, 12, 1, combinaciones_bool[13])

    def final_write_to_plc_nn_mode(self, estado: str, combinaciones_bool):
        """
        Equivalente a FinalWritetoPLCNNmode:
        Aplica el estado resultante en el PLC.
        """
        logging.info(f"final_write_to_plc_nn_mode estado={estado}")
        if estado == 'HeatMode1':
            self.set_heat_mode()
        elif estado == 'ColdMode1':
            self.set_cold_mode()
        elif estado == 'AutomaticMode':
            self.set_automatic_mode()
        elif estado == 'Parada':
            self.set_parada_mode()
        else:
            # Estado None u otros → escribir combinación tal cual
            self._write_none_mode(combinaciones_bool)

    # ------------------- lógica de decisión (YES / NO / PARADA / lluvia-viento) -------------------

    def decision_none_or_parada(self) -> str:
        """
        Replica DecisionNoneorParada: decide nuevo estado si está en None o Parada
        según el día de la semana y horarios configurados en DB80/83.
        """
        Dia = datetime.datetime.now().strftime('%A')
        HoraMinSecActual = datetime.datetime.now().strftime("%H:%M:%S")

        # Por claridad: mantengo la misma estructura que en RadInterface.
        if Dia == 'Monday':
            if self.read_hour(80, 66) > HoraMinSecActual > self.read_hour(80, 62):
                return 'HeatMode1'
            elif self.read_hour(80, 66) < HoraMinSecActual < self.read_hour(83, 62):
                return 'Parada'
            else:
                return 'ColdMode1'

        elif Dia == 'Tuesday':
            if self.read_hour(80, 74) > HoraMinSecActual > self.read_hour(80, 70):
                return 'HeatMode1'
            elif self.read_hour(80, 74) < HoraMinSecActual < self.read_hour(83, 70):
                return 'Parada'
            else:
                return 'ColdMode1'

        elif Dia == 'Wednesday':
            if self.read_hour(80, 82) > HoraMinSecActual > self.read_hour(80, 78):
                return 'HeatMode1'
            elif self.read_hour(80, 82) < HoraMinSecActual < self.read_hour(83, 78):
                return 'Parada'
            else:
                return 'ColdMode1'

        elif Dia == 'Thursday':
            if self.read_hour(80, 90) > HoraMinSecActual > self.read_hour(80, 86):
                return 'HeatMode1'
            elif self.read_hour(80, 90) < HoraMinSecActual < self.read_hour(83, 86):
                return 'Parada'
            else:
                return 'ColdMode1'

        elif Dia == 'Friday':
            if self.read_hour(80, 98) > HoraMinSecActual > self.read_hour(80, 94):
                return 'HeatMode1'
            elif self.read_hour(80, 98) < HoraMinSecActual < self.read_hour(83, 94):
                return 'Parada'
            else:
                return 'ColdMode1'

        elif Dia == 'Saturday' or Dia == 'Sunday':
            return 'Parada'

        return 'Parada'

    def state_decision_yes(self, estado_actual: str) -> str:
        """
        Equivalente a StateDecisionChangerYES.
        """
        if estado_actual in ('ColdMode1', 'ColdMode2'):
            return 'HeatMode1'
        elif estado_actual in ('HeatMode1', 'HeatMode2'):
            return 'ColdMode1'
        elif estado_actual == 'AutomaticMode':
            return 'AutomaticMode'
        elif estado_actual is None or estado_actual == 'Parada':
            return self.decision_none_or_parada()
        else:
            return estado_actual

    def state_decision_no(self, estado_actual: str) -> str:
        """Equivalente a StateDecisionChangerNO."""
        return estado_actual

    def state_decision_rain_wind(self) -> str:
        """Equivalente a StateDecisionChangerRAINWIND."""
        return 'Parada'

    def decide_next_state_from_nn(self, respuesta_nn: str, estado_actual: str) -> str:
        """
        Decide el siguiente estado en función de la respuesta de la red neuronal
        ('si', 'no', 'Parada' o cualquier otro valor).
        """
        r = (respuesta_nn or "").strip().lower()

        if r in ('si', 'yes'):
            return self.state_decision_yes(estado_actual)
        elif r == 'no':
            return self.state_decision_no(estado_actual)
        elif r == 'parada':
            return 'Parada'
        else:
            return self.state_decision_rain_wind()

    # ------------------- ejecución según modo simple (hot/cold/parada/automatic) -------------------

    def exec_mode(self, mode: str) -> None:
        """
        Ejecuta un modo simple:
            'hot'      -> set_heat_mode
            'cold'     -> set_cold_mode
            'parada'   -> set_parada_mode
            'automatic'-> set_automatic_mode
        """
        logging.info(f"exec_mode called. mode[{mode}]")

        mode_actions = {
            "hot": self.set_heat_mode,
            "cold": self.set_cold_mode,
            "parada": self.set_parada_mode,
            "automatic": self.set_automatic_mode,
        }

        fn = mode_actions.get(mode.lower())
        if fn is None:
            logging.error(f"Modo desconocido en exec_mode: {mode}")
            return

        fn()

    # ------------------- ejecución en segundo plano -------------------

    def run_async(self, fn, *args, **kwargs) -> None:
        """
        Ejecuta 'fn' (por ejemplo set_cold_mode) en un thread aparte,
        para no bloquear la GUI ni el hilo principal.
        """
        thread = threading.Thread(target=fn, args=args, kwargs=kwargs, daemon=True)
        thread.start()