"""Health alerts: disk, memory, temperature, battery, failing services, SMART disk status, stale data.

The checks are pure functions over what sysinfo.info() reports plus a SMART scan that a root timer writes
(smartctl needs root). Alerts have stable ids so the mail goes out once when something starts, not every hour."""
import json
import re
import socket
import subprocess
import threading
import time

import serverconf


def scan_path():
    return serverconf.state_dir() / "health.json"


def _run(cmd):
    return subprocess.run(cmd, capture_output=True, text=True, timeout=60)


def parse_smart_health(out):
    """Output of `smartctl -H`: True healthy, False failing, None unknown."""
    m = re.search(r"(overall-health[^:]*|SMART Health Status):\s*(\S+)", out or "")
    if not m:
        return None
    return m.group(2).upper() in ("PASSED", "OK")


def scan(run=_run, now=time.time):
    """Root only. -> {'at':..., 'disks':[{'dev','ok','model'}]}. Without smartctl or on virtual disks: ok=None."""
    disks = []
    try:
        names = [l.split() for l in run(["lsblk", "-dn", "-o", "NAME,TYPE,MODEL"]).stdout.splitlines()]
    except (OSError, subprocess.SubprocessError):
        names = []
    for f in names:
        if len(f) < 2 or f[1] != "disk" or not re.fullmatch(r"[a-z0-9]+", f[0]) or f[0].startswith(("loop", "ram", "zram", "sr")):
            continue
        ok = None
        try:
            ok = parse_smart_health(run(["smartctl", "-H", "/dev/" + f[0]]).stdout)
        except (OSError, subprocess.SubprocessError):
            pass
        disks.append({"dev": "/dev/" + f[0], "ok": ok, "model": " ".join(f[2:])[:60]})
    return {"at": now(), "disks": disks}


def write_scan(data=None, run=_run):
    data = data or scan(run)
    p = scan_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data))
    p.chmod(0o644)
    return data


def read_scan():
    try:
        return json.loads(scan_path().read_text())
    except (OSError, ValueError):
        return None


_DNS = {"t": 0.0, "ok": None}


def dns_ok(host="github.com", resolve=socket.getaddrinfo, now=time.time, cache=300):
    """Can this machine turn a name into an address? Without it nothing works: no WebUntis data, no updates, no mail. Cached for 5 minutes."""
    if _DNS["ok"] is not None and now() - _DNS["t"] < cache:
        return _DNS["ok"]
    res = {"ok": False}

    def work():
        try:
            res["ok"] = bool(resolve(host, 443))
        except (OSError, UnicodeError):
            res["ok"] = False
    th = threading.Thread(target=work, daemon=True)
    th.start()
    th.join(4)                                     # a resolver that hangs counts as broken
    _DNS.update(t=now(), ok=res["ok"])
    return res["ok"]


def alerts(info, scan_data, h, synced_at=0, sync_minutes=30, has_key=False, now=None, dns_ok=None):
    """-> [{'id','level','text'}] (level 'warn' or 'crit'). `h` = the health section of the settings."""
    now = now or time.time()
    out = []
    if not h.get("enabled", True):
        return out
    d = (info or {}).get("disk")
    if d:
        if d["percent"] >= h["disk_crit"]:
            out.append({"id": "disk", "level": "crit", "text": f"The disk is {d['percent']}% full ({d['used_gb']} of {d['total_gb']} GB)."})
        elif d["percent"] >= h["disk_warn"]:
            out.append({"id": "disk", "level": "warn", "text": f"The disk is filling up: {d['percent']}% used."})
    mem = (info or {}).get("memory")
    if mem and mem["percent"] >= 95:
        out.append({"id": "mem", "level": "warn", "text": f"Memory is almost full ({mem['percent']}%)."})
    temps = [t["celsius"] for t in (info or {}).get("temperatures") or []]
    if temps and max(temps) >= h["temp_warn"]:
        hot = max(temps)
        out.append({"id": "temp", "level": "crit" if hot >= h["temp_warn"] + 10 else "warn", "text": f"The machine is hot: {hot:g} °C."})
    b = (info or {}).get("battery")
    if b:
        if b.get("ac_online") is False and b["percent"] <= h["battery_warn"]:
            out.append({"id": "battery", "level": "crit", "text": f"Battery at {b['percent']}% and the charger is not connected."})
        elif b.get("ac_online") is False:
            out.append({"id": "ac", "level": "warn", "text": f"Running on battery ({b['percent']}%): the charger is not connected (power cut?)."})
    for s in (info or {}).get("services") or []:
        if s["state"] == "failed":
            out.append({"id": "svc:" + s["name"], "level": "crit", "text": f"The service '{s['label']}' has failed."})
    for dk in (scan_data or {}).get("disks") or []:
        if dk["ok"] is False:
            out.append({"id": "smart:" + dk["dev"], "level": "crit", "text": f"The disk {dk['dev']} {dk['model']} reports that it is failing (SMART). Back up and replace it."})
    if dns_ok is False:
        out.append({"id": "dns", "level": "crit", "text": "This server can't look up names (DNS), so it cannot reach WebUntis, GitHub or the mail server - "
                                                          "no timetable, homework or updates will arrive. Check Network → DNS servers (with a fixed IP address) or the router."})
    uptime = (info or {}).get("uptime_s")
    if has_key and not synced_at and uptime and uptime > 3600:
        out.append({"id": "nosync", "level": "warn", "text": "A WebUntis key is set, but no data has ever been fetched. See WebUntis → Sync now for the reason."})
    if has_key and synced_at and now - synced_at > max(3 * sync_minutes * 60, 3 * 3600):
        out.append({"id": "stale", "level": "warn", "text": "No new school data for a long time: the WebUntis key may have stopped working, or the server has no internet."})
    return out


def state_path():
    return serverconf.state_dir() / "alerts.json"


def new_alerts(current, now=None):
    """Remember which alerts are active; return the ones that just appeared (so mail goes out once). Resolved ones are forgotten."""
    now = now or time.time()
    try:
        active = json.loads(state_path().read_text())
    except (OSError, ValueError):
        active = {}
    fresh = [a for a in current if a["id"] not in active]
    active = {a["id"]: active.get(a["id"], now) for a in current}
    p = state_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(active))
    return fresh
