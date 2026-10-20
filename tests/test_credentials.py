"""Passwords never live in versioned files: they come from an environment variable or sc/secrets.local.json."""
import importlib
import json
import re
from pathlib import Path

import pytest

from sc import credentials

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def local_file(tmp_path, monkeypatch):
    f = tmp_path / "secrets.local.json"
    f.write_text(json.dumps({"smtp_password": "from-file", "gmail_app_password": "gmail-from-file"}), encoding="utf-8")
    monkeypatch.setattr(credentials, "SECRETS_FILE", f)
    monkeypatch.delenv("SC_SMTP_PASSWORD", raising=False)
    monkeypatch.delenv("GMAIL_APP_PASSWORD", raising=False)
    return f


def test_environment_variable_wins(local_file, monkeypatch):
    monkeypatch.setenv("SC_SMTP_PASSWORD", "from-env")
    assert credentials.get_secret("smtp_password") == "from-env"


def test_local_file_when_no_environment_variable(local_file):
    assert credentials.get_secret("smtp_password") == "from-file"


def test_missing_secret_is_none(tmp_path, monkeypatch):
    monkeypatch.setattr(credentials, "SECRETS_FILE", tmp_path / "absent.json")
    monkeypatch.delenv("SC_SMTP_PASSWORD", raising=False)
    assert credentials.get_secret("smtp_password") is None


@pytest.mark.parametrize("value, expected", [("", None), ("XXXXX", None), (None, None), ("abc", "abc")])
def test_placeholders_are_not_passwords(value, expected):
    assert credentials.real(value) == expected


def test_config_takes_the_smtp_password_from_the_local_file(local_file):
    import sc.config as config
    try:
        importlib.reload(config)
        assert config.SMTP_PASSWORD == "from-file"
    finally:
        importlib.reload(config)


def test_email_utility_takes_its_password_from_the_local_file(local_file):
    import sc.utils.utils_email as ue
    try:
        importlib.reload(ue)
        assert ue.GMAIL_APP_PASSWORD == "gmail-from-file"
    finally:
        importlib.reload(ue)


def test_versioned_files_hold_no_passwords():
    for name in ("config.prod.json", "config.test.json"):
        cfg = json.loads((ROOT / "sc" / name).read_text(encoding="utf-8"))
        assert credentials.real(cfg["smtp"].get("password")) is None, name
    source = (ROOT / "sc" / "utils" / "utils_email.py").read_text(encoding="utf-8")
    assert not re.search(r'os\.getenv\("GMAIL_APP_PASSWORD",\s*"[^"]+"\)', source)


LITERAL_PASSWORD = re.compile(r'(?i)\b\w*pass(word)?\w*"?\s*[:=]\s*["\']([^"\']{6,})["\']')


def test_no_literal_password_in_python_or_json_files():
    found = []
    for path in list(ROOT.rglob("*.py")) + list(ROOT.rglob("*.json")):
        rel = path.relative_to(ROOT).as_posix()
        if rel.startswith((".venv/", "venv/")) or rel == "tests/test_credentials.py" or \
                path.name in ("secrets.local.json", "secrets.example.json"):
            continue
        for n, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            m = LITERAL_PASSWORD.search(line)
            value = m.group(2) if m else None
            # environment variable names (SC_SMTP_PASSWORD) are not passwords
            if value and credentials.real(value) is not None and not re.fullmatch(r"[A-Z0-9_]+", value):
                found.append(f"{rel}:{n}")
    assert not found, f"contraseñas literales en: {found}"
