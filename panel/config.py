"""Panel configuration in state/panel_config.json (not versioned). The same file holds the people list (panel.auth)
and the REAL mode password (hashed)."""
import hashlib
import json
import secrets
from pathlib import Path

DEFAULTS = {
    "bind": "0.0.0.0", "port": 8050, "autostart": True, "exit_state": "automatic", "refresh_s": 10,
    "api_url": "http://localhost:8000", "readings_hours": 6, "plc_read_allowed": False,
}
PRIVATE_KEYS = ("people", "real_password")
PBKDF2_ROUNDS = 200_000


def _pw_hash(salt, password):
    return hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt), PBKDF2_ROUNDS).hex()
EXIT_STATES = {"automatic": "Automático (manda el programa del PLC)", "parada": "Parada (bombas paradas, tapa cerrada)",
               "as_is": "Como esté (no se toca nada)"}


class Config:
    def __init__(self, path):
        self.path = Path(path)

    def _load(self):
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def get(self, key, default=None):
        return self._load().get(key, DEFAULTS.get(key, default))

    def set(self, **fields):
        data = self._load()
        data.update(fields)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")

    def all(self):
        data = {k: v for k, v in self._load().items() if k not in PRIVATE_KEYS}
        return {**DEFAULTS, **data}

    # --- REAL mode password: set from the lab PC, stored as salt + PBKDF2 hash, never returned
    def real_password_set(self):
        pw = self._load().get("real_password")
        return bool(pw and pw.get("salt") and pw.get("hash"))

    def set_real_password(self, password):
        salt = secrets.token_hex(16)
        self.set(real_password={"salt": salt, "hash": _pw_hash(salt, password)})

    def verify_real_password(self, password):
        pw = self._load().get("real_password")
        if not (pw and pw.get("salt") and pw.get("hash")):
            return False
        return secrets.compare_digest(pw["hash"], _pw_hash(pw["salt"], password or ""))
