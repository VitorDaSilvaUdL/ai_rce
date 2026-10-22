"""Web API of the panel (FastAPI, TestClient): login, status, lock, dry-run, pause, diary, settings."""
import json
from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from panel import web
from panel.config import Config
from sc import state_io

HEADER = '"VarName";"TimeString";"VarValue";"Validity";"Time_ms"\r\n'


class FakeSupervisor:
    def __init__(self):
        self.wanted = False
        self.calls = []

    def start(self, now=None):
        self.wanted = True
        self.calls.append("start")

    def stop(self):
        self.wanted = False
        self.calls.append("stop")

    def restart(self, now=None):
        self.calls.append("restart")

    def restart_one(self, name):
        self.calls.append(f"restart:{name}")

    def stop_one(self, name):
        self.calls.append(f"stop:{name}")

    def start_one(self, name):
        self.calls.append(f"start:{name}")

    def status(self, now=None):
        return {"wanted": self.wanted, "api": {"running": self.wanted, "restarts": 0, "last_exit_code": None},
                "sc": {"running": self.wanted, "restarts": 0, "last_exit_code": None}, "events": []}


def fake_predict(samples):
    from datetime import timedelta
    t0 = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    prod = {(t0 + timedelta(minutes=15 * i)).strftime("%Y-%m-%d %H:%M"): 3e5 for i in range(192)}
    dem = {(t0 + timedelta(hours=i)).isoformat(timespec="seconds"): 1.0e6 for i in range(72)}
    return {"info": {"demand": {"hot_dem": dem, "cold_dem": dem}, "energy-production": {"hot": prod, "cold": prod},
                     "rain-prediction": {}}}


KEY, KEY1, KEY2 = "clave-de-prueba", "clave-nueva-1", "clave-nueva-2"   # REAL mode passwords of the tests

@pytest.fixture
def ctx(tmp_path):
    now = datetime.now().replace(second=0, microsecond=0)
    wincc = tmp_path / "wincc"
    wincc.mkdir()
    lines = "".join(f'"{tag}_RCEa";"{now:%d/%m/%Y %H:%M:%S}";{val};1;1\r\n'
                    for tag, val in (("TempT9", "12,5"), ("TempT6", "41,2"), ("CauQ1", "0"), ("TempExt", "15,1")))
    (wincc / "Lecturas0.csv").write_bytes((HEADER + lines).encode("latin-1"))
    state = tmp_path / "state"
    state.mkdir()
    state_io.write_status(state, {"written_at": now.isoformat(sep=" "), "cycle_time": now.isoformat(sep=" "),
                                  "next_cycle_time": now.isoformat(sep=" "), "execution": "shadow", "paused": False,
                                  "plc_state": "ColdMode1", "plc_mode": "cold", "cover": "open",
                                  "pumps": {"b1": "auto", "b2": "on", "ev1": "on", "ev2": "off"},
                                  "inputs": {"cold": 12.5}, "forecast": {"updated_day": now.day}, "plan": {},
                                  "decision": {"action": "no"}, "schedule": {"heat_start": "08:00:00"}})
    (tmp_path / "heartbeat_sc.txt").write_text(now.isoformat(), encoding="utf-8")
    sc_cfg = tmp_path / "config.prod.json"
    sc_cfg.write_text(json.dumps({"plc": {"mode": "shadow", "ip": "10.0.0.1", "rack": 0, "slot": 1}}), encoding="utf-8")
    probe_result = {"ok": True, "ts": "2026-10-21 12:00:00", "ip": "10.0.0.1", "state": "Parada", "plc_mode": "parada",
                    "cover": "closed", "alarms": {"lluvia": False, "viento": False}, "schedule_today": None,
                    "pumps": {"b1": "off", "b2": "off", "ev1": "off", "ev2": "on"}}
    exit_calls = []
    # no REAL password after the installation: these tests exercise the REAL mode, so they define one
    Config(state / "panel_config.json").set_real_password(KEY)
    c = web.PanelContext(root=tmp_path, state_dir=state, wincc_dir=wincc, dades_path=tmp_path / "dades.csv",
                         sc_config_path=sc_cfg, plc={"ip": "10.0.0.1", "rack": 0, "slot": 1},
                         probe=lambda: dict(probe_result), port_users=lambda: [],
                         apply_exit_state=lambda st: (exit_calls.append(st) or {"ok": True, "exit_state": st, "state_after": "AutomaticMode", "steps": []}),
                         log_path=tmp_path / "sc.log", heartbeat_path=tmp_path / "heartbeat_sc.txt",
                         config=Config(state / "panel_config.json"), supervisor=FakeSupervisor(),
                         mode="shadow", local_hosts={"testclient"}, service_state=lambda n: "running",
                         api_health=lambda: True, predict=fake_predict)
    c.exit_calls = exit_calls
    c.probe_result = probe_result
    return c


