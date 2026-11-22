"""Sleep schedule for SSC Server: switch everything off at one time of day and back on at another.

  quiet     stop the SSC services (web, Parent links, sync; SSH optionally), spin the hard disks down and blank the console;
            at the switch-on time everything starts again. The machine itself keeps running.
  suspend   suspend to RAM and wake up by itself at the switch-on time (needs a hardware clock alarm: rtcwake)
  poweroff  power off and let the hardware clock switch the machine on again (needs BIOS/UEFI "wake on RTC alarm")

Pure planning functions (tested) + thin runners. Times are the machine's local time (set in the installer).
"""
import json
import subprocess
import time
from datetime import datetime, time as dtime, timedelta
from pathlib import Path

import serverconf

DAYS = serverconf.DAYS
CAL = {"mon": "Mon", "tue": "Tue", "wed": "Wed", "thu": "Thu", "fri": "Fri", "sat": "Sat", "sun": "Sun"}
UNITS = ("ssc-sleep.timer", "ssc-sleep.service", "ssc-wake.timer", "ssc-wake.service")
CMD = "/usr/bin/python3 /opt/ssc/server/ssconfig.py"


def _t(hhmm):
    h, m = str(hhmm).split(":")
    return dtime(int(h), int(m))


def overnight(sc):
    return _t(sc["on"]) <= _t(sc["off"])


def in_window(now, sc):
    """Is `now` between a switch-off on a scheduled day and the following switch-on?"""
    if not sc["enabled"]:
        return False
    for back in (0, 1):
        d = (now - timedelta(days=back)).date()
        if DAYS[d.weekday()] not in sc["days"]:
            continue
        start, end = datetime.combine(d, _t(sc["off"])), datetime.combine(d, _t(sc["on"]))
        if end <= start:
            end += timedelta(days=1)
        if start <= now < end:
            return True
    return False


def next_wake(now, sc):
    """The next switch-on time after `now` (what the hardware clock is set to when the machine goes to sleep)."""
    t = datetime.combine(now.date(), _t(sc["on"]))
    return t if t > now else t + timedelta(days=1)


def render_units(sc):
    """systemd timer + service files from fixed templates (validated values only). The wake timer exists only for 'quiet':
    suspend/poweroff wake the whole machine through the hardware clock."""
    sleep_days = ",".join(CAL[d] for d in sc["days"])
    wake_days = ",".join(CAL[DAYS[(DAYS.index(d) + (1 if overnight(sc) else 0)) % 7]] for d in sc["days"])
    head = "# Managed by ssconfig - changes are overwritten\n"
    units = {
        "ssc-sleep.timer": head + f"[Unit]\nDescription=SSC Server: start the sleep period\n\n[Timer]\nOnCalendar={sleep_days} {sc['off']}:00\nPersistent=false\n\n[Install]\nWantedBy=timers.target\n",
        "ssc-sleep.service": head + f"[Unit]\nDescription=SSC Server sleep period ({sc['mode']})\n\n[Service]\nType=oneshot\nTimeoutStartSec=infinity\nEnvironment=SSC_DATA_DIR=/var/lib/ssc SSC_VAULT=file\nExecStart={CMD} schedule down\n",
    }
    if sc["mode"] == "quiet":
        units["ssc-wake.timer"] = head + f"[Unit]\nDescription=SSC Server: end the sleep period\n\n[Timer]\nOnCalendar={wake_days} {sc['on']}:00\nPersistent=false\n\n[Install]\nWantedBy=timers.target\n"
        units["ssc-wake.service"] = head + f"[Unit]\nDescription=SSC Server wake-up\n\n[Service]\nType=oneshot\nEnvironment=SSC_DATA_DIR=/var/lib/ssc SSC_VAULT=file\nExecStart={CMD} schedule up\n"
    return units


