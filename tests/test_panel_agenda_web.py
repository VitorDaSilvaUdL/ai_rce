"""Schedule through the API: who can create and cancel entries, immediate start, status for Inicio, audit."""
from datetime import datetime, timedelta

from fastapi.testclient import TestClient

from panel import web
from sc import state_io
from tests.test_panel_web import ctx, login        # noqa: F401  (fixture and helper)


def as_user(ctx, name, pin="Clave-2026", lock=True):
    if name not in ctx.people.names():
        ctx.people.add(name, pin)
    c = TestClient(web.create_app(ctx))
    assert c.post("/api/login", json={"name": name, "pin": pin}).status_code == 200
    if lock:
        assert c.post("/api/lock/take").status_code == 200
    return c


def fmt(dt):
    return dt.strftime("%Y-%m-%dT%H:%M")


def test_team_schedules_a_state_that_starts_now_in_shadow(ctx):
    login(TestClient(web.create_app(ctx)))
    c = as_user(ctx, "roger.vila")
    end = datetime.now() + timedelta(hours=2)
    r = c.post("/api/agenda", json={"kind": "estado", "target": "ColdMode1", "end": fmt(end)})
    assert r.status_code == 200, r.text
    e = r.json()
    assert e["status"] == "en_curso" and e["author"] == "roger.vila"               # no start: starts right away
    ctrl = state_io.read_control(ctx.state_dir)
    assert ctrl["set_state"] == "ColdMode1" and ctrl["paused"] is True and ctrl["paused_by"] == "agenda"
    listing = c.get("/api/agenda").json()
    assert listing["entries"][0]["id"] == e["id"] and "ColdMode1" in listing["states"] and "control" in listing["at_end"]
    s = c.get("/api/status").json()
    assert s["agenda"]["running"]["id"] == e["id"]
    acts = [a["accion"] for a in ctx.actions.rows()]
    assert any(a.startswith("agenda:crear:#") for a in acts) and any(a.startswith("agenda:inicio:#") for a in acts)


def test_schedule_permissions_and_validation(ctx):
    login(TestClient(web.create_app(ctx)))
    ctx.people.add("rce_viewer", "Ver-2026")
    v = TestClient(web.create_app(ctx))
    v.post("/api/login", json={"name": "rce_viewer", "pin": "Ver-2026"})
    later = datetime.now() + timedelta(hours=3)
    assert v.post("/api/agenda", json={"kind": "mando", "target": "open_cover", "start": fmt(later)}).status_code == 403
    nolock = as_user(ctx, "marc.medrano", lock=False)
    assert nolock.post("/api/agenda", json={"kind": "mando", "target": "open_cover", "start": fmt(later)}).status_code == 423
    c = as_user(ctx, "roger.vila")
    r = c.post("/api/agenda", json={"kind": "estado", "target": "ColdMode1", "start": fmt(later), "end": fmt(later - timedelta(hours=1))})
    assert r.status_code == 422 and "fin" in r.json()["detail"]
    e = c.post("/api/agenda", json={"kind": "mando", "target": "close_cover", "start": fmt(later)}).json()
    c.post("/api/lock/release")
    other = as_user(ctx, "marc.medrano")
    assert other.post(f"/api/agenda/{e['id']}/cancel").status_code == 403              # not the author
    other.post("/api/lock/release")
    admin = TestClient(web.create_app(ctx))
    login(admin)
    admin.post("/api/lock/take")
    assert admin.post(f"/api/agenda/{e['id']}/cancel").status_code == 200              # rce_admin can
    assert ctx.agenda.get(e["id"])["status"] == "cancelada"


def test_failed_entries_raise_an_alert(ctx):
    login(TestClient(web.create_app(ctx)))
    e = ctx.agenda.add_command("roger.vila", "open_cover", datetime.now() + timedelta(minutes=5))
    ctx.agenda.update(e["id"], status="fallida", message="el PLC no responde", started_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    c = TestClient(web.create_app(ctx))
    login(c)
    alerts = c.get("/api/status").json()["alerts"]
    assert any(a["id"] == f"agenda:{e['id']}" and "Agenda" in a["title"] for a in alerts)
