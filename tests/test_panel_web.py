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
    c = web.PanelContext(root=tmp_path, state_dir=state, wincc_dir=wincc, dades_path=tmp_path / "dades.csv",
                         log_path=tmp_path / "sc.log", heartbeat_path=tmp_path / "heartbeat_sc.txt",
                         config=Config(state / "panel_config.json"), supervisor=FakeSupervisor(),
                         mode="shadow", local_hosts={"testclient"}, service_state=lambda n: "running",
                         api_health=lambda: True, predict=fake_predict)
    return c


@pytest.fixture
def client(ctx):
    return TestClient(web.create_app(ctx))


def login(client, name="Albert", pin="1234"):
    r = client.post("/api/setup", json={"name": name, "pin": pin})
    assert r.status_code in (200, 409)
    r = client.post("/api/login", json={"name": name, "pin": pin})
    assert r.status_code == 200
    return client


# ----------------------------------------------------------------------------------------------- setup and login
def test_setup_only_from_localhost_and_only_once(ctx):
    client = TestClient(web.create_app(ctx))
    assert client.get("/api/me").json() == {"name": None, "people_exist": False, "lock": None, "mode": "shadow", "host": "testclient"}
    assert client.post("/api/setup", json={"name": "Albert", "pin": "1234"}).status_code == 200
    assert client.post("/api/setup", json={"name": "Roger", "pin": "0000"}).status_code == 409
    ctx.local_hosts = set()
    ctx.people.remove("Albert")
    assert client.post("/api/setup", json={"name": "Albert", "pin": "1234"}).status_code == 403


def test_login_required_for_the_api(client):
    assert client.get("/api/status").status_code == 401
    client.post("/api/setup", json={"name": "Albert", "pin": "1234"})
    assert client.post("/api/login", json={"name": "Albert", "pin": "9999"}).status_code == 401
    r = client.post("/api/login", json={"name": "Albert", "pin": "1234"})
    assert r.status_code == 200 and "panel_session" in r.cookies
    assert client.get("/api/status").status_code == 200
    assert client.get("/api/me").json()["name"] == "Albert"
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
    assert s["lock"] is None and s["me"] == "Albert"


# ----------------------------------------------------------------------------------------------- system
def test_system_start_stop_logs_the_action(client, ctx):
    login(client)
    assert client.post("/api/system/start").status_code == 200
    assert ctx.supervisor.calls == ["start"]
    assert client.post("/api/system/stop").status_code == 200
    assert client.post("/api/system/restart/sc").status_code == 200
    acts = client.get("/api/actions").json()
    assert [a["accion"] for a in acts] == ["system:start", "system:stop", "system:restart:sc"]
    assert acts[0]["autor"] == "Albert" and acts[0]["modo"] == "shadow"


# ----------------------------------------------------------------------------------------------- lock and commands
def test_commands_need_the_lock(client, ctx):
    login(client)
    r = client.post("/api/dryrun", json={"command": "cold"})
    assert r.status_code == 423
    assert client.post("/api/lock/take").json()["lock"]["name"] == "Albert"
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
    ctx.people.add("Roger", "5678")
    b = TestClient(a.app)
    b.post("/api/login", json={"name": "Roger", "pin": "5678"})
    r = b.post("/api/lock/take")
    assert r.status_code == 423 and r.json()["lock"]["name"] == "Albert"
    assert b.post("/api/dryrun", json={"command": "cold"}).status_code == 423


def test_pause_writes_control_json(client, ctx):
    login(client)
    assert client.post("/api/control/pause", json={"paused": True}).status_code == 423
    client.post("/api/lock/take")
    assert client.post("/api/control/pause", json={"paused": True}).status_code == 200
    c = state_io.read_control(ctx.state_dir)
    assert c["paused"] is True and c["paused_by"] == "Albert" and c["since"]
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
    assert [x["tipo"] for x in rows] == ["ensayo_inicio", "incidencia"] and rows[1]["autor"] == "Albert"
    assert client.post("/api/diary", json={"kind": "otra_cosa", "text": "x"}).status_code == 422


# ----------------------------------------------------------------------------------------------- settings, mode, history
def test_settings_and_people(client, ctx):
    login(client)
    s = client.get("/api/settings").json()
    assert s["exit_state"] == "automatic" and s["people"] == ["Albert"] and s["mode"] == "shadow"
    assert client.post("/api/settings", json={"exit_state": "parada"}).status_code == 423
    client.post("/api/lock/take")
    assert client.post("/api/settings", json={"exit_state": "parada"}).status_code == 200
    assert client.post("/api/settings", json={"exit_state": "volando"}).status_code == 422
    assert ctx.config.get("exit_state") == "parada"
    assert client.post("/api/people", json={"name": "Roger", "pin": "5678"}).status_code == 200
    assert client.get("/api/settings").json()["people"] == ["Albert", "Roger"]
    assert client.delete("/api/people/Roger").status_code == 200
    assert client.delete("/api/people/Albert").status_code == 409        # cannot remove yourself


def test_mode_change_is_phase_3(client):
    login(client)
    client.post("/api/lock/take")
    r = client.post("/api/mode", json={"mode": "real"})
    assert r.status_code == 501


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
