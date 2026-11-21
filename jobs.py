"""Background jobs on every platform: daily jobs (sync, reminders) and always-on services (Parent mode, tray icon).

  macOS    launchd agents            (~/Library/LaunchAgents)
  Linux    systemd user units/timers (~/.config/systemd/user); the tray icon uses an XDG autostart entry
  Windows  Task Scheduler            (schtasks)

The generator functions are pure (they only return text / argument lists) so they can be tested on any machine.
"""
import os
import plistlib
import subprocess
import sys
from pathlib import Path

import core

PREFIX = "com.stundl.ssc"


def platform():
    return "mac" if sys.platform == "darwin" else "win" if sys.platform.startswith("win") else "linux"


# ---------------------------------------------------------------- macOS: launchd
def launchd_plist(name, args, daily_at=None, service=False):
    d = dict(Label=f"{PREFIX}.{name}", ProgramArguments=core.self_cmd(*args), WorkingDirectory=str(core.HOME),
             StandardOutPath=str(core.HOME / f"{name}.log"), StandardErrorPath=str(core.HOME / f"{name}.log"))
    if daily_at:
        d["StartCalendarInterval"] = dict(Hour=daily_at[0], Minute=daily_at[1])
    if service:
        d.update(RunAtLoad=True, KeepAlive=dict(SuccessfulExit=False), LimitLoadToSessionType="Aqua", ProcessType="Interactive")
    return d


def _mac_plist_path(name):
    return Path.home() / "Library" / "LaunchAgents" / f"{PREFIX}.{name}.plist"


def _uid():
    return str(os.getuid())


def _mac_install(name, args, daily_at, service):
    pp = _mac_plist_path(name)
    pp.parent.mkdir(parents=True, exist_ok=True)
    core.HOME.mkdir(parents=True, exist_ok=True)
    pp.write_bytes(plistlib.dumps(launchd_plist(name, args, daily_at, service)))
    subprocess.run(["launchctl", "bootout", f"gui/{_uid()}/{PREFIX}.{name}"], capture_output=True)
    r = subprocess.run(["launchctl", "bootstrap", f"gui/{_uid()}", str(pp)], capture_output=True, text=True)
    return r.returncode == 0, r.stderr.strip()


def _mac_remove(name):
    subprocess.run(["launchctl", "bootout", f"gui/{_uid()}/{PREFIX}.{name}"], capture_output=True)
    _mac_plist_path(name).unlink(missing_ok=True)


# ---------------------------------------------------------------- Linux: systemd --user (+ XDG autostart for the tray)
def _q(a):
    return '"' + str(a).replace("\\", "\\\\").replace('"', '\\"') + '"'


def systemd_units(name, args, daily_at=None, service=False):
    """-> {filename: content}. A daily job is a oneshot service + timer; a service restarts on failure."""
    exec_line = " ".join(_q(a) for a in core.self_cmd(*args))
    unit = f"ssc-{name}"
    if service:
        return {f"{unit}.service": f"[Unit]\nDescription=SSC {name}\n\n[Service]\nExecStart={exec_line}\nRestart=on-failure\nRestartSec=10\n\n"
                                   f"[Install]\nWantedBy=default.target\n"}
    h, m = daily_at
    return {f"{unit}.service": f"[Unit]\nDescription=SSC {name}\n\n[Service]\nType=oneshot\nExecStart={exec_line}\n",
            f"{unit}.timer": f"[Unit]\nDescription=SSC {name} (daily)\n\n[Timer]\nOnCalendar=*-*-* {h:02d}:{m:02d}:00\nPersistent=true\n\n"
                            f"[Install]\nWantedBy=timers.target\n"}


def autostart_desktop(args):
    exec_line = " ".join(_q(a) for a in core.self_cmd(*args))
    return f"[Desktop Entry]\nType=Application\nName=SSC\nComment=Stundl Student Client tray icon\nExec={exec_line}\nX-GNOME-Autostart-enabled=true\n"


def _linux_dirs():
    cfg = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    return cfg / "systemd" / "user", cfg / "autostart"


