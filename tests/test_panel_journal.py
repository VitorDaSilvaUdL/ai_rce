"""Campaign diary and action log: CSV files with author and time."""
from datetime import datetime

from panel import journal


def test_diary_appends_with_header_and_lists_newest_last(tmp_path):
    d = journal.Diary(tmp_path / "diario.csv")
    now = datetime(2026, 10, 21, 22, 0)
    row = d.add(author="AC", kind="ensayo_inicio", text="Tipo 2, T9 inicial 14 °C", now=now, host="LAB-PC")
    assert row["ensayo_id"] == "E20261021-2200" and row["timestamp"] == "2026-10-21 22:00:00"
    d.add(author="RV", kind="incidencia", text="Viento fuerte", now=datetime(2026, 10, 21, 23, 0), host="LAB-PC")
    lines = (tmp_path / "diario.csv").read_text(encoding="utf-8").splitlines()
    assert lines[0] == ",".join(journal.DIARY_HEADER) and len(lines) == 3
    last = d.last(5)
    assert [r["autor"] for r in last] == ["AC", "RV"] and last[-1]["texto"] == "Viento fuerte"
    assert d.last(1)[0]["autor"] == "RV"


def test_diary_keeps_the_open_trial_id(tmp_path):
    d = journal.Diary(tmp_path / "diario.csv")
    d.add(author="AC", kind="ensayo_inicio", text="t2", now=datetime(2026, 10, 21, 22, 0))
    r = d.add(author="AC", kind="incidencia", text="x", now=datetime(2026, 10, 21, 23, 0))
    assert r["ensayo_id"] == "E20261021-2200"
    d.add(author="AC", kind="ensayo_fin", text="", now=datetime(2026, 10, 22, 7, 0))
    r = d.add(author="AC", kind="incidencia", text="y", now=datetime(2026, 10, 22, 8, 0))
    assert r["ensayo_id"] == ""
    assert d.open_trial() is None


def test_actions_log(tmp_path):
    a = journal.Actions(tmp_path / "acciones.csv")
    a.add(author="AC", action="dryrun:cold", mode="shadow", result="ok", duration_s=0.2, now=datetime(2026, 10, 21, 10, 0))
    r = a.last(1)[0]
    assert (r["accion"], r["modo"], r["resultado"], r["duracion_s"]) == ("dryrun:cold", "shadow", "ok", "0.2")


def test_text_with_commas_and_newlines_round_trips(tmp_path):
    d = journal.Diary(tmp_path / "diario.csv")
    d.add(author="AC", kind="incidencia", text='Bomba "B2", ruido\nsegunda línea', now=datetime(2026, 10, 21, 10, 0))
    assert d.last(1)[0]["texto"] == 'Bomba "B2", ruido\nsegunda línea'
