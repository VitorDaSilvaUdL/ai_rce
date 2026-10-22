"""Schedule of the panel: states held from a start to an end, and single commands at a time, executed in shadow (virtual
state) or, only for rce_admin, in real mode; audited; a manual trial in the diary for real-mode states."""
from datetime import datetime, timedelta

import pytest

from panel import agenda as ag

T0 = datetime(2026, 10, 22, 20, 0)
H = timedelta(hours=1)


class FakeExec:
    def __init__(self, state="Parada", paused=False, fail=None):
        self.state, self.paused, self.calls, self.fail = state, paused, [], fail

    def current(self):
        return self.state, self.paused

    def set_state(self, state, paused):
        self.calls.append(("state", state, paused))
        if self.fail:
            return False, self.fail
        self.state, self.paused = state, paused
        return True, ""

    def set_paused(self, paused):
        self.calls.append(("pause", paused))
        self.paused = paused
        return True, ""

    def command(self, cmd):
        self.calls.append(("cmd", cmd))
        return (False, self.fail) if self.fail else (True, "")


class Box:
    def __init__(self, tmp_path, mode="shadow"):
        self.mode = mode
        self.shadow, self.real = FakeExec(), FakeExec()
        self.audit, self.trials = [], []
        self.agenda = ag.Agenda(tmp_path / "agenda.json")
        self.sched = ag.Scheduler(self.agenda, executors={"shadow": self.shadow, "real": self.real}, mode=lambda: self.mode,
                                  audit=lambda author, action, result: self.audit.append((author, action, result)),
                                  trial=lambda kind, text, author: self.trials.append((kind, author)))


# ----------------------------------------------------------------------------------------------- creating entries
def test_state_entry_validation(tmp_path):
    a = ag.Agenda(tmp_path / "a.json")
    with pytest.raises(ValueError):
        a.add_state("roger", "Volando", T0 + H, T0 + 2 * H, now=T0)
    with pytest.raises(ValueError):
        a.add_state("roger", "ColdMode1", T0 + 2 * H, T0 + H, now=T0)              # end before start
    with pytest.raises(ValueError):
        a.add_state("roger", "ColdMode1", None, T0 - H, now=T0)                     # already over
    e = a.add_state("roger", "ColdMode1", None, T0 + 2 * H, now=T0)                  # no start = now
    assert e["start"] == "2026-10-22 20:00:00" and e["status"] == "pendiente" and e["at_end"] == "control"
    with pytest.raises(ValueError, match="solapa"):
        a.add_state("marc", "HeatMode1", T0 + H, T0 + 3 * H, now=T0)
    a.add_state("marc", "HeatMode1", T0 + 2 * H, T0 + 3 * H, now=T0)                 # back to back is fine
    with pytest.raises(ValueError):
        a.add_command("roger", "volar", T0 + H, now=T0)
    with pytest.raises(ValueError):
        a.add_command("roger", "open_cover", T0 - 5 * H, now=T0)
    assert [x["id"] for x in ag.Agenda(tmp_path / "a.json").entries()] == [1, 2]      # persisted


# ----------------------------------------------------------------------------------------------- running them
def test_state_from_start_to_end_and_back_to_the_control(tmp_path):
    b = Box(tmp_path)
    e = b.agenda.add_state("roger", "ColdMode1", T0 + H, T0 + 11 * H, now=T0)
    b.sched.tick(T0 + H - timedelta(minutes=1))
    assert b.shadow.calls == []
    b.sched.tick(T0 + H)
    assert b.shadow.calls == [("state", "ColdMode1", True)]
    assert b.agenda.get(e["id"])["status"] == "en_curso" and b.agenda.get(e["id"])["prev_state"] == "Parada"
    b.sched.tick(T0 + 11 * H)
    assert b.shadow.calls[-1] == ("pause", False) and b.agenda.get(e["id"])["status"] == "hecha"
    assert [x[1].split(":")[1] for x in b.audit] == ["inicio", "fin"] and b.audit[0][0] == "roger"
    assert b.trials == []                                         # shadow: no trial in the diary


