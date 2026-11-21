"""Secrets (WebUntis session cookies, the Parent link secret) live in the macOS Keychain - never in files.

The Keychain item is created by this app, so macOS only lets this (code-signed) app read it silently; any other
program has to ask you first. If the Keychain is unavailable the secret is NOT written anywhere else: you just have to
sign in again. SSC never stores a password at all - you sign in in a browser window.
"""
import hashlib
import sys

import core

CHUNK = 500   # Windows Credential Manager entries are limited to ~1280 characters: bigger secrets are split


class VaultError(Exception):
    pass


def _service():
    # one Keychain namespace per data folder (so tests / a second profile never touch the real items)
    return "com.stundl.ssc:" + hashlib.sha1(str(core.HOME).encode()).hexdigest()[:10]


def _keyring():
    """The OS secret store: macOS Keychain, Windows Credential Manager, or the Linux Secret Service
    (GNOME Keyring / KWallet / KeePassXC). Backends are imported directly (no auto-discovery: works in packaged apps)."""
    try:
        if sys.platform == "darwin":
            from keyring.backends.macOS import Keyring
        elif sys.platform.startswith("win"):
            from keyring.backends.Windows import WinVaultKeyring as Keyring
        else:
            from keyring.backends.SecretService import Keyring
        kr = Keyring()
        if getattr(kr, "priority", 1) <= 0:
            raise VaultError("no secure keyring is available on this system "
                             "(Linux: install and unlock gnome-keyring, KWallet or KeePassXC with Secret Service)")
        return kr
    except VaultError:
        raise
    except Exception as e:
        raise VaultError(f"secret store not available: {e}")


def _put(kr, service, name, value):
    if sys.platform == "darwin" or len(value) <= CHUNK:
        kr.set_password(service, name, value)
        return
    parts = [value[i:i + CHUNK] for i in range(0, len(value), CHUNK)]
    for i, part in enumerate(parts):
        kr.set_password(service, f"{name}#{i}", part)
    kr.set_password(service, name, f"ssc-chunks:{len(parts)}")


def _pull(kr, service, name):
    v = kr.get_password(service, name)
    if v and v.startswith("ssc-chunks:"):
        n = int(v.split(":")[1])
        parts = [kr.get_password(service, f"{name}#{i}") for i in range(n)]
        return None if any(p is None for p in parts) else "".join(parts)
    return v


def _drop(kr, service, name):
    v = None
    try:
        v = kr.get_password(service, name)
    except Exception:
        pass
    if v and v.startswith("ssc-chunks:"):
        for i in range(int(v.split(":")[1])):
            try:
                kr.delete_password(service, f"{name}#{i}")
            except Exception:
                pass
    try:
        kr.delete_password(service, name)
    except Exception:
        pass


def migrate_from(old_home, names=("parent-token", "webuntis-cookies")):
    """Copy secrets stored under the old data-folder path to the current one, then delete the old items."""
    old_service = "com.stundl.ssc:" + hashlib.sha1(str(old_home).encode()).hexdigest()[:10]
    kr = _keyring()
    for n in names:
        v = _pull(kr, old_service, n)
        if v:
            _put(kr, _service(), n, v)
            _drop(kr, old_service, n)


# --- server edition: no desktop keyring on a headless box. SSC_VAULT=file keeps secrets in one file that only the
# service account can read (0600). Anyone with root on the machine can read it - that is the trade-off of a server.
def _file_vault():
    import json, os
    return core.HOME / "secrets.json", json, os


def _file_read():
    f, json, _ = _file_vault()
    try:
        return json.loads(f.read_text())
    except Exception:
        return {}


def _file_write(d):
    f, json, os = _file_vault()
    f.parent.mkdir(parents=True, exist_ok=True)
    tmp = f.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        fh.write(json.dumps(d))
    os.replace(tmp, f)
    f.chmod(0o600)


def _use_file():
    import os
    return os.environ.get("SSC_VAULT") == "file"


def get(name):
    if _use_file():
        return _file_read().get(name)
    try:
        return _pull(_keyring(), _service(), name)
    except Exception:
        return None


def set(name, value):
    if _use_file():
        d = _file_read()
        d[name] = value
        _file_write(d)
        return
    try:
        _drop(_keyring(), _service(), name)   # clear any older, differently-chunked value first
        _put(_keyring(), _service(), name, value)
    except VaultError:
        raise
    except Exception as e:
        raise VaultError(f"Could not save to the secret store: {e}")


def delete(name):
    if _use_file():
        d = _file_read()
        d.pop(name, None)
        _file_write(d)
        return
    try:
        _drop(_keyring(), _service(), name)
    except Exception:
        pass
