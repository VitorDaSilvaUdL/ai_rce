"""Audit: every action done in the panel, with the user, the PC, the mode and the result; filters and CSV download."""
from datetime import datetime, timedelta

from fastapi.testclient import TestClient

from panel import web
from panel.journal import category_of
from tests.test_panel_web import ctx, login        # noqa: F401  (fixture and helper)


def test_access_lock_and_diary_are_audited(ctx):
    c = TestClient(web.create_app(ctx))
    login(c)
    assert c.post("/api/login", json={"name": "nadie", "pin": "9999"}).status_code == 401
    c.post("/api/lock/take")
    c.post("/api/diary", json={"kind": "nota", "text": "Viento fuerte"})
    c.post("/api/lock/release")
    c.post("/api/logout")
    acts = [(a["autor"], a["accion"], a["resultado"]) for a in ctx.actions.rows()]
    assert ("rce_admin", "login", "ok") in acts
    assert ("nadie", "login", "error: usuario o contraseña incorrectos") in acts
    assert ("rce_admin", "lock:take", "ok") in acts and ("rce_admin", "lock:release", "ok") in acts
    assert ("rce_admin", "diario:nota", "ok") in acts and ("rce_admin", "logout", "ok") in acts
    assert all(a["host"] for a in ctx.actions.rows())


def test_categories():
    assert category_of("login") == "acceso" and category_of("lock:take") == "mando"
    assert category_of("dryrun:cold") == "mandos" and category_of("command:open_cover") == "mandos"
    assert category_of("people:add:roger") == "cuentas" and category_of("mode:real") == "modo"
    assert category_of("agenda:start:3") == "agenda" and category_of("algo:raro") == "otros"


def test_audit_filters_newest_first_and_csv(ctx):
    t0 = datetime(2020, 1, 15, 9, 0)        # far from today: the login of the test itself (now) never falls in the window
    ctx.actions.add("roger.vila", "login", "shadow", "ok", now=t0, host="10.0.0.5")
    ctx.actions.add("roger.vila", "dryrun:cold", "shadow", "ok", now=t0 + timedelta(minutes=5), host="10.0.0.5")
    ctx.actions.add("vitor.dasilva", "settings:refresh_s=20", "shadow", "ok", now=t0 + timedelta(hours=2), host="LAB")
    c = TestClient(web.create_app(ctx))
    login(c)
    r = c.get("/api/audit", params={"user": "roger.vila"}).json()
    assert [x["accion"] for x in r["rows"]] == ["dryrun:cold", "login"]                 # newest first
    assert {"rce_admin", "roger.vila", "vitor.dasilva"} <= set(r["users"])
    assert [x["accion"] for x in c.get("/api/audit", params={"category": "mandos"}).json()["rows"]] == ["dryrun:cold"]
    r = c.get("/api/audit", params={"since": "2020-01-15T10:00", "until": "2020-01-15T12:00"}).json()
    assert [x["accion"] for x in r["rows"]] == ["settings:refresh_s=20"]
    assert r["rows"][0]["categoria"] == "ajustes"
    csv = c.get("/api/audit.csv", params={"user": "roger.vila"})
    assert csv.status_code == 200 and csv.headers["content-type"].startswith("text/csv")
    assert "attachment" in csv.headers["content-disposition"]
    lines = csv.text.strip().splitlines()
    assert lines[0].startswith("timestamp,autor,accion") and len(lines) == 3


def test_viewer_can_read_the_audit(ctx):
    login(TestClient(web.create_app(ctx)))
    ctx.people.add("rce_viewer", "Ver-2026")
    v = TestClient(web.create_app(ctx))
    v.post("/api/login", json={"name": "rce_viewer", "pin": "Ver-2026"})
    assert v.get("/api/audit").status_code == 200
