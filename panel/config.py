"""Panel configuration in state/panel_config.json (not versioned). The same file holds the people list (panel.auth)."""
import json
from pathlib import Path

DEFAULTS = {
    "bind": "0.0.0.0", "port": 8050, "autostart": True, "exit_state": "automatic", "refresh_s": 10,
    "api_url": "http://localhost:8000", "readings_hours": 6, "plc_read_allowed": False,
}
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
        data = {k: v for k, v in self._load().items() if k != "people"}
        return {**DEFAULTS, **data}
