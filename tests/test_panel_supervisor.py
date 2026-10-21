"""Supervisor: starts, stops, restarts and watches the API and control subprocesses."""
import sys
import time
from datetime import datetime, timedelta

import pytest

from panel.supervisor import ManagedProcess, Supervisor

PY = sys.executable
SLEEP = [PY, "-c", "import time; time.sleep(60)"]
CRASH = [PY, "-c", "import sys; sys.exit(3)"]


@pytest.fixture
def logs(tmp_path):
    return tmp_path


def test_managed_process_start_stop(logs):
    p = ManagedProcess("sleep", SLEEP, cwd=logs, log_path=logs / "sleep.log")
    assert p.running is False
    assert p.start() is True
    assert p.running is True and p.pid
    assert p.start() is False                       # already running
    p.stop(timeout=5)
    assert p.running is False and p.pid is None


def test_managed_process_records_exit_code(logs):
    p = ManagedProcess("crash", CRASH, cwd=logs, log_path=logs / "crash.log")
    p.start()
    for _ in range(50):
        if not p.running:
            break
        time.sleep(0.1)
    assert p.running is False and p.last_exit_code == 3


def test_supervisor_starts_sc_only_when_api_is_healthy(logs):
    api = ManagedProcess("api", SLEEP, cwd=logs, log_path=logs / "api.log")
    sc = ManagedProcess("sc", SLEEP, cwd=logs, log_path=logs / "sc.log")
    healthy = {"v": False}
    sup = Supervisor(api=api, sc=sc, api_ready=lambda: healthy["v"], restart_delay_s=60)
    t0 = datetime(2026, 10, 21, 11, 0)
    try:
        sup.start(now=t0)
        sup.tick(now=t0)
        assert api.running and not sc.running
        healthy["v"] = True
        sup.tick(now=t0 + timedelta(seconds=5))
        assert sc.running
        st = sup.status(now=t0 + timedelta(seconds=10))
        assert st["wanted"] is True and st["api"]["running"] and st["sc"]["running"] and st["sc"]["restarts"] == 0
    finally:
        sup.stop()
    assert not api.running and not sc.running


def test_supervisor_restarts_a_crashed_process_after_the_delay(logs):
    api = ManagedProcess("api", SLEEP, cwd=logs, log_path=logs / "api.log")
    sc = ManagedProcess("sc", CRASH, cwd=logs, log_path=logs / "sc.log")
    sup = Supervisor(api=api, sc=sc, api_ready=lambda: True, restart_delay_s=60)
    t0 = datetime(2026, 10, 21, 11, 0)
    try:
        sup.start(now=t0)
        sup.tick(now=t0)
        for _ in range(50):
            if not sc.running:
                break
            time.sleep(0.1)
        sup.tick(now=t0 + timedelta(seconds=30))       # the supervisor notices the crash here
        assert sc.restarts == 0
        sup.tick(now=t0 + timedelta(seconds=60))       # 30 s since noticed: too early
        assert sc.restarts == 0
        assert sup.status(now=t0 + timedelta(seconds=61))["sc"]["last_exit_code"] == 3
        sup.tick(now=t0 + timedelta(seconds=91))
        assert sc.restarts == 1
    finally:
        sup.stop()


def test_supervisor_does_not_restart_when_stopped(logs):
    api = ManagedProcess("api", SLEEP, cwd=logs, log_path=logs / "api.log")
    sc = ManagedProcess("sc", SLEEP, cwd=logs, log_path=logs / "sc.log")
    sup = Supervisor(api=api, sc=sc, api_ready=lambda: True)
    t0 = datetime(2026, 10, 21, 11, 0)
    sup.start(now=t0)
    sup.tick(now=t0)
    sup.stop()
    sup.tick(now=t0 + timedelta(minutes=5))
    assert not api.running and not sc.running and sup.status(now=t0)["wanted"] is False