def _systemctl(*a):
    return subprocess.run(["systemctl", "--user", *a], capture_output=True, text=True)


def _linux_install(name, args, daily_at, service):
    sysd, auto = _linux_dirs()
    if name == "menubar":  # the tray icon needs the desktop session: XDG autostart, not systemd
        auto.mkdir(parents=True, exist_ok=True)
        (auto / "ssc-tray.desktop").write_text(autostart_desktop(args))
        return True, "will start with your desktop session"
    sysd.mkdir(parents=True, exist_ok=True)
    for fn, text in systemd_units(name, args, daily_at, service).items():
        (sysd / fn).write_text(text)
    _systemctl("daemon-reload")
    target = f"ssc-{name}.service" if service else f"ssc-{name}.timer"
    r = _systemctl("enable", "--now", target)
    if r.returncode != 0 and "No such file" in r.stderr + r.stdout:
        return False, "systemd user services are not available on this system"
    return r.returncode == 0, (r.stderr or r.stdout).strip()


def _linux_remove(name):
    sysd, auto = _linux_dirs()
    if name == "menubar":
        (auto / "ssc-tray.desktop").unlink(missing_ok=True)
        return
    for t in (f"ssc-{name}.timer", f"ssc-{name}.service"):
        _systemctl("disable", "--now", t)
        (sysd / t).unlink(missing_ok=True)
    _systemctl("daemon-reload")


def _linux_installed(name):
    sysd, auto = _linux_dirs()
    if name == "menubar":
        return (auto / "ssc-tray.desktop").exists()
    return (sysd / f"ssc-{name}.timer").exists() or (sysd / f"ssc-{name}.service").exists()


# ---------------------------------------------------------------- Windows: Task Scheduler
def schtasks_create(name, args, daily_at=None, service=False):
    cmdline = subprocess.list2cmdline(core.self_cmd(*args))
    base = ["schtasks", "/Create", "/F", "/TN", f"SSC\\{name}", "/TR", cmdline]
    if service:
        return base + ["/SC", "ONLOGON"]
    return base + ["/SC", "DAILY", "/ST", f"{daily_at[0]:02d}:{daily_at[1]:02d}"]


def _win_install(name, args, daily_at, service):
    r = subprocess.run(schtasks_create(name, args, daily_at, service), capture_output=True, text=True)
    if r.returncode == 0 and service:  # also start it now, not only at the next sign-in
        subprocess.run(["schtasks", "/Run", "/TN", f"SSC\\{name}"], capture_output=True)
    return r.returncode == 0, (r.stderr or r.stdout).strip()


def _win_remove(name):
    subprocess.run(["schtasks", "/End", "/TN", f"SSC\\{name}"], capture_output=True)
    subprocess.run(["schtasks", "/Delete", "/F", "/TN", f"SSC\\{name}"], capture_output=True)


def _win_installed(name):
    return subprocess.run(["schtasks", "/Query", "/TN", f"SSC\\{name}"], capture_output=True).returncode == 0


# ---------------------------------------------------------------- public API
def install_daily(name, args, hour, minute):
    p = platform()
    return {"mac": _mac_install, "linux": _linux_install, "win": _win_install}[p](name, args, (hour, minute), False)


def install_service(name, args):
    p = platform()
    return {"mac": _mac_install, "linux": _linux_install, "win": _win_install}[p](name, args, None, True)


def remove(name):
    {"mac": _mac_remove, "linux": _linux_remove, "win": _win_remove}[platform()](name)


def installed(name):
    p = platform()
    return {"mac": lambda n: _mac_plist_path(n).exists(), "linux": _linux_installed, "win": _win_installed}[p](name)


def remove_legacy():
    """macOS only: jobs from before the rename (com.offlineuntis.*)."""
    if platform() != "mac":
        return
    for label in ("sync", "remind", "menubar", "parent"):
        plist = Path.home() / "Library" / "LaunchAgents" / f"com.offlineuntis.{label}.plist"
        if plist.exists():
            subprocess.run(["launchctl", "bootout", f"gui/{_uid()}/com.offlineuntis.{label}"], capture_output=True)
            plist.unlink(missing_ok=True)