@pytest.mark.parametrize("at_end, last", [("parada", ("state", "Parada", True)), ("anterior", ("state", "HeatMode1", False))])
def test_end_options(tmp_path, at_end, last):
    b = Box(tmp_path)
    b.shadow.state = "HeatMode1"
    b.agenda.add_state("roger", "ColdMode1", T0 + H, T0 + 2 * H, at_end=at_end, now=T0)
    b.sched.tick(T0 + H)
    b.sched.tick(T0 + 2 * H)
    assert b.shadow.calls[-1] == last


def test_back_to_back_states_skip_the_end_action(tmp_path):
    b = Box(tmp_path)
    b.agenda.add_state("roger", "ColdMode1", T0 + H, T0 + 2 * H, now=T0)
    b.agenda.add_state("roger", "HeatMode1", T0 + 2 * H, T0 + 3 * H, now=T0)
    b.sched.tick(T0 + H)
    b.sched.tick(T0 + 2 * H)
    assert b.shadow.calls == [("state", "ColdMode1", True), ("state", "HeatMode1", True)]


def test_single_command_on_time_or_lost(tmp_path):
    b = Box(tmp_path)
    c1 = b.agenda.add_command("roger", "open_cover", T0 + H, now=T0)
    c2 = b.agenda.add_command("roger", "close_cover", T0 + 2 * H, now=T0)
    b.sched.tick(T0 + H + timedelta(minutes=1))
    assert b.shadow.calls == [("cmd", "open_cover")] and b.agenda.get(c1["id"])["status"] == "hecha"
    b.sched.tick(T0 + 2 * H + timedelta(minutes=30))                     # panel stopped: more than 10 min late
    assert b.agenda.get(c2["id"])["status"] == "perdida" and len(b.shadow.calls) == 1


def test_missed_state_window_is_lost(tmp_path):
    b = Box(tmp_path)
    e = b.agenda.add_state("roger", "ColdMode1", T0 + H, T0 + 2 * H, now=T0)
    b.sched.tick(T0 + 3 * H)
    assert b.agenda.get(e["id"])["status"] == "perdida" and b.shadow.calls == []


def test_failures_are_recorded(tmp_path):
    b = Box(tmp_path)
    b.shadow.fail = "el PLC no responde"
    e = b.agenda.add_state("roger", "ColdMode1", T0 + H, T0 + 2 * H, now=T0)
    b.sched.tick(T0 + H)
    got = b.agenda.get(e["id"])
    assert got["status"] == "fallida" and "PLC" in got["message"] and b.audit[-1][2].startswith("error")


def test_real_mode_runs_only_the_entries_of_rce_admin_and_opens_a_trial(tmp_path):
    b = Box(tmp_path, mode="real")
    team = b.agenda.add_state("roger", "ColdMode1", T0 + H, T0 + 2 * H, now=T0)
    admin = b.agenda.add_state("rce_admin", "HeatMode1", T0 + 3 * H, T0 + 4 * H, now=T0)
    b.sched.tick(T0 + H)
    assert b.real.calls == [] and b.agenda.get(team["id"])["waiting"] is True
    b.sched.tick(T0 + 3 * H)
    assert b.real.calls == [("state", "HeatMode1", True)] and b.shadow.calls == []
    b.sched.tick(T0 + 4 * H)
    assert b.trials == [("ensayo_inicio", "rce_admin"), ("ensayo_fin", "rce_admin")]
    assert b.agenda.get(team["id"])["status"] == "perdida"


def test_cancel_a_running_state_ends_it_now(tmp_path):
    b = Box(tmp_path)
    e = b.agenda.add_state("roger", "ColdMode1", T0 + H, T0 + 5 * H, now=T0)
    b.sched.tick(T0 + H)
    b.sched.cancel(e["id"], "roger", now=T0 + 2 * H)
    assert b.agenda.get(e["id"])["status"] == "cancelada" and b.shadow.calls[-1] == ("pause", False)
    p = b.agenda.add_command("roger", "close_cover", T0 + 3 * H, now=T0 + 2 * H)
    b.sched.cancel(p["id"], "roger", now=T0 + 2 * H)
    b.sched.tick(T0 + 3 * H)
    assert b.agenda.get(p["id"])["status"] == "cancelada" and ("cmd", "close_cover") not in b.shadow.calls


