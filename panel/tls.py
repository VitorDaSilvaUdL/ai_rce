"""HTTPS of the panel with its own certificate (self-signed).

Without HTTPS the user names, passwords and the REAL mode password travel unencrypted over the laboratory network.
The panel creates its certificate in state/ (panel_cert.pem and panel_key.pem, never versioned, the key never shown)
for the name of the PC, localhost and the PC's IPv4 addresses, valid 825 days. It is reused while it still names the
PC and has more than 30 days left: a new DHCP address does not renew it, so a certificate that the team has installed
in its browsers keeps working. Browsers warn the first time (nobody vouches for a self-signed certificate); the
certificate can be downloaded from /certificado and installed, and its SHA-256 fingerprint is printed by

    python -m panel.tls            (also creates it if missing)
    python -m panel.tls --check 8050   (installers: exit 0 HTTPS, 2 plain HTTP, 1 no answer)

panel_config.json "https": false serves plain HTTP again.
"""
import ipaddress
import logging
import os
import socket
import ssl
import sys
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

CERT_FILE = "panel_cert.pem"
KEY_FILE = "panel_key.pem"
VALID_DAYS = 825                    # longest validity some browsers accept for a server certificate
RENEW_DAYS = 30

log = logging.getLogger(__name__)


def pc_names_and_ips():
    """Name of this PC (and its full name if different), localhost, and its IPv4 addresses (not link-local)."""
    names = []
    for n in (socket.gethostname(), socket.getfqdn(), "localhost"):
        if n and n.lower() not in [x.lower() for x in names]:
            names.append(n)
    ips = ["127.0.0.1"]
    try:
        for ip in socket.gethostbyname_ex(socket.gethostname())[2]:
            if ip not in ips and not ip.startswith("169.254."):
                ips.append(ip)
    except OSError:
        pass
    return names, ips


def _read(cert_path):
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes
    cert = x509.load_pem_x509_certificate(Path(cert_path).read_bytes())
    san = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    return {"names": san.get_values_for_type(x509.DNSName),
            "ips": [str(i) for i in san.get_values_for_type(x509.IPAddress)],
            "not_after": cert.not_valid_after_utc,
            "sha256": ":".join(f"{b:02X}" for b in cert.fingerprint(hashes.SHA256())),
            "public_key": cert.public_key()}


def _key_matches(key_path, public_key):
    from cryptography.hazmat.primitives import serialization
    key = serialization.load_pem_private_key(Path(key_path).read_bytes(), password=None)
    der = serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
    return key.public_key().public_bytes(*der) == public_key.public_bytes(*der)


def _write_private(path, data):
    tmp = Path(str(path) + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def _create(cert_path, key_path, names, ips, now):
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, names[0]),
                         x509.NameAttribute(NameOID.ORGANIZATION_NAME, "Panel RCE")])
    san = [x509.DNSName(n) for n in names] + [x509.IPAddress(ipaddress.ip_address(i)) for i in ips]
    cert = (x509.CertificateBuilder().subject_name(subject).issuer_name(subject).public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(minutes=5)).not_valid_after(now + timedelta(days=VALID_DAYS))
            .add_extension(x509.SubjectAlternativeName(san), critical=False)
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
            .add_extension(x509.KeyUsage(digital_signature=True, key_encipherment=True, content_commitment=False,
                                         data_encipherment=False, key_agreement=False, key_cert_sign=False,
                                         crl_sign=False, encipher_only=False, decipher_only=False), critical=True)
            .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
            .sign(key, hashes.SHA256()))
    _write_private(key_path, key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                               serialization.NoEncryption()))
    _write_private(cert_path, cert.public_bytes(serialization.Encoding.PEM))


def ensure_certificate(state_dir, names=None, ips=None, now=None):
    """The certificate of the panel in state_dir, created or renewed if needed. Returns its description:
    cert, key (paths), names, ips, not_after, sha256 and created (True if made now)."""
    state_dir = Path(state_dir)
    if names is None or ips is None:
        n, i = pc_names_and_ips()
        names, ips = names or n, ips or i
    now = now or datetime.now(timezone.utc)
    cert_path, key_path = state_dir / CERT_FILE, state_dir / KEY_FILE
    created = False
    try:
        info = _read(cert_path)
        covered = {n.lower() for n in info["names"]}
        reuse = (names[0].lower() in covered and info["not_after"] - now > timedelta(days=RENEW_DAYS)
                 and _key_matches(key_path, info["public_key"]))
    except Exception:                           # missing, damaged, or a key that is not its own
        reuse = False
    if not reuse:
        state_dir.mkdir(parents=True, exist_ok=True)
        _create(cert_path, key_path, list(names), list(ips), now)
        info, created = _read(cert_path), True
    info.pop("public_key", None)
    return {"cert": str(cert_path), "key": str(key_path), "created": created, **info}


def uvicorn_ssl(config, state_dir, names=None, ips=None):
    """Keyword arguments for uvicorn.run(): the certificate if "https" is on (default), nothing for plain HTTP.
    If the certificate cannot be made (e.g. the cryptography package is missing) the panel still starts, on HTTP."""
    if not config.get("https", True):
        return {}
    try:
        info = ensure_certificate(state_dir, names=names, ips=ips)
    except Exception as e:
        log.error("Sin HTTPS: no se ha podido preparar el certificado del panel (%s). El panel arranca en HTTP.", e)
        print(f"AVISO: panel sin HTTPS ({e}); arranca en HTTP", file=sys.stderr)
        return {}
    log.info("HTTPS con certificado propio %s (huella SHA-256 %s)", info["cert"], info["sha256"])
    return {"ssl_certfile": info["cert"], "ssl_keyfile": info["key"]}


def check(port, state_dir, timeout=5):
    """How the panel answers /api/me on this PC: "https" (its own certificate, checked), "http" (unencrypted) or
    None (no answer)."""
    cert = Path(state_dir) / CERT_FILE
    if cert.exists():
        try:
            trust = ssl.create_default_context(cafile=str(cert))
            with urllib.request.urlopen(f"https://127.0.0.1:{port}/api/me", context=trust, timeout=timeout) as r:
                if r.status == 200:
                    return "https"
        except Exception:
            pass
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/me", timeout=timeout) as r:
            if r.status == 200:
                return "http"
    except Exception:
        pass
    return None


def main(argv=None):
    from sc import state_io
    argv = sys.argv[1:] if argv is None else argv
    state_dir = Path(os.getenv("PANEL_WORK_DIR")) / "state" if os.getenv("PANEL_WORK_DIR") else state_io.STATE_DIR
    if argv[:1] == ["--check"]:
        how = check(int(argv[1]) if len(argv) > 1 else 8050, state_dir)
        print(how or "sin respuesta")
        return {"https": 0, "http": 2}.get(how, 1)
    info = ensure_certificate(state_dir)
    print(f"Certificado del panel: {info['cert']} ({'creado ahora' if info['created'] else 'ya existía'})")
    print(f"Nombres:  {', '.join(info['names'])}")
    print(f"IP:       {', '.join(info['ips'])}")
    print(f"Caduca:   {info['not_after']:%Y-%m-%d}")
    print(f"Huella SHA-256: {info['sha256']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
