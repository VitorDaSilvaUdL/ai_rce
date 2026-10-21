"""Reader of the WinCC CSV archives for the panel: tail of the files, parsed readings, latest values, stale probes."""
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from panel import wincc

ROOT = Path(__file__).resolve().parents[1]

HEADER = '"VarName";"TimeString";"VarValue";"Validity";"Time_ms"\r\n'
LINES = (
    '"TempT9_RCEa";"21/10/2026 10:05:00";12,5;1;46315425000,0\r\n'
    '"TempT6_RCEa";"21/10/2026 10:05:00";41,2;1;46315425000,0\r\n'
    '"$RT_OFF$";"21/10/2026 10:06:00";0;2;46315425694,4\r\n'
    '"TempT9_RCEa";"21/10/2026 10:10:00";12,4;1;46315428472,2\r\n'
    '"CauQ1_RCEa";"21/10/2026 10:10:00";0;1;46315428472,2\r\n'
    '"E_FIR, neto, [W/m2]_RCEb";"21/10/2026 10:10:00";-65,3;1;46315428472,2\r\n'
    '"TempT9_RCEa";"21/10/2026 10:15:00";12,4;0;46315431944,4\r\n'
    '"PotenciaActivaTotal_a";"21/10/2026 10:15:00";0;1;46315431944,4\r\n'
)


@pytest.fixture
def lecturas(tmp_path):
    p = tmp_path / "Lecturas0.csv"
    p.write_bytes((HEADER + LINES).encode("latin-1"))
    return p


# ----------------------------------------------------------------------------------------------- parsing
def test_parse_var_splits_tag_and_unit():
    assert wincc.parse_var("TempT9_RCEa") == ("TempT9", "RCE-a")
    assert wincc.parse_var("E_FIR, neto, [W/m2]_RCEb") == ("E_FIR, neto, [W/m2]", "RCE-b")
    assert wincc.parse_var("PotenciaActivaTotal_a") == ("PotenciaActivaTotal_a", "RCE-a")
    assert wincc.parse_var("EdissCalReal_8") == ("EdissCalReal_8", "RCE")


def test_parse_line_handles_decimal_comma_validity_and_markers():
    r = wincc.parse_line('"TempT9_RCEa";"21/10/2026 10:05:00";12,5;1;46315425000,0')
    assert (r.ts, r.tag, r.system, r.variable, r.value, r.valid) == \
        (datetime(2026, 10, 21, 10, 5), "TempT9", "RCE-a", "t_cold_tank_c", 12.5, True)
    assert wincc.parse_line('"TempT9_RCEa";"21/10/2026 10:15:00";12,4;0;1').valid is False
    assert wincc.parse_line('"$RT_OFF$";"21/10/2026 10:06:00";0;2;1') is None
    assert wincc.parse_line('"VarName";"TimeString";"VarValue";"Validity";"Time_ms"') is None
    assert wincc.parse_line("") is None
    assert wincc.parse_line('"TempT9_RCEa";"21/10/2026 10:05";abc;1;1').value is None


def test_unknown_tags_keep_their_name():
    r = wincc.parse_line('"PotenciaActivaTotal_a";"21/10/2026 10:15:00";0;1;1')
    assert r.variable == "PotenciaActivaTotal_a" and r.system == "RCE-a"


# ----------------------------------------------------------------------------------------------- tail
def test_read_recent_returns_only_lines_since(lecturas):
    rs = wincc.read_recent(lecturas, since=datetime(2026, 10, 21, 10, 10))
    assert [(r.tag, r.value) for r in rs] == [("TempT9", 12.4), ("CauQ1", 0.0), ("E_FIR, neto, [W/m2]", -65.3),
                                              ("TempT9", 12.4), ("PotenciaActivaTotal_a", 0.0)]


