"""People with a personal PIN (stored hashed in the panel config file), browser sessions and the single
command lock («Tomar el mando»): only one person at a time can send commands or change the mode."""
import hashlib
import json
import secrets
from datetime import datetime, timedelta
from pathlib import Path


def _hash(salt, pin):
    return hashlib.sha256((salt + str(pin)).encode("utf-8")).hexdigest()


def user_id(name):
    """User name = university e-mail without @udl.cat, lower case («Vitor.DaSilva@udl.cat» -> «vitor.dasilva»)."""
    n = str(name or "").strip().lower()
    return n[:-len("@udl.cat")] if n.endswith("@udl.cat") else n


class People:
    """people: [{name, salt, hash}] inside the panel config JSON (other keys of the file are preserved)."""

    def __init__(self, path):
        self.path = Path(path)

    def _load(self):
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def _save(self, data):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")

    def names(self):
        return [p["name"] for p in self._load().get("people", [])]

    def add(self, name, pin):
        name = user_id(name)
        data = self._load()
        people = [p for p in data.get("people", []) if user_id(p["name"]) != name]
        salt = secrets.token_hex(8)
        people.append({"name": name, "salt": salt, "hash": _hash(salt, pin)})
        data["people"] = people
        self._save(data)

    def remove(self, name):
        name = user_id(name)
        data = self._load()
        data["people"] = [p for p in data.get("people", []) if user_id(p["name"]) != name]
        self._save(data)

    def verify(self, name, pin):
        name = user_id(name)
        for p in self._load().get("people", []):
            if user_id(p["name"]) == name:
                return secrets.compare_digest(p["hash"], _hash(p["salt"], pin))
        return False


class Sessions:
    def __init__(self, ttl=timedelta(hours=12)):
        self.ttl = ttl
        self._s = {}

    def create(self, name, host, now=None):
        now = now or datetime.now()
        token = secrets.token_urlsafe(24)
        self._s[token] = {"name": name, "host": host, "created": now}
        return token

    def get(self, token, now=None):
        now = now or datetime.now()
        s = self._s.get(token)
        if not s:
            return None
        if now - s["created"] > self.ttl:
            self._s.pop(token, None)
            return None
        return {"name": s["name"], "host": s["host"]}

    def drop(self, token):
        self._s.pop(token, None)


class Lock:
    def __init__(self, idle=timedelta(minutes=30)):
        self.idle = idle
        self._h = None

    def holder(self, now=None):
        now = now or datetime.now()
        if self._h and now - self._h["last"] > self.idle:
            self._h = None
        return dict(self._h) if self._h else None

    def take(self, name, host, now=None):
        now = now or datetime.now()
        h = self.holder(now)
        if h and h["name"] != name:
            return False, h["name"]
        self._h = {"name": name, "host": host, "since": (h or {}).get("since", now), "last": now}
        return True, name

    def touch(self, name, now=None):
        now = now or datetime.now()
        if self._h and self._h["name"] == name:
            self._h["last"] = now

    def release(self, name, now=None):
        h = self.holder(now)
        if not h or h["name"] != name:
            return False
        self._h = None
        return True