@pytest.fixture
def client(ctx):
    return TestClient(web.create_app(ctx))


def login(client, name="albert", pin="1234"):
    r = client.post("/api/setup", json={"name": name, "pin": pin})
    assert r.status_code in (200, 409)
    r = client.post("/api/login", json={"name": name, "pin": pin})
    assert r.status_code == 200
    return client


# ----------------------------------------------------------------------------------------------- setup and login
def test_setup_only_from_localhost_and_only_once(ctx):
    client = TestClient(web.create_app(ctx))
    assert client.get("/api/me").json() == {"name": None, "people_exist": False, "lock": None, "mode": "shadow", "host": "testclient"}
    assert client.post("/api/setup", json={"name": "albert", "pin": "1234"}).status_code == 200
    assert client.post("/api/setup", json={"name": "roger", "pin": "0000"}).status_code == 409
    ctx.local_hosts = set()
    ctx.people.remove("albert")
    assert client.post("/api/setup", json={"name": "albert", "pin": "1234"}).status_code == 403


def test_pin_accepts_letters_digits_and_symbols(client):
    assert client.post("/api/setup", json={"name": "albert", "pin": "Lab2026x"}).status_code == 200
    assert client.post("/api/login", json={"name": "albert", "pin": "lab2026x"}).status_code == 401   # case matters
    assert client.post("/api/login", json={"name": "albert", "pin": "Lab2026x"}).status_code == 200
    client.post("/api/lock/take")
    assert client.post("/api/people", json={"name": "roger", "pin": "Rv_2026!"}).status_code == 200
    assert client.post("/api/people", json={"name": "Marc", "pin": "abc"}).status_code == 422          # too short
    assert client.post("/api/people", json={"name": "Marc", "pin": "    "}).status_code == 422         # only spaces
    assert client.post("/api/people", json={"name": "Marc", "pin": "x" * 33}).status_code == 422       # too long


def test_quick_simulator_follows_the_policy_of_the_control(client):
    login(client)
    r = client.post("/api/simulate", json={"now": "2026-10-20T09:30", "state": "Parada", "answer": "si", "frame": "cold"})
    assert r.status_code == 200 and r.json()["next_state"] == "ColdMode1"


def test_manual_is_served_and_covers_every_tab(client):
    import re
    assert client.get("/static/manual.html").status_code == 200
    m = client.get("/static/manual.json").json()
    page = client.get("/").text
    tabs = re.findall(r'<button data-tab="[a-z]+"[^>]*>([^<]+)</button>', page)
    assert tabs and m["tabs"] == tabs
    assert set(tabs) <= {s["tab"] for s in m["sections"] if s["tab"]}
    assert 'href="/static/manual.html"' in page
    assert "PID2023-146193OB-I00" in m["acknowledgment"]


def test_login_required_for_the_api(client):
    assert client.get("/api/status").status_code == 401
    client.post("/api/setup", json={"name": "albert", "pin": "1234"})
    assert client.post("/api/login", json={"name": "albert", "pin": "9999"}).status_code == 401
    r = client.post("/api/login", json={"name": "albert", "pin": "1234"})
    assert r.status_code == 200 and "panel_session" in r.cookies
    assert client.get("/api/status").status_code == 200
    assert client.get("/api/me").json()["name"] == "albert"
    client.post("/api/logout")
    assert client.get("/api/status").status_code == 401


def test_page_is_served_without_login(client):
    r = client.get("/")
    assert r.status_code == 200 and "Panel RCE" in r.text


# ----------------------------------------------------------------------------------------------- status
def test_status_has_what_inicio_needs(client):
    login(client)
    s = client.get("/api/status").json()
    assert s["mode"] == "shadow" and s["supervisor"]["wanted"] is False
    assert s["control"]["plc_state"] == "ColdMode1" and s["control"]["cover"] == "open"
    assert s["wincc"]["last_write"] and s["wincc"]["age_s"] < 900
    assert {r["variable"] for r in s["readings"]} >= {"t_cold_tank_c", "t_hot_tank_c", "flow_rce_l_min"}
    t9 = next(r for r in s["readings"] if r["variable"] == "t_cold_tank_c")
    assert t9["value"] == 12.5 and t9["label"] == "Depósito frío (T9)" and t9["unit"] == "°C"
    assert {a["id"] for a in s["alerts"]} == {"sc_stopped"}
    assert s["lights"]["wincc"] == "ok" and s["lights"]["sc"] == "stopped" and s["lights"]["filebeat"] == "ok"
    assert s["lock"] is None and s["me"] == "albert"


