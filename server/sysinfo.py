"""Read-only information about the machine for the admin panel: health, services, logs.

Files are read through a root prefix, so the tests can fake /proc and /sys. Nothing here changes anything.
"""
import os
import platform
import shutil
import subprocess
import time
from pathlib import Path

SERVICES = [("ssc-server", "SSC web service"), ("ssh", "SSH"), ("nftables", "Firewall"), ("ssc-sync.timer", "Data sync timer"),
            ("ssc-apply.path", "Settings helper"), ("ssc-sleep.timer", "Sleep schedule: switch off"), ("ssc-wake.timer", "Sleep schedule: switch on"),
            ("ssc-health.timer", "Disk health check"), ("getty@tty1", "Console login (screen)")]
RESTARTABLE = ("ssc-server", "ssh", "nftables")

LOG_UNITS = {"web": ["-u", "ssc-server"], "apply": ["-u", "ssc-apply"], "sync": ["-u", "ssc-sync"], "ssh": ["-u", "ssh"], "firewall": ["-u", "nftables"],
             "network": ["-u", "networking"], "schedule": ["-u", "ssc-sleep", "-u", "ssc-wake", "-u", "ssc-schedule-boot"], "boot": ["-b", "-p", "warning"]}


def _read(root, rel):
    try:
        return (Path(root) / rel).read_text(errors="replace")
    except OSError:
        return ""


def parse_os_release(text):
    for line in str(text).splitlines():
        if line.startswith("PRETTY_NAME="):
            return line.split("=", 1)[1].strip().strip('"')
    return ""


def parse_uptime(text):
    try:
        return float(str(text).split()[0])
    except (ValueError, IndexError):
        return None


def parse_meminfo(text):
    out = {}
    for line in str(text).splitlines():
        k, _, v = line.partition(":")
        if k in ("MemTotal", "MemAvailable", "SwapTotal", "SwapFree"):
            try:
                out[k] = int(v.split()[0])
            except (ValueError, IndexError):
                pass
    if "MemTotal" not in out:
        return None
    avail = out.get("MemAvailable", 0)
    return {"total_mb": out["MemTotal"] // 1024, "used_mb": (out["MemTotal"] - avail) // 1024, "percent": round(100 * (out["MemTotal"] - avail) / out["MemTotal"])}


def read_thermal(root):
    out = []
    for z in sorted((Path(root) / "sys/class/thermal").glob("thermal_zone*")):
        try:
            c = int((z / "temp").read_text().strip()) / 1000
            out.append({"name": (z / "type").read_text().strip() if (z / "type").exists() else z.name, "celsius": round(c, 1)})
        except (OSError, ValueError):
            continue
    return out


def read_battery(root):
    """A laptop used as a server: is there a battery, how full is it, is the charger connected."""
    ps = Path(root) / "sys/class/power_supply"
    bat, ac = None, None
    try:
        for d in sorted(ps.iterdir()):
            kind = (d / "type").read_text().strip() if (d / "type").exists() else ""
            if kind == "Battery" and bat is None:
                bat = {"percent": int((d / "capacity").read_text().strip()), "status": (d / "status").read_text().strip() if (d / "status").exists() else ""}
            elif kind == "Mains":
                ac = (d / "online").read_text().strip() == "1"
    except (OSError, ValueError):
        pass
    return None if bat is None else dict(bat, ac_online=ac)


def _run(cmd):
    return subprocess.run(cmd, capture_output=True, text=True, timeout=15)


def services_state(run=_run):
    names = [n for n, _ in SERVICES]
    try:
        out = run(["systemctl", "is-active", *[n if "." in n or "@" in n else n + ".service" for n in names]]).stdout.split()
    except (OSError, subprocess.SubprocessError):
        out = []
    return [{"name": n, "label": label, "state": out[i] if i < len(out) else "unknown", "restartable": n in RESTARTABLE} for i, (n, label) in enumerate(SERVICES)]


def info(root="/", run=_run, now=None):
    root = Path(root)
    try:
        du = shutil.disk_usage(root if root.exists() else "/")
        disk = {"total_gb": round(du.total / 1e9, 1), "used_gb": round((du.total - du.free) / 1e9, 1), "percent": round(100 * (du.total - du.free) / du.total)}
    except OSError:
        disk = None
    try:
        load = [round(x, 2) for x in os.getloadavg()]
    except (OSError, AttributeError):
        load = None
    return {"os": parse_os_release(_read(root, "etc/os-release")) or platform.system(), "kernel": platform.release(), "machine": platform.machine(),
            "uptime_s": parse_uptime(_read(root, "proc/uptime")), "load": load, "cpus": os.cpu_count(), "memory": parse_meminfo(_read(root, "proc/meminfo")),
            "disk": disk, "temperatures": read_thermal(root), "battery": read_battery(root), "services": services_state(run),
            "time": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now)) if now else time.strftime("%Y-%m-%d %H:%M:%S"),
            "timezone": (_read(root, "etc/timezone").strip() or time.tzname[0])}


def journal(key, lines=100, run=_run):
    """The last lines of one allow-listed log. -> text (never raises)."""
    if key not in LOG_UNITS:
        raise ValueError("unknown log")
    n = max(10, min(500, int(lines)))
    try:
        r = run(["journalctl", *LOG_UNITS[key], "-n", str(n), "--no-pager", "-o", "short-iso"])
    except (OSError, subprocess.SubprocessError) as e:
        return f"(can't read the log: {e})"
    if r.returncode != 0:
        return "(can't read the log: " + ((r.stderr or r.stdout).strip()[:200] or "no access to the journal") + ")"
    return r.stdout[-60000:] or "(nothing logged)"