def test_tail_drops_the_partial_first_line(lecturas):
    whole = lecturas.read_bytes()
    cut = len(whole) - 120                      # start in the middle of a line
    rs = wincc.read_recent(lecturas, since=datetime(2026, 1, 1), max_bytes=120)
    assert all(r.ts >= datetime(2026, 10, 21, 10, 5) for r in rs)
    assert len(rs) >= 1 and len(rs) < len(LINES.splitlines())
    assert whole[cut:].split(b"\r\n")[0] not in b"".join(r.raw.encode("latin-1") for r in rs)


def test_read_recent_missing_file_is_empty(tmp_path):
    assert wincc.read_recent(tmp_path / "nope.csv", since=datetime(2026, 1, 1)) == []


# ----------------------------------------------------------------------------------------------- latest and stale
def test_latest_values_per_series_and_stale_detection(lecturas):
    now = datetime(2026, 10, 21, 11, 20)
    rs = wincc.read_recent(lecturas, since=now - timedelta(hours=24))
    latest = wincc.latest_values(rs)
    t9 = latest[("t_cold_tank_c", "RCE-a")]
    assert (t9.value, t9.valid, t9.ts) == (12.4, False, datetime(2026, 10, 21, 10, 15))
    assert latest[("t_hot_tank_c", "RCE-a")].value == 41.2
    status = wincc.series_status(rs, now=now, stale_after=timedelta(minutes=30), frozen_window=timedelta(hours=1))
    assert status[("t_cold_tank_c", "RCE-a")]["stale"] is True          # last reading 65 min ago
    assert status[("t_cold_tank_c", "RCE-a")]["last_invalid"] is True
    assert status[("t_hot_tank_c", "RCE-a")]["frozen"] is False         # one sample: cannot tell
    fresh = wincc.series_status(rs, now=datetime(2026, 10, 21, 10, 20), stale_after=timedelta(minutes=30),
                                frozen_window=timedelta(hours=1))
    assert fresh[("t_cold_tank_c", "RCE-a")]["stale"] is False


def test_frozen_series_is_flagged(tmp_path):
    p = tmp_path / "Lecturas0.csv"
    body = "".join(f'"TempT6_RCEa";"21/10/2026 {h:02d}:{m:02d}:00";41,2;1;1\r\n' for h in (9, 10) for m in range(0, 60, 5))
    p.write_bytes((HEADER + body).encode("latin-1"))
    now = datetime(2026, 10, 21, 11, 0)
    st = wincc.series_status(wincc.read_recent(p, since=now - timedelta(hours=3)), now=now,
                             stale_after=timedelta(minutes=30), frozen_window=timedelta(hours=1))
    assert st[("t_hot_tank_c", "RCE-a")]["frozen"] is True


# ----------------------------------------------------------------------------------------------- labels, folder
def test_every_canonical_variable_has_a_label_and_unit():
    for var in wincc.CANONICAL.values():
        assert var in wincc.LABELS, var
        assert len(wincc.LABELS[var]) == 2


def test_read_folder_covers_all_archives(tmp_path, lecturas):
    (tmp_path / "Solarimeter0.csv").write_bytes((HEADER + '"IO_SENSOR1_DATA_RCEa";"21/10/2026 10:10:00";350;1;1\r\n').encode("latin-1"))
    rs = wincc.read_folder(tmp_path, since=datetime(2026, 1, 1))
    assert {r.source for r in rs} == {"Lecturas0.csv", "Solarimeter0.csv"}
    assert any(r.variable == "solar_global_w_m2" for r in rs)


@pytest.mark.skipif(not (ROOT / "CSVs" / "Lecturas0.csv").exists(), reason="sample archive not present")
def test_real_archive_tail_parses_without_errors():
    rs = wincc.read_recent(ROOT / "CSVs" / "Lecturas0.csv", since=datetime(2000, 1, 1), max_bytes=200_000)
    assert len(rs) > 1000
    assert all(r.value is not None for r in rs)
    assert ("t_cold_tank_c", "RCE-a") in wincc.latest_values(rs)
