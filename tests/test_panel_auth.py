"""People with a PIN, sessions and the single command lock."""
from datetime import datetime, timedelta

from panel import auth

T0 = datetime(2026, 10, 21, 10, 0)


def test_people_add_verify_and_persist(tmp_path):
    p = auth.People(tmp_path / "panel_config.json")
    assert p.names() == []
    p.add("albert", "1234")
    assert p.verify("albert", "1234") is True
    assert p.verify("albert", "0000") is False and p.verify("Nadie", "1234") is False
    again = auth.People(tmp_path / "panel_config.json")
    assert again.names() == ["albert"] and again.verify("albert", "1234")
    assert "1234" not in (tmp_path / "panel_config.json").read_text(encoding="utf-8")


def test_user_is_the_university_email_without_the_domain(tmp_path):
    assert auth.user_id("  Vitor.DaSilva@udl.cat ") == "vitor.dasilva"
    assert auth.user_id("roger.vila@UDL.CAT") == "roger.vila" and auth.user_id("Albert") == "albert"
    p = auth.People(tmp_path / "c.json")
    p.add("Vitor.DaSilva@udl.cat", "Lab2026x")
    assert p.names() == ["vitor.dasilva"]
    assert p.verify("vitor.dasilva", "Lab2026x") and p.verify("VITOR.DASILVA@udl.cat", "Lab2026x")
    p.add("vitor.dasilva", "otra-clave")              # same person: changes the PIN, no duplicate
    assert p.names() == ["vitor.dasilva"] and p.verify("vitor.dasilva", "otra-clave")
    p.remove("Vitor.DaSilva@udl.cat")
    assert p.names() == []


def test_people_remove_and_change_pin(tmp_path):
    p = auth.People(tmp_path / "c.json")
    p.add("albert", "1234"); p.add("roger", "5678")
    p.add("albert", "9999")                 # change PIN
    assert p.verify("albert", "9999") and not p.verify("albert", "1234")
    p.remove("roger")
    assert p.names() == ["albert"]


def test_sessions_expire():
    s = auth.Sessions(ttl=timedelta(hours=12))
    tok = s.create("albert", host="LAB-PC", now=T0)
    assert s.get(tok, now=T0 + timedelta(hours=1)) == {"name": "albert", "host": "LAB-PC"}
    assert s.get(tok, now=T0 + timedelta(hours=13)) is None
    assert s.get("nope", now=T0) is None
    s.drop(tok)
    assert s.get(tok, now=T0) is None


def test_lock_single_holder_and_expiry():
    lock = auth.Lock(idle=timedelta(minutes=30))
    assert lock.holder(now=T0) is None
    assert lock.take("albert", "LAB-PC", now=T0) == (True, "albert")
    assert lock.take("roger", "PC2", now=T0 + timedelta(minutes=5)) == (False, "albert")
    assert lock.release("roger", now=T0 + timedelta(minutes=6)) is False
    lock.touch("albert", now=T0 + timedelta(minutes=20))
    assert lock.holder(now=T0 + timedelta(minutes=45))["name"] == "albert"      # touched at +20, idle 25
    assert lock.holder(now=T0 + timedelta(minutes=51)) is None                   # idle > 30
    assert lock.take("roger", "PC2", now=T0 + timedelta(minutes=52)) == (True, "roger")
    assert lock.release("roger", now=T0 + timedelta(minutes=53)) is True
    assert lock.holder(now=T0 + timedelta(minutes=53)) is None
