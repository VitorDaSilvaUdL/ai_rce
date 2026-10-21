"""Dry-run commands: the exact PLC writes a command would do, simulated without a PLC."""
import pytest
import snap7

from panel import dryrun


def texts(result):
    return [s["text"] for s in result["steps"]]


def test_dry_run_never_touches_snap7(monkeypatch):
    def forbidden(*a, **k):
        raise AssertionError("dry run created a snap7 client")
    monkeypatch.setattr(snap7.client, "Client", forbidden)
    for cmd in dryrun.COMMANDS:
        dryrun.run(cmd, state="Parada")


def test_cold_mode_from_parada_opens_the_cover():
    r = dryrun.run("cold", state="Parada")
    assert r["state_before"] == "Parada" and r["state_after"] == "ColdMode1"
    assert (r["cover_before"], r["cover_after"]) == ("closed", "open")
    assert r["aborted"] is False
    t = texts(r)
    assert any("B2" in s and "marcha" in s.lower() and "ON" in s for s in t)
    assert any("Esperar 45 s" in s for s in t)
    assert any("Tapa horizontal" in s and "esté abierta" in s for s in t)
    assert r["writes"] >= 10


def test_heat_mode_from_cold_closes_the_cover():
    r = dryrun.run("heat", state="ColdMode1")
    assert r["state_after"] == "HeatMode1" and r["cover_after"] == "closed" and r["aborted"] is False


def test_parada_from_heat_with_cover_closed_aborts_the_close_sequence_but_sets_parada():
    r = dryrun.run("parada", state="HeatMode1")
    assert r["state_after"] == "Parada" and r["cover_after"] == "closed"
    assert r["aborted"] is True and any("abort" in s.lower() for s in texts(r))


def test_automatic_hands_everything_to_the_plc():
    r = dryrun.run("automatic", state="ColdMode1")
    assert r["state_after"] == "AutomaticMode" and r["writes"] == 14


def test_open_cover_is_blocked_by_an_alarm():
    r = dryrun.run("open_cover", state="Parada", alarm=True)
    assert r["cover_after"] == "closed" and r["aborted"] is True
    assert any("bloquead" in s.lower() for s in texts(r))


def test_open_and_close_cover_and_test_sequence():
    assert dryrun.run("open_cover", state="Parada")["cover_after"] == "open"
    assert dryrun.run("close_cover", state="ColdMode1")["cover_after"] == "closed"
    r = dryrun.run("test_cover", state="Parada")
    assert r["cover_after"] == "closed" and r["aborted"] is False
    t = texts(r)
    assert t.index(next(s for s in t if "esté abierta" in s)) < t.index(next(s for s in t if "esté cerrada" in s))


def test_unknown_command():
    with pytest.raises(ValueError):
        dryrun.run("fly", state="Parada")


def test_steps_carry_the_raw_write_for_traceability():
    r = dryrun.run("cold", state="Parada")
    w = next(s for s in r["steps"] if s["kind"] == "write")
    assert {"db", "byte", "bit", "value", "text"} <= set(w)