# ----------------------------------------------------------------------------------------------- system
def test_system_start_stop_logs_the_action(client, ctx):
    login(client)
    assert client.post("/api/system/start").status_code == 200
    assert ctx.supervisor.calls == ["start"]
    assert client.post("/api/system/stop").status_code == 200
    assert client.post("/api/system/restart/sc").status_code == 200
    acts = client.get("/api/actions").json()
    assert [a["accion"] for a in acts] == ["system:start", "system:stop", "system:restart:sc"]
    assert acts[0]["autor"] == "albert" and acts[0]["modo"] == "shadow"


# ----------------------------------------------------------------------------------------------- lock and commands
def test_commands_need_the_lock(client, ctx):
    login(client)
    r = client.post("/api/dryrun", json={"command": "cold"})
    assert r.status_code == 423
    assert client.post("/api/lock/take").json()["lock"]["name"] == "albert"
    r = client.post("/api/dryrun", json={"command": "cold"})
    assert r.status_code == 200
    body = r.json()
    assert body["state_before"] == "ColdMode1" and body["state_after"] == "ColdMode1" and body["simulated"] is True
    assert client.get("/api/actions").json()[-1]["accion"] == "dryrun:cold"
    assert client.post("/api/lock/release").json()["lock"] is None


def test_dryrun_can_set_the_virtual_state_of_the_control(client, ctx):
    login(client)
    client.post("/api/lock/take")
    r = client.post("/api/dryrun", json={"command": "heat", "apply": True})
    assert r.status_code == 200 and r.json()["applied"] is True
    c = state_io.read_control(ctx.state_dir)
    assert c["set_state"] == "HeatMode1" and c["set_state_seq"] == 1
    client.post("/api/dryrun", json={"command": "parada", "apply": True})
    assert state_io.read_control(ctx.state_dir)["set_state_seq"] == 2


def test_second_person_cannot_take_the_lock(ctx):
    a = TestClient(web.create_app(ctx))
    login(a)
    a.post("/api/lock/take")
    ctx.people.add("roger", "5678")
    b = TestClient(a.app)
    b.post("/api/login", json={"name": "roger", "pin": "5678"})
    r = b.post("/api/lock/take")
    assert r.status_code == 423 and r.json()["lock"]["name"] == "albert"
    assert b.post("/api/dryrun", json={"command": "cold"}).status_code == 423


def test_pause_writes_control_json(client, ctx):
    login(client)
    assert client.post("/api/control/pause", json={"paused": True}).status_code == 423
    client.post("/api/lock/take")
    assert client.post("/api/control/pause", json={"paused": True}).status_code == 200
    c = state_io.read_control(ctx.state_dir)
    assert c["paused"] is True and c["paused_by"] == "albert" and c["since"]
    client.post("/api/control/pause", json={"paused": False})
    assert state_io.read_control(ctx.state_dir)["paused"] is False


# ----------------------------------------------------------------------------------------------- simulator, diary
def test_simulate(client):
    login(client)
    r = client.post("/api/simulate", json={"now": "2026-10-20T22:00", "state": "Parada", "answer": "si", "alarm": False})
    assert r.status_code == 200 and r.json()["next_state"] == "ColdMode1" and r.json()["cover"] == "open"
    assert client.post("/api/simulate", json={"now": "bad", "state": "Parada", "answer": "si"}).status_code == 422


def test_diary(client):
    login(client)
    r = client.post("/api/diary", json={"kind": "ensayo_inicio", "text": "Tipo 2, T9 14 °C"})
    assert r.status_code == 200 and r.json()["ensayo_id"].startswith("E")
    client.post("/api/diary", json={"kind": "incidencia", "text": "viento"})
    rows = client.get("/api/diary").json()
    assert [x["tipo"] for x in rows] == ["ensayo_inicio", "incidencia"] and rows[1]["autor"] == "albert"
    assert client.post("/api/diary", json={"kind": "otra_cosa", "text": "x"}).status_code == 422


