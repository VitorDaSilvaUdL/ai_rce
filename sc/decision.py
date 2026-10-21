"""The real-time decision of the control loop as a pure function with reasons.

decide() is step 5 of get_decision() (sc/main.py) extracted without changing its behaviour, so that the panel can
explain every decision and simulate one from chosen inputs. One quirk of the original is kept on purpose and
reported in the reasons instead of fixed here: the production of the active frame is looked up with an ISO key
('2026-10-21T22:00:00') while the forecast uses '2026-10-21 22:00' keys, so the frame production is usually 0 and the
COP 0 (frame_prod_missing). Changing it is a decision of the project.

COP units (corrected 2026-10-21, decision of V. da Silva): the original computed the pump energy of a cycle as
P_bomba_watts * time_step_hours = 2.5 Wh and divided the production in J by it (COP in J/Wh, 3600 times too high).
With pump_energy_units="J" (default) the pump energy is P * t * 3600 = 9000 J and the COP is dimensionless;
pump_energy_units="Wh" reproduces the original (sc config: pump.cop_pump_energy_units).
"""
import random
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta

LABELS = {0: "no", 1: "si", 2: "parada"}


def kwh(j):
    return f"{float(j) / 3.6e6:.2f} kWh"


@dataclass
class Decision:
    now: datetime
    action: int                      # 0 = no (bomba OFF), 1 = si (bomba ON), 2 = parada
    label: str
    mode: str                        # mode for the log/row: that of the active frame or the previous one
    in_time_frame: object            # True/False, or 0 when production is insufficient (as the original row)
    frame_start: datetime | None
    frame_mode: str | None
    frame_prod_j: float
    frame_key: str
    pump_energy_wh: float
    cop: float
    demand_before: float
    demand_after: float
    plan_reset: bool
    pump_energy_units: str = "J"
    codes: list = field(default_factory=list)
    reasons: list = field(default_factory=list)

    def as_dict(self):
        d = asdict(self)
        d["now"] = self.now.isoformat(sep=" ", timespec="seconds")
        d["frame_start"] = self.frame_start.isoformat(sep=" ", timespec="seconds") if self.frame_start else None
        return d


def decide(now, temp_mode, prod_data, current_dem_target, total_predicted_production, *, action_prev=0,
           mode_prev="hot", p_bomba_watts=10, time_step_hours=0.25, min_time_step=15, cop_min=1.0,
           rng=random.uniform, pump_energy_units="J"):
    """One real-time decision. temp_mode: {frame_start datetime: 1 hot | 0 cold}; prod_data: {"hot": {key: J},
    "cold": {...}}. Returns a Decision; the caller applies demand_after / plan_reset to its state."""
    codes, reasons = [], []
    mode = mode_prev
    frame_start = frame_mode = None
    frame_prod = 0.0
    frame_key = ""
    pump_wh = p_bomba_watts * time_step_hours                 # energy of the pump in one cycle, Wh
    pump_energy = pump_wh * 3600 if pump_energy_units == "J" else pump_wh   # same units as the production (J)
    cop = 0.0
    demand = float(current_dem_target)

    insufficient = total_predicted_production == -1 and demand > 0
    if insufficient:
        action = 2
        in_frame = 0
        codes.append("insufficient_production")
        reasons.append(f"La producción prevista no cubre la demanda planificada ({kwh(demand)}): bomba parada")
    else:
        in_frame = False
        for start, kind in temp_mode.items():
            if start <= now < start + timedelta(minutes=min_time_step):
                in_frame = True
                frame_start = start
                frame_mode = "hot" if kind else "cold"
                frame_key = start.isoformat(timespec="seconds")
                codes.append(f"in_frame:{frame_mode}")
                reasons.append(f"Franja planificada de {'calor' if frame_mode == 'hot' else 'frío'} activa "
                               f"({start:%H:%M}–{start + timedelta(minutes=min_time_step):%H:%M})")
                series = prod_data.get(frame_mode, {})
                if frame_key in series:
                    frame_prod = float(series[frame_key])
                    codes.append("frame_prod")
                    reasons.append(f"Producción prevista en la franja: {kwh(frame_prod)}")
                else:
                    codes.append("frame_prod_missing")
                    reasons.append("Sin dato de producción para la franja: el control la busca con la clave "
                                   f"«{frame_key}» y la previsión usa «{start:%Y-%m-%d %H:%M}», así que cuenta 0")
                cop = frame_prod / pump_energy if pump_energy > 0 else 0.0
                codes.append(f"cop:{cop:.2f}")
                if pump_energy_units == "J":
                    reasons.append(f"COP = producción prevista ({kwh(frame_prod)}) / consumo de la bomba en la franja "
                                   f"({pump_wh:g} Wh = {pump_energy:g} J) = {cop:.2f}")
                else:
                    reasons.append(f"COP (unidades antiguas) = producción (J) / consumo de la bomba ({pump_wh:g} Wh) = {cop:.2f}")
                if cop >= cop_min and demand > 0:
                    action = 1
                    codes.append("cop_ok")
                    reasons.append(f"COP ≥ {cop_min:g} y queda demanda ({kwh(demand)}): bomba en marcha → «sí»")
                else:
                    action = 0
                    codes.append("cop_low" if demand > 0 else "no_demand_in_frame")
                    reasons.append(f"COP < {cop_min:g}: no compensa arrancar la bomba → «no»" if demand > 0
                                   else "No queda demanda por cubrir → «no»")
                mode = frame_mode
                break
        if not in_frame:
            action = 2
            codes.append("out_of_frames")
            reasons.append("La hora actual no está en ninguna franja planificada: bomba parada → «parada»")

    plan_reset = False
    demand_after = demand
    if not insufficient:                      # the original adjusts the demand only in this branch
        if demand > 0:
            produced = p_bomba_watts * time_step_hours * rng(0.8, 1.2) if action == 1 else 0
            demand_after = max(0, demand - produced)
            codes.append("demand_adjusted")
            reasons.append(f"Demanda restante: de {kwh(demand)} a {kwh(demand_after)}")
            if demand_after <= 0:
                plan_reset = True
                demand_after = 0.0
                action = 0
                codes.append("plan_done")
                reasons.append("Demanda cubierta: plan reiniciado y bomba parada → «no»")
        else:
            action = 0
            codes.append("no_pending_demand")
            reasons.append("No hay demanda objetivo pendiente: bomba parada → «no»")

    safe = action if action in (0, 1, 2) else 2
    return Decision(now=now, action=safe, label=LABELS[safe], mode=mode, in_time_frame=in_frame,
                    frame_start=frame_start, frame_mode=frame_mode, frame_prod_j=frame_prod, frame_key=frame_key,
                    pump_energy_wh=pump_wh, cop=cop, demand_before=demand, demand_after=demand_after,
                    pump_energy_units=pump_energy_units,
                    plan_reset=plan_reset, codes=codes, reasons=reasons)
