"""Real PLC operations run by the panel (phase 3): progress, abort, cover test, probe, exit state, jobs.
All against an in-memory fake of the snap7 client (no PLC)."""
import threading
import time

import pytest
import snap7
from snap7.types import Areas

from panel import plc_ops


class FakeClient:
    """snap7.client.Client stand-in: DB memory, limit switches that follow the TH motor bits after a few reads."""
    instances = []

    def __init__(self):
        self.db = {}
        self.connected = False
        self.reads_moving = 0
        self.writes = []
        FakeClient.instances.append(self)

    def _buf(self, db):
        return self.db.setdefault(db, bytearray(256))

    def connect(self, ip, rack, slot, tcpport=102):
        if ip == "10.0.0.99":
            raise RuntimeError("timeout")
        self.connected = True

    def get_connected(self):
        return self.connected

    def disconnect(self):
        self.connected = False

    def destroy(self):
        pass

    def _th_moving(self):
        b = self._buf(503)[12]
        return bool(b & 0b010) or bool(b & 0b100)

    def db_read(self, db, start, size):
        if db == 10 and self._th_moving():
            self.reads_moving += 1
            if self.reads_moving >= 3:                       # the cover reaches the target: forward -> open
                forward = bool(self._buf(503)[12] & 0b010)
                self._buf(10)[0] = 0b01 if forward else 0b10
        return bytes(self._buf(db)[start:start + size])

    def read_area(self, area, db, start, size):
        return bytearray(self.db_read(db, start, size))

    def write_area(self, area, db, start, data):
        self._buf(db)[start:start + len(data)] = data
        self.writes.append((db, start, bytes(data)))


@pytest.fixture
def fake(monkeypatch):
    FakeClient.instances.clear()
    monkeypatch.setattr(snap7.client, "Client", FakeClient)
    monkeypatch.setattr(plc_ops.time, "sleep", lambda s: None)
    return FakeClient


def closed(client):
    client._buf(10)[0] = 0b10


# ----------------------------------------------------------------------------------------------- probe
def test_probe_reads_state_cover_alarms_and_schedule(fake):
    r = plc_ops.probe("10.0.0.1", 0, 1)
    assert r["ok"] is True and r["state"] in (None, "AutomaticMode", "Parada", "ColdMode1", "HeatMode1")
    assert r["cover"] in ("open", "closed", "moving", "error") and r["alarms"] == {"lluvia": False, "viento": False}
    assert "schedule_today" in r and fake.instances[-1].connected is False      # disconnected afterwards


def test_probe_reports_connection_failure(fake):
    r = plc_ops.probe("10.0.0.99", 0, 1)
    assert r["ok"] is False and "timeout" in r["error"]


# ----------------------------------------------------------------------------------------------- jobs
def test_open_cover_job_records_steps_and_cover_timeline(fake):
    runner = plc_ops.JobRunner()
    job = runner.start("open_cover", plc_factory=lambda: plc_ops.ProgressPLC("10.0.0.1", 0, 1), prepare=closed)
    job.thread.join(5)
    assert job.status == "done", job.error
    assert job.cover_after == "open" and any(s["kind"] == "write" for s in job.steps)
    assert job.cover_timeline[-1][1] == "open" and job.elapsed_s is not None
    assert not fake.instances[-1].connected


def test_job_can_be_aborted(fake, monkeypatch):
    gate = threading.Event()
    real_sleep = time.sleep
    monkeypatch.setattr(plc_ops.time, "sleep", lambda s: gate.wait(0.05))
    runner = plc_ops.JobRunner()
    job = runner.start("open_cover", plc_factory=lambda: plc_ops.ProgressPLC("10.0.0.1", 0, 1), prepare=closed)
    for _ in range(200):                      # wait until the 45 s motor wait starts
        if any(s["kind"] == "wait" for s in job.steps):
            break
        real_sleep(0.01)
    assert runner.abort() is True
    job.thread.join(5)
    assert job.status == "aborted" and job.steps[-1]["kind"] == "abort"


def test_only_one_job_at_a_time(fake, monkeypatch):
    monkeypatch.setattr(plc_ops.time, "sleep", lambda s: time.sleep(0.01) if False else threading.Event().wait(0.02))
    runner = plc_ops.JobRunner()
    runner.start("open_cover", plc_factory=lambda: plc_ops.ProgressPLC("10.0.0.1", 0, 1), prepare=closed)
    with pytest.raises(plc_ops.Busy):
        runner.start("close_cover", plc_factory=lambda: plc_ops.ProgressPLC("10.0.0.1", 0, 1))
    runner.abort()
    runner.current().thread.join(5)


def test_test_cover_opens_then_closes_with_timings(fake):
    runner = plc_ops.JobRunner()
    job = runner.start("test_cover", plc_factory=lambda: plc_ops.ProgressPLC("10.0.0.1", 0, 1), prepare=closed)
    job.thread.join(5)
    assert job.status == "done" and job.cover_after == "closed"
    texts = [s["text"] for s in job.steps]
    assert any("esté abierta" in t for t in texts) and any("esté cerrada" in t for t in texts)
    assert job.timings.get("open_s") is not None and job.timings.get("close_s") is not None


def test_job_with_alarm_blocks_the_opening(fake):
    def alarm(client):
        closed(client)
        client._buf(808)[160] = 0b100          # lluvia: DB808.DBX160.2
    runner = plc_ops.JobRunner()
    job = runner.start("open_cover", plc_factory=lambda: plc_ops.ProgressPLC("10.0.0.1", 0, 1), prepare=alarm)
    job.thread.join(5)
    assert job.status == "done" and job.cover_after == "closed" and any(s["kind"] == "blocked" for s in job.steps)


def test_connection_error_is_an_error_job(fake):
    runner = plc_ops.JobRunner()
    job = runner.start("cold", plc_factory=lambda: plc_ops.ProgressPLC("10.0.0.99", 0, 1))
    job.thread.join(5)
    assert job.status == "error" and "timeout" in job.error


# ----------------------------------------------------------------------------------------------- exit state
@pytest.mark.parametrize("exit_state, expect_writes", [("automatic", 14), ("parada", 14), ("as_is", 0)])
def test_apply_exit_state(fake, exit_state, expect_writes):
    r = plc_ops.apply_exit_state("10.0.0.1", 0, 1, exit_state, prepare=closed)
    assert r["ok"] is True
    assert sum(1 for s in r["steps"] if s["kind"] == "write") >= expect_writes
    if exit_state == "automatic":
        assert r["state_after"] == "AutomaticMode"


def test_job_status_dict_is_json_friendly(fake):
    runner = plc_ops.JobRunner()
    job = runner.start("automatic", plc_factory=lambda: plc_ops.ProgressPLC("10.0.0.1", 0, 1))
    job.thread.join(5)
    d = runner.status()
    assert d["status"] == "done" and d["command"] == "automatic" and isinstance(d["steps"], list) and d["started"]