# ----------------------------------------------------------------------------------------------- settings, mode, history
def test_settings_and_people(client, ctx):
    login(client)
    s = client.get("/api/settings").json()
    assert s["exit_state"] == "automatic" and s["people"] == ["albert"] and s["mode"] == "shadow"
    assert client.post("/api/settings", json={"exit_state": "parada"}).status_code == 423
    client.post("/api/lock/take")
    assert client.post("/api/settings", json={"exit_state": "parada"}).status_code == 200
    assert client.post("/api/settings", json={"exit_state": "volando"}).status_code == 422
    assert ctx.config.get("exit_state") == "parada"
    assert client.post("/api/people", json={"name": "roger", "pin": "5678"}).status_code == 200
    assert client.get("/api/settings").json()["people"] == ["albert", "roger"]
    assert client.delete("/api/people/Roger").status_code == 200
    assert client.delete("/api/people/Albert").status_code == 409        # cannot remove yourself


def test_mode_real_needs_the_lock_the_word_and_a_live_plc(client, ctx):
    login(client)
    assert client.post("/api/mode", json={"mode": "real", "confirm": "REAL", "password": KEY}).status_code == 423
    client.post("/api/lock/take")
    assert client.post("/api/mode", json={"mode": "real", "confirm": "si"}).status_code == 422
    assert client.post("/api/mode", json={"mode": "shadow"}).status_code == 409          # already shadow
    ctx.probe_result["ok"] = False; ctx.probe_result["error"] = "timeout"
    assert client.post("/api/mode", json={"mode": "real", "confirm": "REAL", "password": KEY}).status_code == 503
    ctx.probe_result["ok"] = True
    ctx.port_users = lambda: [4321]
    assert client.post("/api/mode", json={"mode": "real", "confirm": "REAL", "password": KEY}).status_code == 409
    ctx.port_users = lambda: []
    r = client.post("/api/mode", json={"mode": "real", "confirm": "REAL", "password": KEY})
    assert r.status_code == 200 and r.json()["mode"] == "real" and r.json()["plc"]["state"] == "Parada"
    assert json.loads(ctx.sc_config_path.read_text(encoding="utf-8"))["plc"]["mode"] == "real"
    assert ctx.supervisor.calls[-1] == "restart:sc" and ctx.mode == "real"
    assert client.get("/api/me").json()["mode"] == "real"
    s = client.get("/api/status").json()
    assert s["mode"] == "real" and s["plc"]["ok"] and s["lights"]["plc"] == "ok"
    d = client.get("/api/diary").json()
    assert d[-1]["tipo"] == "modo" and "REAL" in d[-1]["texto"]


def test_real_mode_needs_the_password_defined_on_the_lab_pc(client, ctx):
    login(client)
    client.post("/api/lock/take")
    ctx.config.set(real_password=None)
    s = client.get("/api/settings").json()
    assert s["real_password_set"] is False and "real_password" not in s
    r = client.post("/api/mode", json={"mode": "real", "confirm": "REAL", "password": "mal"})
    assert r.status_code == 403 and "contraseña" in r.json()["detail"]
    assert ctx.mode == "shadow" and ctx.supervisor.calls == []
    # defined only from the lab PC itself, with the lock, at least 8 characters
    assert client.post("/api/real-password", json={"password": "corta"}).status_code == 422
    ctx.local_hosts = set()
    assert client.post("/api/real-password", json={"password": KEY1}).status_code == 403
    ctx.local_hosts = {"testclient"}
    assert client.post("/api/real-password", json={"password": KEY1}).status_code == 200
    s = client.get("/api/settings").json()
    assert s["real_password_set"] is True and "salt" not in json.dumps(s)
    assert "real_password" not in json.dumps(client.get("/api/status").json())
    # changing it needs the current one
    assert client.post("/api/real-password", json={"password": KEY2, "current": "mal"}).status_code == 403
    assert client.post("/api/real-password", json={"password": KEY2, "current": KEY1}).status_code == 200
    assert client.post("/api/settings", json={"real_password": "x", "refresh_s": 20}).status_code == 200   # ignored
    assert ctx.config.verify_real_password("clave-nueva-2") and not ctx.config.verify_real_password("x")
    # the word REAL is still required, and a wrong password is refused; five failures block the mode for a while
    assert client.post("/api/mode", json={"mode": "real", "confirm": "si", "password": KEY2}).status_code == 422
    for _ in range(5):
        assert client.post("/api/mode", json={"mode": "real", "confirm": "REAL", "password": "mal"}).status_code == 403
    assert client.post("/api/mode", json={"mode": "real", "confirm": "REAL", "password": KEY2}).status_code == 429
    ctx.real_failures.clear()
    assert client.post("/api/mode", json={"mode": "real", "confirm": "REAL", "password": KEY2}).status_code == 200
    assert ctx.mode == "real"
    d = client.get("/api/diary").json()
    assert any(e["tipo"] == "modo" and "contraseña del modo real" in e["texto"].lower() for e in d)


