"""Secrets (WebUntis session cookies, the Parent link secret) live in the macOS Keychain - never in files.

The Keychain item is created by this app, so macOS only lets this (code-signed) app read it silently; any other
program has to ask you first. If the Keychain is unavailable the secret is NOT written anywhere else: you just have to
sign in again. SSC never stores a password at all - you sign in in a browser window.
"""
import hashlib

import core


class VaultError(Exception):
    pass


def _service():
    # one Keychain namespace per data folder (so tests / a second profile never touch the real items)
    return "com.stundl.ssc:" + hashlib.sha1(str(core.HOME).encode()).hexdigest()[:10]


def _keyring():
    try:
        from keyring.backends.macOS import Keyring  # used directly: no backend auto-discovery needed (works in the packaged app)
        return Keyring()
    except Exception as e:
        raise VaultError(f"Keychain not available: {e}")


def migrate_from(old_home, names=("parent-token", "webuntis-cookies")):
    """Copy secrets stored under the old data-folder path to the current one, then delete the old items."""
    old_service = "com.stundl.ssc:" + hashlib.sha1(str(old_home).encode()).hexdigest()[:10]
    kr = _keyring()
    for n in names:
        v = kr.get_password(old_service, n)
        if v:
            kr.set_password(_service(), n, v)
            try:
                kr.delete_password(old_service, n)
            except Exception:
                pass


def get(name):
    try:
        return _keyring().get_password(_service(), name)
    except VaultError:
        return None
    except Exception:
        return None


def set(name, value):
    try:
        _keyring().set_password(_service(), name, value)
    except VaultError:
        raise
    except Exception as e:
        raise VaultError(f"Could not save to the Keychain: {e}")


def delete(name):
    try:
        _keyring().delete_password(_service(), name)
    except Exception:
        pass
