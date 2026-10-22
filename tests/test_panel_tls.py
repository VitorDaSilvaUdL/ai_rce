"""HTTPS of the panel with its own (self-signed) certificate, kept in state/ next to the other private files.

The certificate names the PC (PcCSole in the laboratory), localhost and the PC's addresses. It is reused while it
still names the PC and is not about to expire, so that a certificate installed in the team's browsers keeps working."""
import socket
import ssl
import threading
import time
import urllib.request
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from panel import tls
from panel.config import Config
from tests.test_panel_web import ctx, login  # noqa: F401  (fixture and helper of the panel web tests)
from panel import web

NAMES = ["PcCSole", "localhost"]
IPS = ["127.0.0.1", "10.100.9.144"]


def test_creates_a_certificate_for_the_pc_name_localhost_and_its_addresses(tmp_path):
    info = tls.ensure_certificate(tmp_path, names=NAMES, ips=IPS)
    assert info["created"] is True
    assert (tmp_path / tls.CERT_FILE).exists() and (tmp_path / tls.KEY_FILE).exists()
    assert info["names"] == ["PcCSole", "localhost"] and info["ips"] == ["127.0.0.1", "10.100.9.144"]
    assert info["not_after"] - datetime.now(timezone.utc) > timedelta(days=800)
    assert len(info["sha256"].split(":")) == 32


def test_reuses_the_certificate_while_it_names_the_pc(tmp_path):
    first = tls.ensure_certificate(tmp_path, names=NAMES, ips=IPS)
    again = tls.ensure_certificate(tmp_path, names=NAMES, ips=["127.0.0.1", "10.100.9.200"])   # new DHCP address
    assert again["created"] is False and again["sha256"] == first["sha256"]


def test_renews_when_the_pc_name_changes_or_it_is_about_to_expire(tmp_path):
    first = tls.ensure_certificate(tmp_path, names=NAMES, ips=IPS)
    renamed = tls.ensure_certificate(tmp_path, names=["OtroPC", "localhost"], ips=IPS)
    assert renamed["created"] is True and renamed["sha256"] != first["sha256"]
    later = datetime.now(timezone.utc) + timedelta(days=810)
    old = tls.ensure_certificate(tmp_path, names=["OtroPC", "localhost"], ips=IPS, now=later)
    assert old["created"] is True


def test_renews_a_damaged_certificate(tmp_path):
    tls.ensure_certificate(tmp_path, names=NAMES, ips=IPS)
    (tmp_path / tls.CERT_FILE).write_text("not a certificate", encoding="utf-8")
    assert tls.ensure_certificate(tmp_path, names=NAMES, ips=IPS)["created"] is True


def test_uvicorn_options(tmp_path):
    cfg = Config(tmp_path / "panel_config.json")
    opts = tls.uvicorn_ssl(cfg, tmp_path, names=NAMES, ips=IPS)
    assert opts == {"ssl_certfile": str(tmp_path / tls.CERT_FILE), "ssl_keyfile": str(tmp_path / tls.KEY_FILE)}
    cfg.set(https=False)
    assert tls.uvicorn_ssl(cfg, tmp_path, names=NAMES, ips=IPS) == {}


def test_without_the_cryptography_package_the_panel_still_starts_on_http(tmp_path, monkeypatch):
    def broken(*a, **k):
        raise ImportError("No module named 'cryptography'")
    monkeypatch.setattr(tls, "ensure_certificate", broken)
    assert tls.uvicorn_ssl(Config(tmp_path / "panel_config.json"), tmp_path) == {}


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_a_browser_that_trusts_the_certificate_gets_the_page_over_https(tmp_path):
    """The real handshake: uvicorn with the certificate, a client that trusts it and checks the name."""
    import uvicorn
    from fastapi import FastAPI
    tls.ensure_certificate(tmp_path, names=NAMES, ips=IPS)
    app = FastAPI()

    @app.get("/ping")
    def ping():
        return {"ok": True}
    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning",
                                           ssl_certfile=str(tmp_path / tls.CERT_FILE),
                                           ssl_keyfile=str(tmp_path / tls.KEY_FILE)))
    th = threading.Thread(target=server.run, daemon=True)
    th.start()
    try:
        for _ in range(100):
            if server.started:
                break
            time.sleep(0.05)
        trust = ssl.create_default_context(cafile=str(tmp_path / tls.CERT_FILE))
        for host in ("localhost", "127.0.0.1"):
            with urllib.request.urlopen(f"https://{host}:{port}/ping", context=trust, timeout=5) as r:
                assert r.status == 200
        with pytest.raises(Exception):                       # a client that does not trust it is warned
            urllib.request.urlopen(f"https://localhost:{port}/ping", context=ssl.create_default_context(), timeout=5)
    finally:
        server.should_exit = True
        th.join(timeout=5)


# --------------------------------------------------------------------------------------------- the web page
def test_session_cookie_is_secure_over_https_only(ctx):  # noqa: F811
    https = TestClient(web.create_app(ctx), base_url="https://testserver")
    https.post("/api/setup", json={"name": "rce_admin", "pin": "1234"})
    r = https.post("/api/login", json={"name": "rce_admin", "pin": "1234"})
    assert "secure" in r.headers["set-cookie"].lower()
    plain = TestClient(web.create_app(ctx))
    r = plain.post("/api/login", json={"name": "rce_admin", "pin": "1234"})
    assert "secure" not in r.headers["set-cookie"].lower()


def test_the_certificate_can_be_downloaded_to_install_it(ctx):  # noqa: F811
    client = TestClient(web.create_app(ctx))
    assert client.get("/certificado").status_code == 404           # panel on HTTP: no certificate
    tls.ensure_certificate(ctx.state_dir, names=NAMES, ips=IPS)
    r = client.get("/certificado")
    assert r.status_code == 200 and r.text.startswith("-----BEGIN CERTIFICATE-----")
    assert "panel_rce.crt" in r.headers["content-disposition"]
    assert "PRIVATE KEY" not in r.text


def test_check_tells_how_the_panel_answers_on_this_pc(tmp_path):
    """Used by the installers: 'https' when the panel answers over HTTPS with its own certificate, 'http' when it
    answers unencrypted, None when it does not answer."""
    import uvicorn
    from fastapi import FastAPI
    tls.ensure_certificate(tmp_path, names=NAMES, ips=IPS)
    app = FastAPI()

    @app.get("/api/me")
    def me():
        return {"name": None}
    port = _free_port()
    assert tls.check(port, tmp_path) is None
    for ssl_opts, expected in (({"ssl_certfile": str(tmp_path / tls.CERT_FILE), "ssl_keyfile": str(tmp_path / tls.KEY_FILE)}, "https"),
                               ({}, "http")):
        port = _free_port()
        server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", **ssl_opts))
        th = threading.Thread(target=server.run, daemon=True)
        th.start()
        try:
            for _ in range(100):
                if server.started:
                    break
                time.sleep(0.05)
            assert tls.check(port, tmp_path) == expected
        finally:
            server.should_exit = True
            th.join(timeout=5)