def test_mode_back_to_shadow_applies_the_exit_state(client, ctx):
    login(client)
    client.post("/api/lock/take")
    client.post("/api/settings", json={"exit_state": "parada"})
    client.post("/api/mode", json={"mode": "real", "confirm": "REAL", "password": KEY})
    r = client.post("/api/mode", json={"mode": "shadow"})
    assert r.status_code == 200 and r.json()["mode"] == "shadow" and r.json()["exit"]["state_after"] == "AutomaticMode"
    assert ctx.exit_calls == ["parada"]
    assert ctx.supervisor.calls[-2:] == ["stop:sc", "start:sc"]
    assert json.loads(ctx.sc_config_path.read_text(encoding="utf-8"))["plc"]["mode"] == "shadow"


def test_real_commands_need_real_mode_pause_lock_and_confirmation(client, ctx, monkeypatch):
    from tests.test_panel_plc_ops import FakeClient, closed
    import snap7
    from panel import plc_ops
    monkeypatch.setattr(snap7.client, "Client", FakeClient)
    monkeypatch.setattr(plc_ops.time, "sleep", lambda s: None)
    login(client)
    client.post("/api/lock/take")
    assert client.post("/api/command", json={"command": "open_cover", "confirm": True}).status_code == 409   # shadow
    client.post("/api/mode", json={"mode": "real", "confirm": "REAL", "password": KEY})
    assert client.post("/api/command", json={"command": "open_cover", "confirm": True}).status_code == 409   # not paused
    client.post("/api/control/pause", json={"paused": True})
    assert client.post("/api/command", json={"command": "open_cover", "confirm": False}).status_code == 422
    assert client.post("/api/command", json={"command": "fly", "confirm": True}).status_code == 422
    ctx.plc_factory = lambda: plc_ops.ProgressPLC("10.0.0.1", 0, 1)
    r = client.post("/api/command", json={"command": "automatic", "confirm": True})
    assert r.status_code == 200 and r.json()["command"] == "automatic"
    ctx.jobs.current().thread.join(5)
    j = client.get("/api/job").json()
    assert j["status"] == "done" and j["state_after"] == "AutomaticMode" and any(s["kind"] == "write" for s in j["steps"])
    assert client.post("/api/job/abort").json()["ok"] is False
    assert [a["accion"] for a in client.get("/api/actions").json()][-2:] == ["command:automatic", "job:abort"]
    assert client.get("/api/diary").json()[-1]["tipo"] == "mando"


def test_plc_read_permission(client, ctx):
    login(client)
    assert client.get("/api/plc").status_code == 403
    client.post("/api/lock/take")
    client.post("/api/settings", json={"plc_read_allowed": True})
    r = client.get("/api/plc")
    assert r.status_code == 200 and r.json()["state"] == "Parada"


def test_history_and_events(client, ctx):
    login(client)
    r = client.get("/api/history?hours=24")
    assert r.status_code == 200
    h = r.json()
    assert "series" in h and "decisions" in h and "events" in h
    assert "t_cold_tank_c" in h["series"] and h["series"]["t_cold_tank_c"]["points"][0][1] == 12.5
    r = client.get("/api/readings?hours=6")
    assert r.status_code == 200 and any(x["variable"] == "t_ambient_c" for x in r.json()["readings"])


def test_simulate_full_and_replay(client):
    login(client)
    r = client.post("/api/simulate_full", json={"state": "Parada", "overrides": {"cold": "18.5", "bogus": 1}})
    assert r.status_code == 200
    b = r.json()
    assert b["inputs"]["cold"] == 18.5 and b["decision"]["reasons"] and b["plan"]["kind"] in ("hot", "cold") and b["note"]
    assert client.post("/api/simulate_full", json={"now": "mal"}).status_code == 422
    today = datetime.now().strftime("%Y-%m-%d")
    r = client.post("/api/replay", json={"date": today, "state": "Parada"})
    assert r.status_code == 200 and len(r.json()["steps"]) == 96 and r.json()["predict_calls"] == 3
    assert client.post("/api/replay", json={"date": "2020-01-01"}).status_code == 422
    assert client.post("/api/replay", json={"date": "ayer"}).status_code == 422
    assert [a["accion"] for a in client.get("/api/actions").json()][-2:] == ["simulate_full", f"replay:{today}"]