def test_running_state_is_reapplied_after_a_restart_in_shadow(tmp_path):
    b = Box(tmp_path)
    b.agenda.add_state("roger", "ColdMode1", T0 + H, T0 + 5 * H, now=T0)
    b.sched.tick(T0 + H)
    again = ag.Scheduler(ag.Agenda(tmp_path / "agenda.json"), executors={"shadow": b.shadow, "real": b.real},
                         mode=lambda: "shadow", audit=lambda *a: None)
    b.shadow.calls.clear()
    again.tick(T0 + 2 * H)
    again.tick(T0 + 2 * H + timedelta(seconds=20))
    assert b.shadow.calls == [("state", "ColdMode1", True)]          # once, on the first tick


# ----------------------------------------------------------------------------------------------- executors
def test_shadow_executor_writes_the_virtual_state(tmp_path):
    from sc import state_io
    state_io.write_status(tmp_path, {"plc_state": "Parada"})
    seq = iter(range(7, 100))
    ex = ag.ShadowExecutor(tmp_path, next_seq=lambda: next(seq))
    assert ex.current() == ("Parada", False)
    assert ex.set_state("ColdMode1", True)[0] is True
    c = state_io.read_control(tmp_path)
    assert (c["set_state"], c["set_state_seq"], c["paused"], c["paused_by"]) == ("ColdMode1", 7, True, "agenda")
    assert ex.set_paused(False)[0] is True and state_io.read_control(tmp_path)["paused"] is False
    ok, msg = ex.command("heat")
    assert ok and state_io.read_control(tmp_path)["set_state"] == "HeatMode1" and "modo calor" in msg


class FakeJob:
    def __init__(self, status, error=None):
        self.status, self.error, self.calls = "running", None, 0
        self._final, self._err = status, error


class FakeJobs:
    def __init__(self, final="done", error=None, busy_for=0):
        self.final, self.error, self.busy_for, self.started = final, error, busy_for, []
        self.job = None

    def running(self):
        if self.busy_for > 0:
            self.busy_for -= 1
            return True
        return False

    def start(self, command, plc_factory):
        self.started.append(command)
        self.job = FakeJob(self.final, self.error)
        self.job.status, self.job.error = self.final, self.error
        return self.job


def test_real_executor_pauses_the_control_and_runs_the_plc_job(tmp_path):
    from sc import state_io
    jobs = FakeJobs()
    ex = ag.RealExecutor(tmp_path, jobs=jobs, plc_factory=lambda: None, probe=lambda: {"ok": True, "state": "Parada"},
                         sleep=lambda s: None)
    assert ex.current() == ("Parada", False)
    assert ex.set_state("ColdMode1", True) == (True, "Modo frío: hecho en el PLC")
    assert jobs.started == ["cold"] and state_io.read_control(tmp_path)["paused"] is True
    assert ex.command("open_cover")[0] is True and jobs.started[-1] == "open_cover"


def test_real_executor_reports_plc_errors_and_busy(tmp_path):
    ex = ag.RealExecutor(tmp_path, jobs=FakeJobs(final="error", error="sin conexión"), plc_factory=lambda: None,
                         probe=lambda: {"ok": False}, sleep=lambda s: None)
    ok, msg = ex.set_state("HeatMode1", True)
    assert not ok and "sin conexión" in msg
    busy = ag.RealExecutor(tmp_path, jobs=FakeJobs(busy_for=10**6), plc_factory=lambda: None, probe=lambda: {"ok": True},
                           sleep=lambda s: None, wait_free_s=5)
    ok, msg = busy.command("close_cover")
    assert not ok and "en ejecución" in msg
