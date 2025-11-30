"""
Versión rápida adaptada para usar PLCController.
Mínimos cambios sobre tu test_estados2.py original.
"""

import tkinter as tk
from tkinter import ttk, messagebox
import threading

# from plc_controller import PLCController   # ⟵ IMPORTA TU CLASE AQUÍ
from .plc_controller import PLCController

class App(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("Test Estados PLC")
        self.geometry("600x220")
        self.resizable(False, False)

        # En lugar de client
        self.plc: PLCController | None = None
        self.connected = tk.BooleanVar(value=False)

        # ------------ UI Conexión ------------
        frm_conn = ttk.LabelFrame(self, text="Conexión")
        frm_conn.pack(fill="x", padx=10, pady=10)

        ttk.Label(frm_conn, text="IP:").grid(row=0, column=0, padx=5, pady=5, sticky="e")
        self.ent_ip = ttk.Entry(frm_conn, width=16)
        self.ent_ip.insert(0, "172.17.10.110")
        self.ent_ip.grid(row=0, column=1, padx=5, pady=5)

        ttk.Label(frm_conn, text="Rack:").grid(row=0, column=2, padx=5, pady=5, sticky="e")
        self.ent_rack = ttk.Entry(frm_conn, width=4)
        self.ent_rack.insert(0, "0")
        self.ent_rack.grid(row=0, column=3, padx=5, pady=5)

        ttk.Label(frm_conn, text="Slot:").grid(row=0, column=4, padx=5, pady=5, sticky="e")
        self.ent_slot = ttk.Entry(frm_conn, width=4)
        self.ent_slot.insert(0, "1")
        self.ent_slot.grid(row=0, column=5, padx=5, pady=5)

        self.btn_connect = ttk.Button(frm_conn, text="Conectar", command=self.on_connect)
        self.btn_connect.grid(row=0, column=6, padx=8)

        self.lbl_status = ttk.Label(frm_conn, text="Desconectado", foreground="red")
        self.lbl_status.grid(row=0, column=7, padx=8)

        # ------------ UI ESTADOS ------------
        frm_states = ttk.LabelFrame(self, text="Estados")
        frm_states.pack(fill="x", padx=10, pady=10)

        self.btn_auto = ttk.Button(frm_states, text="Automático", width=14, command=self.set_auto)
        self.btn_frio = ttk.Button(frm_states, text="Frío", width=14, command=self.set_frio)
        self.btn_calor = ttk.Button(frm_states, text="Calor", width=14, command=self.set_calor)
        self.btn_parada = ttk.Button(frm_states, text="Parada", width=14, command=self.set_parada)
        
        #NUEVO BOTÓN PARA CAPTURAR ESTADO
        self.btn_capturar = ttk.Button(
            frm_states,
            text="Capturar estado",
            width=18,
            command=self.capture_state,   
        )
        self.btn_capturar.grid(row=1, column=0, columnspan=4, padx=8, pady=6)

        self.btn_auto.grid(row=0, column=0, padx=8, pady=6)
        self.btn_frio.grid(row=0, column=1, padx=8, pady=6)
        self.btn_calor.grid(row=0, column=2, padx=8, pady=6)
        self.btn_parada.grid(row=0, column=3, padx=8, pady=6)

        self._update_state_buttons()
        self.protocol("WM_DELETE_WINDOW", self.on_close)

    # ----------------------------------------------------------------------
    # Conexión
    # ----------------------------------------------------------------------
    def on_connect(self) -> None:
        if self.connected.get():
            # desconectar
            try:
                if self.plc is not None:
                    self.plc.disconnect()
            except Exception:
                pass
            self.plc = None
            self.connected.set(False)
            self.lbl_status.configure(text="Desconectado", foreground="red")
            self.btn_connect.configure(text="Conectar")
            self._update_state_buttons()
            return

        # conectar
        ip = self.ent_ip.get().strip()
        rack = int(self.ent_rack.get())
        slot = int(self.ent_slot.get())

        try:
            plc = PLCController(ip=ip, rack=rack, slot=slot)
            plc.connect()
            if not plc.is_connected():
                raise RuntimeError("No se pudo conectar")

            self.plc = plc
            self.connected.set(True)
            self.lbl_status.configure(text="Conectado", foreground="green")
            self.btn_connect.configure(text="Desconectar")
            self._update_state_buttons()

        except Exception as e:
            messagebox.showerror("Error de conexión", str(e))
            self.plc = None
            self.connected.set(False)
            self.lbl_status.configure(text="Desconectado", foreground="red")
            self.btn_connect.configure(text="Conectar")
            self._update_state_buttons()

    
    def capture_state(self):
        """
        Lee el estado actual del PLC y lo muestra:
          - En consola: cada booleano + DB/byte/bit
          - En una ventana: estado lógico y modo simple (get_current_mode)
        """
        self._require_connection()

        try:
            # 1) Leer diccionario de actuadores
            act = self.plc.read_actuators_state()

            # 2) Reconstruir estado lógico y modo simple
            estado_sistema, _ = self.plc.get_system_state()
            modo_simple = self.plc.get_current_mode()

            # 3) Mapa de variables -> (DB, byte, bit)
            mapping = {
                # B1 [DB500]
                "ActManB1":      (500, 12, 0),
                "ActManMarxaB1": (500, 12, 1),

                # B2 [DB501]
                "ActManB2":      (501, 12, 0),
                "ActManMarxaB2": (501, 12, 1),

                # EV1 [DB300]
                "ActManEV1":      (300, 10, 0),
                "ActManMarxaEV1": (300, 10, 1),
                "ActManStopEV1":  (300, 10, 2),

                # EV2 [DB301]
                "ActManEV2":      (301, 10, 0),
                "ActManMarxaEV2": (301, 10, 1),
                "ActManStopEV2":  (301, 10, 2),

                # Tapa Horizontal [DB503]
                "ActManTH":      (503, 12, 0),
                "ActManMarxaTH": (503, 12, 1),
                #Falta INVERSO TH

                # Tapa Vertical [DB504]
                "ActManTV":      (504, 12, 0),
                "ActManMarxaTV": (504, 12, 1),
                #Falta INVERSO TV
            }

            print("\n================= CAPTURA DE ESTADO PLC =================")
            for nombre, (db, byte, bit) in mapping.items():
                valor = act.get(nombre)
                print(f"{nombre:15s} = {str(valor):5s}   (DB{db}.DBX{byte}.{bit})")
            print("---------------------------------------------------------")
            print(f"SystemCase detectado: {estado_sistema}")
            print(f"get_current_mode():   {modo_simple}")
            print("=========================================================\n")

            # 4) Ventanita informativa
            from tkinter import messagebox
            messagebox.showinfo(
                "Estado actual",
                f"SystemCase: {estado_sistema}\n"
                f"Modo simple: {modo_simple}"
            )

        except Exception as e:
            from tkinter import messagebox
            messagebox.showerror("Error", f"No se pudo capturar el estado: {e}")


    # ----------------------------------------------------------------------
    # Ejecutor no bloqueante
    # ----------------------------------------------------------------------
    def _run_async(self, fn, nombre: str):
        def task():
            try:
                fn()
                self.after(0, lambda: messagebox.showinfo("OK", f"Estado: {nombre} aplicado"))
            except Exception as e:
                self.after(0, lambda: messagebox.showerror("Error", f"No se pudo aplicar {nombre}: {e}"))

        threading.Thread(target=task, daemon=True).start()

    # ----------------------------------------------------------------------
    # ESTADOS
    # ----------------------------------------------------------------------
    def set_auto(self):
        self._require_connection()
        self._run_async(self.plc.set_automatic_mode, "Automático")

    def set_frio(self):
        self._require_connection()
        self._run_async(self.plc.set_cold_mode, "Frío")

    def set_calor(self):
        self._require_connection()
        self._run_async(self.plc.set_heat_mode, "Calor")

    def set_parada(self):
        self._require_connection()
        self._run_async(self.plc.set_parada_mode, "Parada")

    # ----------------------------------------------------------------------
    # Helpers
    # ----------------------------------------------------------------------
    def _require_connection(self):
        if self.plc is None or not self.plc.is_connected():
            raise RuntimeError("Conéctate primero al PLC")

    def _update_state_buttons(self):
        state = tk.NORMAL if self.connected.get() else tk.DISABLED
        for btn in (self.btn_auto, self.btn_frio, self.btn_calor, self.btn_parada, self.btn_capturar):
            btn.configure(state=state)

    def on_close(self):
        try:
            if self.plc is not None:
                self.plc.disconnect()
        except Exception:
            pass
        self.destroy()


if __name__ == "__main__":
    App().mainloop()