"""HTTPS for the web panel: a self-signed certificate made on this machine (browsers warn once, the traffic is encrypted).
Files: /etc/ssc/tls/cert.pem (public) and key.pem (readable by the web service only)."""
import base64
import hashlib
import ipaddress
import json
import re
import shutil
from pathlib import Path

import serverconf

DAYS = 825


def tls_dir(root=None):
    return Path(root or serverconf.system_root()) / "etc/ssc/tls"


def exists(root=None):
    d = tls_dir(root)
    return (d / "cert.pem").exists() and (d / "key.pem").exists()


def fingerprint(pem_text):
    """SHA-256 of the certificate, the way browsers show it (AA:BB:...)."""
    m = re.search(r"-----BEGIN CERTIFICATE-----(.+?)-----END CERTIFICATE-----", pem_text or "", re.S)
    if not m:
        return ""
    h = hashlib.sha256(base64.b64decode("".join(m.group(1).split()))).hexdigest().upper()
    return ":".join(h[i:i + 2] for i in range(0, len(h), 2))


def describe(root=None):
    d = tls_dir(root)
    try:
        meta = json.loads((d / "meta.json").read_text())
    except (OSError, ValueError):
        meta = {}
    try:
        fp = fingerprint((d / "cert.pem").read_text())
    except OSError:
        fp = ""
    return {"present": exists(root), "fingerprint": fp, "hostname": meta.get("hostname", ""), "ips": meta.get("ips", [])}


def generate(hostname, ips, run, root=None, force=False):
    """Make the certificate unless a fitting one exists (same host name and addresses). -> note."""
    d = tls_dir(root)
    ips = sorted({str(ipaddress.IPv4Address(i)) for i in ips})
    if not serverconf.HOSTNAME_RE.match(hostname):
        raise RuntimeError("https: invalid host name")
    want = {"hostname": hostname, "ips": ips}
    try:
        have = json.loads((d / "meta.json").read_text())
    except (OSError, ValueError):
        have = None
    if exists(root) and have == want and not force:
        return "HTTPS certificate is up to date"
    d.mkdir(parents=True, exist_ok=True)
    san = ",".join([f"DNS:{hostname}", f"DNS:{hostname}.local", "DNS:localhost"] + [f"IP:{i}" for i in ips] + ["IP:127.0.0.1"])
    r = run(["openssl", "req", "-x509", "-newkey", "ec", "-pkeyopt", "ec_paramgen_curve:prime256v1", "-nodes", "-days", str(DAYS),
             "-keyout", str(d / "key.pem.new"), "-out", str(d / "cert.pem.new"), "-subj", f"/CN={hostname}/O=SSC Server", "-addext", f"subjectAltName={san}"])
    if r.returncode != 0:
        for n in ("key.pem.new", "cert.pem.new"):
            (d / n).unlink(missing_ok=True)
        raise RuntimeError("https: could not make a certificate: " + ((r.stderr or r.stdout) or "openssl failed").strip()[:200])
    (d / "key.pem.new").chmod(0o640)
    try:
        shutil.chown(d / "key.pem.new", group="ssc")           # the web service (user ssc) must read it, nobody else
    except (LookupError, OSError):
        pass                                                   # no such group (tests): the file stays root-only
    (d / "cert.pem.new").chmod(0o644)
    (d / "key.pem.new").replace(d / "key.pem")
    (d / "cert.pem.new").replace(d / "cert.pem")
    (d / "meta.json").write_text(json.dumps(want))
    return f"HTTPS certificate made for {hostname} ({len(ips)} address(es)), valid {DAYS} days"
