"""Passwords are never stored in versioned files.

get_secret(name) looks, in this order, for:
  1. an environment variable (SECRET_ENV below), e.g. SC_SMTP_PASSWORD;
  2. sc/secrets.local.json, a local file ignored by git (template: sc/secrets.example.json).
Returns None when the secret is not available (the feature that needs it stays off).
"""
import json
import os
from pathlib import Path

SECRETS_FILE = Path(__file__).resolve().parent / "secrets.local.json"

SECRET_ENV = {
    "smtp_password": "SC_SMTP_PASSWORD",        # alert e-mails (sc/logger.py)
    "gmail_app_password": "GMAIL_APP_PASSWORD",  # sc/utils/utils_email.py
}

PLACEHOLDERS = {"", "XXXXX"}


def real(value):
    """The value, or None if it is empty or a placeholder left in a versioned file."""
    return None if value is None or str(value).strip() in PLACEHOLDERS else value


def get_secret(name):
    value = real(os.getenv(SECRET_ENV.get(name, name.upper()), ""))
    if value:
        return value
    if SECRETS_FILE.exists():
        with SECRETS_FILE.open(encoding="utf-8") as f:
            return real(json.load(f).get(name))
    return None
