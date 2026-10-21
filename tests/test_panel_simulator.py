"""Quick simulator: given the hour, the current state, the alarm and the AI answer, what would the RCE do."""
from datetime import datetime

import pytest

from panel import simulator

SCHEDULE = {"heat_start": "08:00:00", "heat_end": "14:00:00", "parada_end": "16:00:00"}
TUE = datetime(2026, 10, 20, 9, 30)


@pytest.mark.parametrize("when, expected, slot", [
    (datetime(2026, 10, 20, 9, 30), "HeatMode1", "calor"),
    (datetime(2026, 10, 20, 15, 0), "Parada", "parada"),
    (datetime(2026, 10, 20, 22, 0), "ColdMode1", "frío"),
    (datetime(2026, 10, 24, 22, 0), "Parada", "fin de semana"),
])
def test_yes_from_parada_follows_the_schedule(when, expected, slot):
    r = simulator.quick(now=when, state="Parada", answer="si", alarm=False, schedule=SCHEDULE)
    assert r["next_state"] == expected and r["slot"] == slot
    assert any("horario" in x.lower() or "fin de semana" in x.lower() for x in r["reasons"])


def test_yes_toggles_between_cold_and_heat():
    assert simulator.quick(TUE, "ColdMode1", "si", False, SCHEDULE)["next_state"] == "HeatMode1"
    assert simulator.quick(TUE, "HeatMode1", "si", False, SCHEDULE)["next_state"] == "ColdMode1"


def test_no_keeps_the_state_and_parada_stops():
    r = simulator.quick(TUE, "HeatMode1", "no", False, SCHEDULE)
    assert r["next_state"] == "HeatMode1" and r["changed"] is False and r["cover"] == "closed"
    r = simulator.quick(TUE, "ColdMode1", "parada", False, SCHEDULE)
    assert r["next_state"] == "Parada" and r["cover"] == "closed" and r["plc_mode"] == "parada"


def test_cold_opens_the_cover_unless_there_is_an_alarm():
    assert simulator.quick(TUE, "ColdMode1", "no", False, SCHEDULE)["cover"] == "open"
    r = simulator.quick(TUE, "ColdMode1", "no", True, SCHEDULE)
    assert r["cover"] == "closed" and any("alarma" in x.lower() for x in r["reasons"])


def test_result_has_what_the_page_shows():
    r = simulator.quick(TUE, "Parada", "si", False, SCHEDULE)
    assert {"now", "state", "answer", "next_state", "changed", "plc_mode", "cover", "pumps", "reasons", "slot", "schedule"} <= set(r)
    assert r["pumps"] == {"b1": "auto", "b2": "off", "ev1": "off", "ev2": "on"}
