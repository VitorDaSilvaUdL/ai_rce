#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
PLC dummy (Snap7) para probar tu RadInterface/cliente.
- Expone MK y DBs que usa tu cliente.
- Inicializa valores plausibles (temperaturas, humedad, viento, solarímetro, horarios).
- Loguea eventos (conexión, lecturas, escrituras, etc.).
- Simula variaciones suaves en sensores.
- Simula además el movimiento de la tapa horizontal (TH) y los finales de carrera:
    - FcONHorizontal  -> DB10.DBX0.0 (TH abierta)
    - FcOFFHorizontal -> DB10.DBX0.1 (TH cerrada)
"""

import logging
from logging.handlers import RotatingFileHandler
import signal
import sys
import time
import math
import random

import snap7
from snap7 import types
from snap7.server import Server
from snap7.util import set_real, set_dword


# =========================
# CONFIG
# =========================

# Usa 102 si tu cliente no especifica puerto (Windows: ejecutar como Admin)
TCP_PORT = 102
LOG_FILE = "snap7_server.log"

# Tamaños mínimos por DB según lecturas/escrituras de tu cliente
DB_SIZES = {
    # Sensores @ offset 90 (REAL)
    100: 128, 101: 128, 102: 128, 103: 128,
    105: 128, 106: 128, 107: 128,  # humedad y viento
    112: 128, 113: 128, 114: 128, 115: 128,
    116: 128, 117: 128, 118: 128, 119: 128,
    120: 128, 121: 128, 123: 128,

    # Pirgeómetro / Solarímetro
    # DB1000: REAL @ 0, 8, 16
    1000: 20,
    # DB1001: REAL @ 24
    1001: 28,

    # Finales de carrera TH (DB10.DBX0.0 y DB10.DBX0.1)
    10: 2,

    # Alarmas
    808: 200,  # lluvia 160 / viento 42

    # Horarios (DWORD en ms)
    80: 128,
    83: 128,

    # Actuadores (bits leídos/escritos)
    300: 16, 301: 16, 500: 16, 501: 16, 503: 16, 504: 16,
}

# Merkers: necesitamos al menos el byte 102 (bit 0)
MK_SIZE = 180

# Sensores con lectura en offset 90 (REAL)
SENSOR_DBS_OFFSET90 = [
    100, 101, 102, 103, 105, 106, 107,
    112, 113, 114, 115, 116, 117, 118, 119,
    120, 121, 123,
]


# =========================
# LOGGING
# =========================

logger = logging.getLogger("snap7-dummy-server")
logger.setLevel(logging.INFO)

_fmt = logging.Formatter("%(asctime)s | %(levelname)s | %(message)s")
_fh = RotatingFileHandler(LOG_FILE, maxBytes=1_000_000, backupCount=3, encoding="utf-8")
_fh.setFormatter(_fmt)
_sh = logging.StreamHandler(sys.stdout)
_sh.setFormatter(_fmt)

logger.addHandler(_fh)
logger.addHandler(_sh)


# =========================
# UTIL BUFFERS / ESCRITURA
# =========================

def make_buffer(size_bytes: int):
    """Crea un buffer ctypes (array de bytes) inicializado a 0."""
    ByteArray = (types.wordlen_to_ctypes[types.S7WLByte] * size_bytes)
    return ByteArray()


def write_real_to_cbuf(cbuf, offset: int, value: float):
    """Escribe REAL en offset dentro de un buffer ctypes."""
    ba = bytearray(memoryview(cbuf).cast("B"))
    set_real(ba, offset, float(value))
    mv = memoryview(cbuf).cast("B")
    mv[offset:offset + 4] = ba[offset:offset + 4]


def write_dword_to_cbuf(cbuf, offset: int, value: int):
    """Escribe DWORD (uint32) en offset dentro de un buffer ctypes."""
    ba = bytearray(memoryview(cbuf).cast("B"))
    set_dword(ba, offset, int(value))
    mv = memoryview(cbuf).cast("B")
    mv[offset:offset + 4] = ba[offset:offset + 4]


def hms_to_ms(h, m, s) -> int:
    return (h * 3600 + m * 60 + s) * 1000


# =========================
# SERVIDOR
# =========================

class DummyS7Server:
    def __init__(self, tcp_port: int = TCP_PORT):
        self.tcp_port = tcp_port
        self.server = Server(log=False)
        self.db_buffers: dict[int, object] = {}
        self.mk_buffer = None
        self._running = False
        self._t0 = time.monotonic()

        # ---- Estado simulado de la tapa horizontal ----
        # 0.0 = completamente cerrada, 1.0 = completamente abierta
        self.th_position: float = 0.0
        self._th_moving: str | None = None      # "opening", "closing" o None
        self._th_move_start: float | None = None
        # Tiempo "virtual" de recorrido completo (s) para que wait_for_th_position
        # funcione de forma realista pero rápida
        self.TH_TRAVEL_TIME: float = 3.0

    # ------------- helpers internos TH -------------

    def _set_th_fc_bits(self):
        """
        Actualiza DB10.DBX0.0 (FcONHorizontal) y DB10.DBX0.1 (FcOFFHorizontal)
        en función de self.th_position.
        - th_position >= 0.5 -> TH abierta:  FcON=1, FcOFF=0
        - th_position <  0.5 -> TH cerrada: FcON=0, FcOFF=1
        """
        buf = self.db_buffers.get(10)
        if buf is None:
            return

        mv = memoryview(buf).cast("B")
        b0 = mv[0]

        # Limpia bits 0 y 1
        b0 &= ~0b00000011

        if self.th_position >= 0.5:
            # Abierta
            b0 |= 0b00000001  # bit 0 = 1
        else:
            # Cerrada
            b0 |= 0b00000010  # bit 1 = 1

        mv[0] = b0

    def _update_th_motion_from_actuators(self):
        """
        Lee DB503.DBX12.1 y DB503.DBX12.2 para ver si se está mandando
        movimiento a la TH y, en función de eso, simula el avance y
        actualiza los finales de carrera en DB10.
        """
        buf_th = self.db_buffers.get(503)
        if buf_th is None:
            return

        mv_th = memoryview(buf_th).cast("B")
        # DB503.DBX12.x -> byte 12
        byte12 = mv_th[12]

        act_marxa = (byte12 >> 1) & 0b1      # DBX12.1
        act_marxa_inv = (byte12 >> 2) & 0b1  # DBX12.2 (inverso)

        now = time.monotonic()

        # Determinar orden actual
        if act_marxa and not act_marxa_inv:
            # Apertura (modo normal, obrir)
            if self._th_moving != "opening":
                logger.info("Dummy: TH empieza a abrirse (mandos DB503.DBX12.1=1, DB503.DBX12.2=0)")
                self._th_moving = "opening"
                self._th_move_start = now
        elif act_marxa_inv and not act_marxa:
            # Cierre (modo inverso, tancar)
            if self._th_moving != "closing":
                logger.info("Dummy: TH empieza a cerrarse (mandos DB503.DBX12.1=0, DB503.DBX12.2=1)")
                self._th_moving = "closing"
                self._th_move_start = now
        else:
            # Sin mando explícito → paramos el movimiento
            if self._th_moving is not None:
                logger.info("Dummy: mandos de TH en reposo, movimiento detenido.")
            self._th_moving = None
            self._th_move_start = None

        # Si hay movimiento, vemos si ha completado el recorrido virtual
        if self._th_moving and self._th_move_start is not None:
            elapsed = now - self._th_move_start
            if elapsed >= self.TH_TRAVEL_TIME:
                if self._th_moving == "opening":
                    self.th_position = 1.0
                    logger.info("Dummy: TH simulada COMPLETAMENTE ABIERTA (FcON=1, FcOFF=0).")
                elif self._th_moving == "closing":
                    self.th_position = 0.0
                    logger.info("Dummy: TH simulada COMPLETAMENTE CERRADA (FcON=0, FcOFF=1).")

                # Movimiento completado
                self._th_moving = None
                self._th_move_start = None

        # Actualiza los finales de carrera en DB10 según th_position
        self._set_th_fc_bits()

    # ----------------------------------------------

    def create(self):
        self.server.create()

        # MK
        self.mk_buffer = make_buffer(MK_SIZE)
        self.server.register_area(types.srvAreaMK, 0, self.mk_buffer)
        # Encender MK[102].0 para ButtonRNState=True
        mv_mk = memoryview(self.mk_buffer).cast("B")
        mv_mk[102] |= 0b00000001

        # DBs
        for dbn, size in DB_SIZES.items():
            buf = make_buffer(size)
            self.server.register_area(types.srvAreaDB, dbn, buf)
            self.db_buffers[dbn] = buf

        logger.info("Áreas MK y DBs registradas.")

    def start(self):
        self.server.start(tcpport=self.tcp_port)
        self._running = True
        logger.info("Servidor Snap7 iniciado en TCP %s. Esperando conexiones...", self.tcp_port)

    def stop(self):
        self._running = False
        try:
            self.server.stop()
        except Exception:
            pass
        try:
            self.server.destroy()
        except Exception:
            pass
        logger.info("Servidor detenido y destruido.")

    def init_values(self):
        # Valores por defecto para sensores @90
        defaults = {
            100: 21.5, 101: 20.8, 102: 20.1, 103: 19.9,
            105: 55.0,  # Humedad exterior (%)
            106: 18.3,
            107: 3.5,   # Velocidad viento (m/s)
            112: 22.0, 113: 22.5, 114: 21.2, 115: 20.0,
            116: 19.7, 117: 21.1, 118: 21.3, 119: 21.0,
            120: 20.6, 121: 20.4, 123: 22.2,
        }
        for dbn, val in defaults.items():
            write_real_to_cbuf(self.db_buffers[dbn], 90, val)

        # Solarímetro / Pirgeómetro
        write_real_to_cbuf(self.db_buffers[1000], 0, 350.0)   # PIRG_VALUE_C
        write_real_to_cbuf(self.db_buffers[1000], 8, 150.0)   # PIRG_VALUE
        write_real_to_cbuf(self.db_buffers[1000], 16, 200.0)  # PIRG_AV_VALUE
        write_real_to_cbuf(self.db_buffers[1001], 24, 500.0)  # SolIO_RAW

        # Horarios (ejemplo)
        schedule_80 = {
            62: hms_to_ms(8, 0, 0),  66: hms_to_ms(14, 0, 0),  # Lunes
            70: hms_to_ms(8, 0, 0),  74: hms_to_ms(14, 0, 0),  # Martes
            78: hms_to_ms(8, 0, 0),  82: hms_to_ms(14, 0, 0),  # Miércoles
            86: hms_to_ms(8, 0, 0),  90: hms_to_ms(14, 0, 0),  # Jueves
            94: hms_to_ms(8, 0, 0),  98: hms_to_ms(14, 0, 0),  # Viernes
        }
        for off, val in schedule_80.items():
            write_dword_to_cbuf(self.db_buffers[80], off, val)

        schedule_83 = {
            62: hms_to_ms(16, 0, 0),
            70: hms_to_ms(16, 0, 0),
            78: hms_to_ms(16, 0, 0),
            86: hms_to_ms(16, 0, 0),
            94: hms_to_ms(16, 0, 0),
        }
        for off, val in schedule_83.items():
            write_dword_to_cbuf(self.db_buffers[83], off, val)

        # Estado inicial de la TH: asumimos CERRADA
        self.th_position = 0.0
        self._set_th_fc_bits()

        logger.info("Valores iniciales escritos en DBs, MK y finales de carrera TH.")

    def simulate(self):
        """Variaciones suaves de sensores + simulación de la tapa horizontal (TH)."""
        t = time.monotonic() - self._t0

        # Sensores temperatura/humedad/viento
        for dbn in SENSOR_DBS_OFFSET90:
            base = 20.0 + (dbn % 5)
            wave = 0.8 * math.sin(t / 60.0 + dbn)
            noise = random.uniform(-0.05, 0.05)
            val = base + wave + noise
            write_real_to_cbuf(self.db_buffers[dbn], 90, val)

        # Humedad y viento con curvas más "realistas"
        write_real_to_cbuf(self.db_buffers[105], 90, max(0.0, 55.0 + 5.0 * math.sin(t / 70.0)))
        write_real_to_cbuf(self.db_buffers[107], 90, max(0.0, 3.5 + 1.2 * math.sin(t / 45.0)))

        # Solarímetro / pirgeómetro simulados
        write_real_to_cbuf(self.db_buffers[1001], 24, max(0.0, 500.0 + 50.0 * math.sin(t / 120.0)))
        write_real_to_cbuf(self.db_buffers[1000], 0,  max(0.0, 350.0 + 30.0 * math.sin(t / 150.0)))
        write_real_to_cbuf(self.db_buffers[1000], 8,  max(0.0, 150.0 + 15.0 * math.sin(t / 95.0)))
        write_real_to_cbuf(self.db_buffers[1000], 16, max(0.0, 200.0 + 20.0 * math.sin(t / 80.0)))

        # Simulación del movimiento de la tapa horizontal (TH) y finales de carrera
        self._update_th_motion_from_actuators()

    def run(self):
        self.create()
        self.init_values()
        self.start()
        try:
            while self._running:
                evt = self.server.pick_event()
                if evt:
                    text = self.server.event_text(evt)
                    logger.info(
                        "EVENTO S7 | %s | Sender=%s Code=0x%X Ret=0x%X "
                        "P1=%d P2=%d P3=%d P4=%d",
                        text,
                        evt.EvtSender,
                        evt.EvtCode,
                        evt.EvtRetCode,
                        evt.EvtParam1,
                        evt.EvtParam2,
                        evt.EvtParam3,
                        evt.EvtParam4,
                    )
                else:
                    self.simulate()
                    time.sleep(0.05)
        except KeyboardInterrupt:
            logger.info("Interrupción recibida, cerrando.")
        except SystemExit:
            pass
        except Exception as e:
            logger.exception("Excepción en el bucle principal: %s", e)
        finally:
            self.stop()


# =========================
# Señales
# =========================

def install_signal_handlers(stop_func):
    def handler(signum, frame):
        logger.info("Señal %s recibida. Parando servidor...", signum)
        stop_func()
        sys.exit(0)

    signal.signal(signal.SIGINT, handler)
    signal.signal(signal.SIGTERM, handler)


# =========================
# MAIN
# =========================

def main():
    logger.info("Iniciando PLC dummy en puerto TCP %s ...", TCP_PORT)
    s = DummyS7Server(tcp_port=TCP_PORT)
    install_signal_handlers(s.stop)
    s.run()


if __name__ == "__main__":
    main()