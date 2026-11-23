"""Automatic updates: once a day at the chosen time, install a newer release (the root helper checks the new version
answers and puts the old one back if it does not). One attempt per version: a release that failed is not retried
every night - an administrator can still install it by hand."""
import json
import time
from datetime import datetime

import serverconf


def marker_path():
    return serverconf.state_dir() / "autoupdate.json"


def _marker():
    try:
        return json.loads(marker_path().read_text())
    except (OSError, ValueError):
        return {}


def last():
    return _marker() or None


def due(cfg, now=None):
    au = cfg["autoupdate"]
    now = now or datetime.now()
    return au["enabled"] and now.strftime("%H:%M") >= au["time"] and _marker().get("day") != now.date().isoformat()


def run(cfg, check, request, now=None):
    """check() -> updater.check() dict; request(parts) asks the root helper. -> short text for the log."""
    now = now or datetime.now()
    m = _marker()
    day = now.date().isoformat()
    try:
        info = check()
    except Exception as e:                                         # no internet today: try again tomorrow, do not hammer GitHub
        _save({"day": day, "target": m.get("target"), "result": "check failed: " + str(e)[:100]})
        return "update check failed"
    if not info.get("update_available"):
        _save({"day": day, "target": m.get("target"), "result": "up to date"})
        return "up to date"
    if m.get("target") == info["latest"]:
        _save({"day": day, "target": info["latest"], "result": "not retried: an earlier attempt for " + info["latest"] + " did not finish"})
        return "not retried"
    _save({"day": day, "target": info["latest"], "result": "installing " + info["latest"], "at": time.time()})
    request(["update"])
    return "installing " + info["latest"]


def _save(m):
    p = marker_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(m))
