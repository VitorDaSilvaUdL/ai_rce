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


def _wait_exit(proc):
    for _ in range(50):
        if proc.proc is None or proc.proc.poll() is not None:
            return
        time.sleep(0.1)


def test_supervisor_waits_longer_after_each_quick_crash(logs):
    """A process that crashes at start-up (as sc.main did with no cold demand) is retried after 60 s, 120 s, 240 s...
    up to max_restart_delay_s, not every minute forever."""
    api = ManagedProcess("api", SLEEP, cwd=logs, log_path=logs / "api.log")
    sc = ManagedProcess("sc", CRASH, cwd=logs, log_path=logs / "sc.log")
    sup = Supervisor(api=api, sc=sc, api_ready=lambda: True, restart_delay_s=60, max_restart_delay_s=200)
    t = datetime(2026, 10, 22, 11, 0)
    try:
        sup.start(now=t)
        sup.tick(now=t)                                  # starts sc, which crashes
        expected = [60, 120, 200, 200]                   # 240 capped at 200
        for n, delay in enumerate(expected, start=1):
            _wait_exit(sc)
            sup.tick(now=t)                              # notices the crash
            st = sup.status(now=t)["sc"]
            assert st["consecutive_failures"] == n and st["next_restart_in_s"] == delay
            assert any(f"reintento en {delay} s" in e["text"] for e in sup.status(now=t)["events"])
            sup.tick(now=t + timedelta(seconds=delay - 1))
            assert sc.restarts == n - 1                  # too early
            t = t + timedelta(seconds=delay)
            sup.tick(now=t)
            assert sc.restarts == n
    finally:
        sup.stop()


def test_supervisor_backoff_restarts_from_the_base_delay_after_a_stable_run(logs):
    api = ManagedProcess("api", SLEEP, cwd=logs, log_path=logs / "api.log")
    sc = ManagedProcess("sc", CRASH, cwd=logs, log_path=logs / "sc.log")
    sup = Supervisor(api=api, sc=sc, api_ready=lambda: True, restart_delay_s=60, stable_run_s=600)
    t = datetime(2026, 10, 22, 11, 0)
    try:
        sup.start(now=t)
        sup.tick(now=t)
        _wait_exit(sc)
        sup.tick(now=t)
        t += timedelta(seconds=60)
        sup.tick(now=t)                                  # restart 1
        _wait_exit(sc)
        sup.tick(now=t)
        assert sup.status(now=t)["sc"]["next_restart_in_s"] == 120
        t += timedelta(seconds=120)
        sup.tick(now=t)                                  # restart 2; this run "lasts" 20 min
        _wait_exit(sc)
        sc.started_at -= timedelta(minutes=20)
        sup.tick(now=t)
        st = sup.status(now=t)["sc"]
        assert st["consecutive_failures"] == 1 and st["next_restart_in_s"] == 60
    finally:
        sup.stop()


def test_supervisor_manual_start_clears_the_backoff(logs):
    api = ManagedProcess("api", SLEEP, cwd=logs, log_path=logs / "api.log")
    sc = ManagedProcess("sc", CRASH, cwd=logs, log_path=logs / "sc.log")
    sup = Supervisor(api=api, sc=sc, api_ready=lambda: True, restart_delay_s=60)
    t = datetime(2026, 10, 22, 11, 0)
    try:
        sup.start(now=t)
        sup.tick(now=t)
        _wait_exit(sc)
        sup.tick(now=t)
        assert sup.status(now=t)["sc"]["consecutive_failures"] == 1
        sup.restart_one("sc")                            # someone presses «reiniciar control» in the panel
        st = sup.status(now=t)["sc"]
        assert st["consecutive_failures"] == 0 and st["next_restart_in_s"] is None
    finally:
        sup.stop()
