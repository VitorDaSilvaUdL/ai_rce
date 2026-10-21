"""Quick simulator («¿Qué haría?», nivel rápido): the state machine of the control with a chosen AI answer.

Given the date and hour, the current RCE state, the rain/wind alarm and the answer of the AI (si / no / parada),
it returns the next state, the mode, the cover position, pumps and valves, and the reasons in plain language. It
uses the real decision code (PLCController.decide_next_state_from_nn through PLCShadow) with the configured
schedule, so it answers exactly what the control would do. The full level (with the prediction model) is phase 2.
"""
from sc.plc_shadow import PLCShadow, DEFAULT_SCHEDULE
from sc.cycle_log import state_outputs, MODE_OF_STATE

STATE_ES = {"ColdMode1": "modo frío", "ColdMode2": "modo frío", "HeatMode1": "modo calor", "HeatMode2": "modo calor",
            "Parada": "parada", "AutomaticMode": "automático", None: "sin estado conocido"}
ANSWERS = {"si": "sí", "no": "no", "parada": "parada"}


def slot_of(now, schedule):
    """Which part of the weekly schedule the moment falls in: calor, parada, frío or fin de semana."""
    if now.strftime("%A") in ("Saturday", "Sunday"):
        return "fin de semana"
    t = now.strftime("%H:%M:%S")
    if schedule["heat_start"] < t < schedule["heat_end"]:
        return "calor"
    if schedule["heat_end"] < t < schedule["parada_end"]:
        return "parada"
    return "frío"


def quick(now, state, answer, alarm=False, schedule=None):
    plc = PLCShadow(initial_state=state, schedule=schedule or DEFAULT_SCHEDULE)
    sched = plc.schedule
    answer = (answer or "").strip().lower()
    next_state = plc.decide_next_state_from_nn(answer, state, now=now)
    slot = slot_of(now, sched)
    outputs = state_outputs(next_state, alarm=alarm)
    reasons = []
    if answer in ("si", "yes"):
        if state in ("ColdMode1", "ColdMode2", "HeatMode1", "HeatMode2"):
            reasons.append(f"La IA dice «sí»: estaba en {STATE_ES[state]} y pasa a {STATE_ES[next_state]}")
        elif state == "AutomaticMode":
            reasons.append("La IA dice «sí», pero el RCE está en automático: sigue en automático")
        else:
            if slot == "fin de semana":
                reasons.append("La IA dice «sí» desde parada, pero es fin de semana: el horario manda parada")
            else:
                reasons.append(f"La IA dice «sí» desde parada: decide el horario ({slot}: calor "
                               f"{sched['heat_start'][:5]}–{sched['heat_end'][:5]}, parada hasta "
                               f"{sched['parada_end'][:5]}, el resto frío)")
    elif answer == "no":
        reasons.append(f"La IA dice «no»: se mantiene en {STATE_ES.get(state, state)}")
    elif answer == "parada":
        reasons.append("La IA pide parada: bombas paradas y tapa cerrada")
    else:
        reasons.append("Respuesta desconocida: por seguridad, parada (igual que con lluvia o viento)")
    if alarm and MODE_OF_STATE.get(next_state) == "cold":
        reasons.append("Alarma de lluvia o viento activa: la tapa se queda cerrada aunque el modo sea frío")
    reasons.append({"open": "Tapa abierta", "closed": "Tapa cerrada", "plc": "Tapa: la mueve el programa del PLC"}
                   .get(outputs["cover"], "Tapa: posición desconocida"))
    return {
        "now": now.isoformat(sep=" ", timespec="minutes"), "state": state, "answer": ANSWERS.get(answer, answer),
        "next_state": next_state, "changed": next_state != state,
        "plc_mode": outputs["plc_mode"], "cover": outputs["cover"],
        "pumps": {"b1": outputs["pump_b1"], "b2": outputs["pump_b2"], "ev1": outputs["ev1"], "ev2": outputs["ev2"]},
        "reasons": reasons, "slot": slot, "schedule": sched,
    }