def parse_rotational_disks(lsblk_text):
    """`lsblk -dn -o NAME,ROTA,TYPE` -> ['/dev/sda', ...] for spinning disks only (SSDs and NVMe have nothing to spin down)."""
    out = []
    for line in str(lsblk_text).splitlines():
        p = line.split()
        if len(p) >= 3 and p[1] == "1" and p[2] == "disk" and p[0].replace("-", "").isalnum() and not p[0].startswith(("loop", "sr", "ram", "zram")):
            out.append("/dev/" + p[0])
    return out


def services(sc):
    """What 'everything' means in quiet mode."""
    return ["ssc-sync.timer", "ssc-server.service"] + (["ssh.service"] if sc["stop_ssh"] else [])


def plan_down(sc, now, disks=()):
    """-> list of commands (argument lists) to run, in order. ('screen', 'off') entries are handled by the runner."""
    cmds = []
    if sc["mode"] == "quiet":
        cmds += [["systemctl", "stop", s] for s in services(sc)]
        cmds.append(["sync"])
        if sc["spin_down_disks"]:
            cmds += [["hdparm", "-y", d] for d in disks]
        if sc["screen_off"]:
            cmds.append(["screen", "off"])
    else:
        cmds.append(["sync"])
        wake = int(next_wake(now, sc).timestamp())
        cmds.append(["rtcwake", "-m", "mem" if sc["mode"] == "suspend" else "off", "-t", str(wake)])
    return cmds


def plan_up(sc):
    cmds = [["systemctl", "start", s] for s in reversed(services(sc))]
    if sc["screen_off"]:
        cmds.append(["screen", "on"])
    return cmds


def _screen(on):
    try:
        with open("/dev/tty1", "w") as tty, open("/dev/tty1") as tin:
            subprocess.run(["setterm", "--term", "linux", "--blank", "poke" if on else "force"], stdin=tin, stdout=tty, timeout=10)
    except (OSError, subprocess.SubprocessError):
        pass                                           # best effort: no console, no problem


def disks():
    try:
        return parse_rotational_disks(subprocess.run(["lsblk", "-dn", "-o", "NAME,ROTA,TYPE"], capture_output=True, text=True, timeout=10).stdout)
    except (OSError, subprocess.SubprocessError):
        return []


def _exec(cmds, run):
    notes = []
    for c in cmds:
        if c[0] == "screen":
            _screen(c[1] == "on")
            continue
        r = run(c)
        if getattr(r, "returncode", 0) != 0 and c[0] not in ("hdparm", "systemctl"):      # a disk that can't spin down is not an error
            notes.append(f"{c[0]} failed: {(getattr(r, 'stderr', '') or '').strip()[:120]}")
    return notes


def _run(cmd):
    return subprocess.run(cmd, capture_output=True, text=True, timeout=3600)


def run_down(cfg, run=_run, now=None):
    sc = cfg["schedule"]
    now = now or datetime.now()
    return _exec(plan_down(sc, now, disks() if sc["mode"] == "quiet" and sc["spin_down_disks"] else ()), run)


def run_up(cfg, run=_run):
    return _exec(plan_up(cfg["schedule"]), run)


def check_at_boot(cfg, run=_run, now=None):
    """After a power cut or a manual start inside the sleep window the services came up: put them back to sleep ('quiet' only -
    suspend/poweroff machines were switched on on purpose)."""
    sc = cfg["schedule"]
    if sc["enabled"] and sc["mode"] == "quiet" and in_window(now or datetime.now(), sc):
        return run_down(cfg, run, now)
    return None


def has_rtc(root=None):
    d = Path(root or "/") / "sys/class/rtc"
    try:
        return any(d.iterdir())
    except OSError:
        return False


def describe(cfg, now=None):
    sc = cfg["schedule"]
    if not sc["enabled"]:
        return "Schedule: off"
    now = now or datetime.now()
    days = ",".join(sc["days"])
    return (f"Schedule: on - {sc['mode']}: off at {sc['off']}, on at {sc['on']} ({days}); "
            f"{'inside the sleep period now' if in_window(now, sc) else 'awake now'}")
