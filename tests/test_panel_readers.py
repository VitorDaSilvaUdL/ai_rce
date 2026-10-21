"""Readers of the control's own files: status.json, heartbeat, dades.csv, sc.log, services."""
from datetime import datetime, timedelta

from panel import readers
from sc import cycle_log

NOW = datetime(2026, 10, 21, 11, 0, 0)


def test_heartbeat_age(tmp_path):
    p = tmp_path / "heartbeat_sc.txt"
    assert readers.heartbeat_age(p, NOW) is None
    p.write_text("2026-10-21T10:50:00", encoding="utf-8")
    assert readers.heartbeat_age(p, NOW) == 600
    p.write_text("garbage", encoding="utf-8")
    assert readers.heartbeat_age(p, NOW) is None


def test_status_age(tmp_path):
    assert readers.read_status(tmp_path, NOW) is None
    (tmp_path / "status.json").write_text('{"written_at": "2026-10-21 10:45:00", "execution": "shadow"}', encoding="utf-8")
    st = readers.read_status(tmp_path, NOW)
    assert st["execution"] == "shadow" and st["age_s"] == 900


def write_dades(path, rows):
    cycle_log.write_cycle_row(path, None, cycle_log.state_columns("shadow", "Parada", "Parada"), now=rows[0][0])
    path.unlink()
    for ts, exe, before, state in rows:
        cycle_log.write_cycle_row(path, [ts, True, 0.0, "si", "cold", 1, 2, 3, 4],
                                  cycle_log.state_columns(exe, before, state))


def test_read_decisions_parses_new_format_and_filters_by_time(tmp_path):
    p = tmp_path / "dades.csv"
    write_dades(p, [(datetime(2026, 10, 21, 9, 0), "shadow", "Parada", "ColdMode1"),
                    (datetime(2026, 10, 21, 10, 0), "shadow", "ColdMode1", "ColdMode1"),
                    (datetime(2026, 10, 21, 10, 45), "real", "ColdMode1", "HeatMode1")])
    rows = readers.read_decisions(p, since=datetime(2026, 10, 21, 9, 30))
    assert [r["ts"] for r in rows] == [datetime(2026, 10, 21, 10, 0), datetime(2026, 10, 21, 10, 45)]
    assert rows[-1]["execution"] == "real" and rows[-1]["cover_open"] == 0 and rows[-1]["plc_mode_code"] == 1
    assert rows[0]["cover"] == "open" and rows[0]["hot_prod"] == 1.0


def test_read_decisions_old_format(tmp_path):
    p = tmp_path / "dades.csv"
    p.write_text(",".join(cycle_log.DECISION_HEADER) + "\n2025-12-10 10:36:02,True,43010782.75,no,cold,1,2,3,4\n",
                 encoding="utf-8")
    rows = readers.read_decisions(p, since=datetime(2025, 1, 1))
    assert rows[0]["action"] == "no" and rows[0]["execution"] == "" and rows[0]["cover"] == ""


def test_read_decisions_missing_or_empty(tmp_path):
    assert readers.read_decisions(tmp_path / "none.csv", since=NOW) == []
    (tmp_path / "e.csv").write_text("", encoding="utf-8")
    assert readers.read_decisions(tmp_path / "e.csv", since=NOW) == []


def test_tail_log_and_plain_language_events(tmp_path):
    p = tmp_path / "sc.log"
    p.write_text(
        "2026-10-21 10:45:00,001 [INFO]: 2026-10-21 10:45:00: Nou cicle de control.\n"
        "2026-10-21 10:45:01,002 [INFO]: SOMBRA: se aplicaría HeatMode1 (antes ColdMode1). No se escribe en el PLC.\n"
        "2026-10-21 10:45:02,003 [WARNING]: 2026-10-21 10:45:02: COP < 1.0 o no hi ha demanda. Bomba OFF (action=0).\n"
        "2026-10-21 10:45:03,004 [ERROR]: 2026-10-21 10:45:03: No s'han pogut obtenir prediccions (inicio): req=None.\n"
        "linea sin formato\n", encoding="utf-8")
    ev = readers.read_events(p, max_lines=10)
    assert [e["level"] for e in ev] == ["INFO", "INFO", "WARNING", "ERROR"]
    assert ev[0]["ts"] == datetime(2026, 10, 21, 10, 45, 0) and ev[0]["text"] == "Nuevo ciclo de control"
    assert ev[1]["text"] == "Sombra: habría cambiado a modo calor (antes modo frío); no se escribe en el PLC"
    assert "Bomba parada" in ev[2]["text"] and "rendimiento" in ev[2]["text"]
    assert ev[3]["text"].startswith("No se han podido obtener las previsiones")


def test_service_state_parses_sc_query_output():
    assert readers.service_state("filebeat", runner=lambda a: (0, "SERVICE_NAME: filebeat\n        STATE              : 4  RUNNING\n")) == "running"
    assert readers.service_state("filebeat", runner=lambda a: (0, "        STATE              : 1  STOPPED\n")) == "stopped"
    assert readers.service_state("filebeat", runner=lambda a: (0, "NOMBRE_SERVICIO: filebeat\n        ESTADO             : 4  RUNNING \n")) == "running"
    assert readers.service_state("filebeat", runner=lambda a: (1060, "The specified service does not exist")) == "missing"
    assert readers.service_state("filebeat", runner=lambda a: (None, "")) == "unknown"


def test_disk_free_gb(tmp_path):
    assert readers.disk_free_gb(tmp_path) > 0
