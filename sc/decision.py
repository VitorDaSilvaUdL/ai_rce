"""The real-time decision of the control loop as a pure function with reasons.

decide() is step 5 of get_decision() (sc/main.py) extracted without changing its behaviour, so that the panel can
explain every decision and simulate one from chosen inputs. Three faults of the original control (campaign 2025) are corrected here, each with a legacy option so the old
behaviour can be reproduced (decision of V. da Silva, 2026-10-21; see README «Cambios respecto al control original»):

- frame_key_format: the production of the active frame was looked up with an ISO key ('2026-10-21T22:00:00') while
  the forecast uses '2026-10-21 22:00' keys, so the frame production was always 0, the COP 0 and the answer never
  'si'. "forecast" (default) uses the forecast's own key; "iso" reproduces the original.
- pump_energy_units: the pump energy of a cycle was P_bomba_watts * time_step_hours = 2.5 Wh and the production in J
  was divided by it (COP in J/Wh, 3600 times too high). "J" (default) uses 9000 J; "Wh" reproduces the original.
- demand_adjust: when the pump ran, 2.5 Wh were subtracted from a demand in J, so the demand was never covered.
  "frame_prod" (default) subtracts the predicted production of the frame (J); "pump_wh" reproduces the original.
The fourth fault ('si' toggled the mode instead of selecting the mode of the frame) is corrected in sc/main.py
(control.next_state_policy).
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
    frame_key_format: str = "forecast"
    demand_adjust: str = "frame_prod"
    codes: list = field(default_factory=list)
    reasons: list = field(default_factory=list)

    def as_dict(self):
        d = asdict(self)
        d["now"] = self.now.isoformat(sep=" ", timespec="seconds")
        d["frame_start"] = self.frame_start.isoformat(sep=" ", timespec="seconds") if self.frame_start else None
        return d


def decide(now, temp_mode, prod_data, current_dem_target, total_predicted_production, *, action_prev=0,
           mode_prev="hot", p_bomba_watts=10, time_step_hours=0.25, min_time_step=15, cop_min=1.0,
           rng=random.uniform, pump_energy_units="J", frame_key_format="forecast", demand_adjust="frame_prod"):
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
                iso_key = start.isoformat(timespec="seconds")
                forecast_key = start.strftime("%Y-%m-%d %H:%M")
                candidates = [forecast_key, iso_key] if frame_key_format == "forecast" else [iso_key]
                frame_key = candidates[0]
                codes.append(f"in_frame:{frame_mode}")
                reasons.append(f"Franja planificada de {'calor' if frame_mode == 'hot' else 'frío'} activa "
                               f"({start:%H:%M}–{start + timedelta(minutes=min_time_step):%H:%M})")
                series = prod_data.get(frame_mode, {})
                found = next((k for k in candidates if k in series), None)
                if found is not None:
                    frame_key = found
                    frame_prod = float(series[found])
                    codes.append("frame_prod")
                    reasons.append(f"Producción prevista en la franja: {kwh(frame_prod)}")
                elif frame_key_format == "forecast":
                    codes.append("frame_prod_missing")
                    reasons.append(f"Sin dato de producción para la franja «{forecast_key}» en la previsión: cuenta 0")
                else:
                    codes.append("frame_prod_missing")
                    reasons.append("Sin dato de producción para la franja (formato antiguo): el control la busca con la "
                                   f"clave «{iso_key}» y la previsión usa «{forecast_key}», así que cuenta 0")
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
            if action != 1:
                produced = 0
            elif demand_adjust == "frame_prod":
                produced = frame_prod * rng(0.8, 1.2)                    # J, the predicted production of the frame
            else:
                produced = p_bomba_watts * time_step_hours * rng(0.8, 1.2)   # original: 2.5 Wh subtracted from J
            demand_after = max(0, demand - produced)
            codes.append("demand_adjusted")
            how = (" (se descuenta la producción prevista de la franja)" if action == 1 and demand_adjust == "frame_prod"
                   else " (formato antiguo: se descuentan 2,5 Wh)" if action == 1 else "")
            reasons.append(f"Demanda restante: de {kwh(demand)} a {kwh(demand_after)}{how}")
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
                    pump_energy_units=pump_energy_units, frame_key_format=frame_key_format, demand_adjust=demand_adjust,
                    plan_reset=plan_reset, codes=codes, reasons=reasons)


STATE_OF_FRAME_MODE = {"cold": "ColdMode1", "hot": "HeatMode1"}


def next_state(decision, estado_actual, plc, now=None, policy="frame_mode"):
    """PLC state after a decision. policy="frame_mode" (default): 'si' selects the mode of the active frame
    (cold -> ColdMode1, hot -> HeatMode1) instead of toggling; 'no' keeps the state; 'parada' stops; the automatic
    state of the PLC is never left by the control. policy="toggle": the original state machine
    (PLCController.decide_next_state_from_nn: 'si' toggles cold <-> heat, from parada the weekly schedule decides)."""
    label = decision.label if decision is not None else "parada"
    if policy == "frame_mode" and decision is not None and decision.action == 1 and decision.frame_mode \
            and estado_actual != "AutomaticMode":
        return STATE_OF_FRAME_MODE[decision.frame_mode]
    return plc.decide_next_state_from_nn(label, estado_actual, now=now)
