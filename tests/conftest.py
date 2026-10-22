"""Test isolation. The tests never write in the real files of the installation: state/ (status.json, control.json,
panel_config.json...), dades.csv, sc.log, sc_debug.log, heartbeat_sc.txt and data.txt. On the laboratory PC these
belong to the running control and panel.

- Before any test module imports sc.*, SC_STATE_DIR and SC_LOG_DIR point to a temporary folder, so the defaults
  (state_io.STATE_DIR, the log files of sc.logger) never reach the repository.
- An autouse guard compares those files before and after every test and fails the test that changed them.
  Run the tests with the panel and the control stopped (deploy/update_lab.ps1 does so)."""
import os
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
_TMP = Path(tempfile.mkdtemp(prefix="ai_rce_tests_"))
os.environ["SC_STATE_DIR"] = str(_TMP / "state")
os.environ["SC_LOG_DIR"] = str(_TMP / "logs")

PROTECTED = [ROOT / "state", ROOT / "dades.csv", ROOT / "sc.log", ROOT / "sc_debug.log", ROOT / "heartbeat_sc.txt",
             ROOT / "data.txt"]


def _snapshot():
    snap = {}
    for p in PROTECTED:
        if p.is_dir():
            for f in p.rglob("*"):
                if f.is_file():
                    st = f.stat()
                    snap[f] = (st.st_size, st.st_mtime_ns)
        elif p.is_file():
            st = p.stat()
            snap[p] = (st.st_size, st.st_mtime_ns)
    return snap


@pytest.fixture(autouse=True)
def _real_files_untouched():
    before = _snapshot()
    yield
    after = _snapshot()
    changed = sorted(str(p.relative_to(ROOT)) for p in set(before) | set(after) if before.get(p) != after.get(p))
    if changed:
        pytest.fail("El test ha escrito en ficheros reales de la instalación: " + ", ".join(changed)
                    + " (¿estaba el panel o el control en marcha mientras se pasaban los tests?)")
