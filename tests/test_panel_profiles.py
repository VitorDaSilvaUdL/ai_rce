"""Profiles of the panel (rce_viewer read-only, team, rce_admin superadmin) and the first login of imported users,
whose initial password is their own user name and must be changed."""
from fastapi.testclient import TestClient

from panel import auth, web
from tests.test_panel_web import ctx, login        # noqa: F401  (fixture and helper)


def client_for(ctx, name, pin):
    c = TestClient(web.create_app(ctx))
    assert c.post("/api/login", json={"name": name, "pin": pin}).status_code == 200
    return c


def test_roles_come_from_the_user_name():
    assert auth.role_of("rce_admin") == "admin" and auth.role_of("RCE_Viewer") == "viewer"
    assert auth.role_of("vitor.dasilva@udl.cat") == "team"


def test_import_users_with_their_own_name_as_initial_password(tmp_path):
    p = auth.People(tmp_path / "c.json")
    p.add("marc.medrano", "ya-tenia-una")
    created, skipped = p.import_users(["# equipo", "Vitor.DaSilva@udl.cat", "", "marc.medrano", "roger.vila"])
    assert created == ["vitor.dasilva", "roger.vila"] and skipped == ["marc.medrano"]
    assert p.verify("vitor.dasilva", "vitor.dasilva") and p.must_change("vitor.dasilva")
    assert p.verify("marc.medrano", "ya-tenia-una") and not p.must_change("marc.medrano")     # not reset


def test_first_login_forces_a_new_password(ctx):
    ctx.people.import_users(["vitor.dasilva@udl.cat"])
    c = TestClient(web.create_app(ctx))
    r = c.post("/api/login", json={"name": "vitor.dasilva", "pin": "vitor.dasilva"})
    assert r.status_code == 200 and r.json()["must_change"] is True
    assert c.get("/api/me").json()["must_change"] is True
    r = c.get("/api/status")
    assert r.status_code == 403 and r.json()["must_change"] is True
    assert c.post("/api/password", json={"current": "mala", "new": "Nueva-2026"}).status_code == 403
    assert c.post("/api/password", json={"current": "vitor.dasilva", "new": "vitor.dasilva"}).status_code == 422
    assert c.post("/api/password", json={"current": "vitor.dasilva", "new": "abc"}).status_code == 422
    assert c.post("/api/password", json={"current": "vitor.dasilva", "new": "Nueva-2026"}).status_code == 200
    assert c.get("/api/status").status_code == 200 and c.get("/api/me").json()["must_change"] is False
    assert ctx.people.verify("vitor.dasilva", "Nueva-2026")


def test_only_rce_admin_manages_people_and_other_passwords(ctx):
    admin = login(TestClient(web.create_app(ctx)), "rce_admin")
    ctx.people.add("roger.vila", "Roger-2026")
    team = client_for(ctx, "roger.vila", "Roger-2026")
    team.post("/api/lock/take")
    assert team.post("/api/people", json={"name": "marc.medrano", "pin": "1234"}).status_code == 403
    assert team.delete("/api/people/rce_admin").status_code == 403
    assert team.post("/api/password", json={"current": "Roger-2026", "new": "Roger-2027"}).status_code == 200   # own: yes
    team.post("/api/lock/release")
    admin.post("/api/lock/take")
    assert admin.post("/api/people", json={"name": "roger.vila", "pin": "Reset-1"}).status_code == 200
    assert ctx.people.must_change("roger.vila")                     # a password set by the admin must be changed
    assert admin.post("/api/people", json={"name": "rce_viewer", "pin": "Ver-2026"}).status_code == 200
    assert not ctx.people.must_change("rce_viewer")                 # shared account: no forced change


def test_only_rce_admin_changes_settings_and_the_mode(ctx):
    login(TestClient(web.create_app(ctx)), "rce_admin")
    ctx.people.add("roger.vila", "Roger-2026")
    team = client_for(ctx, "roger.vila", "Roger-2026")
    team.post("/api/lock/take")
    assert team.post("/api/settings", json={"refresh_s": 20}).status_code == 403
    assert team.post("/api/mode", json={"mode": "real", "confirm": "REAL", "password": "x"}).status_code == 403
    assert team.post("/api/command", json={"command": "open_cover", "confirm": True}).status_code == 403
    assert team.post("/api/dryrun", json={"command": "cold"}).status_code == 200          # the team keeps its tools
    assert team.post("/api/diary", json={"kind": "nota", "text": "ok"}).status_code == 200


def test_rce_viewer_only_looks(ctx):
    login(TestClient(web.create_app(ctx)), "rce_admin")
    ctx.people.add("rce_viewer", "Ver-2026")
    v = client_for(ctx, "rce_viewer", "Ver-2026")
    assert v.get("/api/status").status_code == 200 and v.get("/api/me").json()["role"] == "viewer"
    for path, body in (("/api/lock/take", {}), ("/api/diary", {"kind": "nota", "text": "x"}), ("/api/system/stop", {}),
                       ("/api/filebeat/restart", {}), ("/api/password", {"current": "Ver-2026", "new": "Otra-2026"})):
        assert v.post(path, json=body).status_code == 403, path
    r = v.post("/api/simulate", json={"now": "2026-10-20T09:30", "state": "Parada", "answer": "si", "frame": "cold"})
    assert r.status_code == 200
