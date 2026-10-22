"""Command line import of the team: python -m panel.import_users <file>."""
from panel import import_users
from panel.auth import People


def test_import_users_from_a_file(tmp_path, capsys):
    users = tmp_path / "usuarios.txt"
    users.write_text("# equipo TED-RCE\nvitor.dasilva@udl.cat\nroger.vila\n\nroger.vila\n", encoding="utf-8")
    state = tmp_path / "state"
    assert import_users.main([str(users)], state_dir=state) == 0
    out = capsys.readouterr().out
    assert "vitor.dasilva" in out and "roger.vila" in out and "ya existía" in out
    p = People(state / "panel_config.json")
    assert p.names() == ["vitor.dasilva", "roger.vila"]
    assert p.verify("vitor.dasilva", "vitor.dasilva") and p.must_change("vitor.dasilva")
    assert "vitor.dasilva\"" not in (state / "panel_config.json").read_text(encoding="utf-8").split('"hash"')[1][:80]


def test_missing_file_is_an_error(tmp_path, capsys):
    assert import_users.main([str(tmp_path / "no.txt")], state_dir=tmp_path) == 2
