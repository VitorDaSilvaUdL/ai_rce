"""People with a PIN, sessions and the single command lock."""
from datetime import datetime, timedelta

from panel import auth

T0 = datetime(2026, 10, 21, 10, 0)


def test_people_add_verify_and_persist(tmp_path):
    p = auth.People(tmp_path / "panel_config.json")
    assert p.names() == []
    p.add("Albert", "1234")
    assert p.verify("Albert", "1234") is True
    assert p.verify("Albert", "0000") is False and p.verify("Nadie", "1234") is False
    again = auth.People(tmp_path / "panel_config.json")
    assert again.names() == ["Albert"] and again.verify("Albert", "1234")
    assert "1234" not in (tmp_path / "panel_config.json").read_text(encoding="utf-8")


def test_people_remove_and_change_pin(tmp_path):
    p = auth.People(tmp_path / "c.json")
    p.add("Albert", "1234"); p.add("Roger", "5678")
    p.add("Albert", "9999")                 # change PIN
    assert p.verify("Albert", "9999") and not p.verify("Albert", "1234")
    p.remove("Roger")
    assert p.names() == ["Albert"]


def test_sessions_expire():
    s = auth.Sessions(ttl=timedelta(hours=12))
    tok = s.create("Albert", host="LAB-PC", now=T0)
    assert s.get(tok, now=T0 + timedelta(hours=1)) == {"name": "Albert", "host": "LAB-PC"}
    assert s.get(tok, now=T0 + timedelta(hours=13)) is None
    assert s.get("nope", now=T0) is None
    s.drop(tok)
    assert s.get(tok, now=T0) is None


def test_lock_single_holder_and_expiry():
    lock = auth.Lock(idle=timedelta(minutes=30))
    assert lock.holder(now=T0) is None
    assert lock.take("Albert", "LAB-PC", now=T0) == (True, "Albert")
    assert lock.take("Roger", "PC2", now=T0 + timedelta(minutes=5)) == (False, "Albert")
    assert lock.release("Roger", now=T0 + timedelta(minutes=6)) is False
    lock.touch("Albert", now=T0 + timedelta(minutes=20))
    assert lock.holder(now=T0 + timedelta(minutes=45))["name"] == "Albert"      # touched at +20, idle 25
    assert lock.holder(now=T0 + timedelta(minutes=51)) is None                   # idle > 30
    assert lock.take("Roger", "PC2", now=T0 + timedelta(minutes=52)) == (True, "Roger")
    assert lock.release("Roger", now=T0 + timedelta(minutes=53)) is True
    assert lock.holder(now=T0 + timedelta(minutes=53)) is None
