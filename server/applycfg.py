"""Turns the validated server.json into system files and service actions. Runs as root (ssconfig / ssc-apply.service).

Everything written comes from the fixed templates in serverconf - never from raw text. `run` is injectable so tests can
check the commands without touching the machine; SSC_ROOT redirects /etc for tests.
"""
import json
import os
import re
import subprocess
import time
from pathlib import Path

import serverconf
import wake                                          # noqa: F401 (also imported where used)

WPA_REL = "etc/wpa_supplicant/ssc-wifi.conf"


def _run(cmd, check=False):
    return subprocess.run(cmd, capture_output=True, text=True, timeout=60, check=check)


def _write(root, rel, text, mode=0o644):
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name("." + p.name + ".new")
    tmp.write_text(text)
    os.chmod(tmp, mode)
    os.replace(tmp, p)


def _unix_user(root, name):
    """A normal (uid >= 1000) login user in <root>/etc/passwd."""
    try:
        for line in (root / "etc/passwd").read_text().splitlines():
            f = line.split(":")
            if len(f) > 3 and f[0] == name and f[2].isdigit() and 1000 <= int(f[2]) < 60000:
                return True
    except OSError:
        pass
    return False


def default_login_user(root=None):
    """The first normal user on this machine (the one the installer made)."""
    try:
        users = sorted((int(f[2]), f[0]) for f in (l.split(":") for l in ((root or serverconf.system_root()) / "etc/passwd").read_text().splitlines())
                       if len(f) > 3 and f[2].isdigit() and 1000 <= int(f[2]) < 60000)
    except OSError:
        return None
    return users[0][1] if users else None


def _interfaces(root):
    d = root / "sys/class/net"
    try:
        names = sorted(n.name for n in d.iterdir() if n.name != "lo" and not n.name.startswith(("docker", "veth", "br-", "virbr")))
    except OSError:
        return [], []
    wireless = [n for n in names if (d / n / "wireless").exists() or (d / n / "phy80211").exists()]
    return [n for n in names if n not in wireless], wireless


def default_interface(root):
    wired, wireless = _interfaces(root)
    return (wired or wireless or ["eth0"])[0]


def wifi_interface(root=None):
    return (_interfaces(root or serverconf.system_root())[1] or [None])[0]


def _apply_wifi(cfg, root, wlan):
    """Writes /etc/wpa_supplicant/ssc-wifi.conf (root-only). The key comes from the staged file (new password) or, when the
    network name is unchanged, from the file already there. Returns a note."""
    wf = cfg["wifi"]
    path = root / WPA_REL
    staged = serverconf.state_dir() / "apply" / "wifi.psk"
    psk = None
    if not wf["open"]:
        try:
            st = json.loads(staged.read_text())
            if st.get("ssid") == wf["ssid"]:
                psk = st.get("psk")
        except (OSError, ValueError):
            pass
        if not psk and path.exists() and serverconf.ssid_from_conf(path.read_text()) == wf["ssid"]:
            psk = serverconf.psk_from_conf(path.read_text())
        if not psk:
            raise RuntimeError(f"wifi: no password known for '{wf['ssid']}' - set it again (ssconfig wifi set)")
    _write(root, WPA_REL, serverconf.render_wpa(wf, psk), 0o600)
    staged.unlink(missing_ok=True)
    return f"Wi-Fi '{wf['ssid']}' on {wlan}"


def apply_part(part, cfg, root=None, run=_run):
    """-> list of human-readable notes. Raises RuntimeError when a part could not be applied (and was rolled back)."""
    root = root or serverconf.system_root()
    if part == "network":
        n = cfg["network"]
        wired, wireless = _interfaces(root)
        wlan = wireless[0] if wireless else None
        notes = []
        if cfg["wifi"]["enabled"] and not wlan:
            notes.append("Wi-Fi is on but no wireless card was found; using the wired port")
        iface = (wired[0] if wired else (wlan or "eth0")) if n["interface"] == "auto" else n["interface"]
        if cfg["wifi"]["enabled"] and wlan:
            notes.append(_apply_wifi(cfg, root, wlan))
        elif (root / WPA_REL).exists() and not cfg["wifi"]["enabled"]:
            (root / WPA_REL).unlink()
        _write(root, "etc/network/interfaces.d/ssc", serverconf.render_network(cfg, iface, wlan))
        if cfg["power"]["wol"] and iface != wlan:
            run(["ethtool", "-s", iface, "wol", "g"])
        resolv = serverconf.render_resolv(n)
        if resolv:
            _write(root, "etc/resolv.conf", resolv)
        _write(root, "etc/hostname", n["hostname"] + "\n")
        hosts = root / "etc/hosts"
        try:
            lines = [l for l in hosts.read_text().splitlines() if not l.startswith("127.0.1.1")]
        except OSError:
            lines = ["127.0.0.1\tlocalhost"]
        _write(root, "etc/hosts", "\n".join(lines + [f"127.0.1.1\t{n['hostname']}"]) + "\n")
        run(["hostname", n["hostname"]])
        ups = [x for x in dict.fromkeys([iface, wlan if cfg["wifi"]["enabled"] else None]) if x]
        for x in ups:
            run(["ifdown", "--force", x])
        for x in ups:
            r = run(["ifup", x])
            if r.returncode != 0:
                raise RuntimeError(f"network: ifup {x} failed: " + (r.stderr or r.stdout).strip()[:200])
        return notes + [f"network applied on {', '.join(ups)} ({n['mode']})"]
    if part == "ssh":
        s = cfg["ssh"]
        path = "etc/ssh/sshd_config.d/ssc.conf"
        old = (root / path).read_text() if (root / path).exists() else None
        _write(root, path, serverconf.render_sshd(s))
        r = run(["sshd", "-t"])
        if r.returncode != 0:
            if old is None:
                (root / path).unlink()
            else:
                _write(root, path, old)
            raise RuntimeError("ssh: sshd rejected the settings, previous settings kept: " + (r.stderr or "").strip()[:200])
        run(["systemctl", "enable" if s["enabled"] else "disable", "--now" if s["enabled"] else "--now", "ssh"])
        if s["enabled"]:
            run(["systemctl", "reload-or-restart", "ssh"])
        return ["ssh " + ("enabled" if s["enabled"] else "disabled")]
    if part == "firewall":
        _write(root, "etc/nftables.conf", "#!/usr/sbin/nft -f\n" + serverconf.render_nftables(cfg))
        r = run(["nft", "-c", "-f", str(root / "etc/nftables.conf")])
        if r.returncode != 0:
            raise RuntimeError("firewall: nft rejected the rules: " + (r.stderr or "").strip()[:200])
        run(["nft", "-f", str(root / "etc/nftables.conf")])
        run(["systemctl", "enable", "nftables"])
        return ["firewall " + ("on" if cfg["firewall"]["enabled"] else "off")]
    if part == "schedule":
        import schedule
        sc = cfg["schedule"]
        sysd = root / "etc/systemd/system"
        if sc["enabled"] and sc["mode"] != "quiet" and not schedule.has_rtc(root):
            raise RuntimeError("schedule: this machine has no hardware clock (RTC) to wake it up - use the 'quiet' mode")
        units = schedule.render_units(sc) if sc["enabled"] else {}
        for name in schedule.UNITS:
            if name in units:
                _write(root, f"etc/systemd/system/{name}", units[name])
            else:
                (sysd / name).unlink(missing_ok=True)
        run(["systemctl", "daemon-reload"])
        for timer in ("ssc-sleep.timer", "ssc-wake.timer"):
            if timer in units:
                run(["systemctl", "enable", "--now", timer])
            else:
                run(["systemctl", "disable", "--now", timer])
        if not sc["enabled"]:
            schedule.run_up(dict(cfg, schedule=dict(sc, screen_off=True)), run)         # switching the schedule off must never leave the server asleep
            return ["sleep schedule off"]
        return [f"sleep schedule on: {sc['mode']}, off {sc['off']}, on {sc['on']}"]
    if part.startswith("restart:"):
        import sysinfo
        name = part[len("restart:"):]
        if name not in sysinfo.RESTARTABLE:
            raise RuntimeError(f"{name} can't be restarted from here")
        r = run(["systemctl", "restart", name])
        if r.returncode != 0:
            raise RuntimeError(f"restarting {name} failed: {(r.stderr or r.stdout).strip()[:150]}")
        return [f"{name} restarted"]
    if part in ("reboot", "poweroff"):
        # a few seconds later, so that this request can still be answered and its result written before the machine goes down
        run(["systemd-run", "--no-block", "--on-active=5", "systemctl", part])
        return [("restarting" if part == "reboot" else "powering off") + " in a few seconds"]
    if part == "update":
        import updater
        opt = str(serverconf.system_root() / "opt/ssc")
        try:
            v = updater.install_server(root=opt, run=run, log=lambda m: None)
        except updater.UpdateError as e:
            raise RuntimeError("update: " + str(e))
        if not HEALTH_CHECK(cfg):                      # the new version must answer, or the old one comes back
            try:
                back = updater.rollback(root=opt, run=run)
            except updater.UpdateError as e:
                raise RuntimeError(f"update: {v} does not start and the roll-back failed: {e}")
            raise RuntimeError(f"update: {v} did not start properly, so the previous version ({back}) was put back")
        return [f"updated to {v} and it is running (the previous version is kept for a roll-back)"]
    if part == "waketest":
        import wake
        import schedule
        if not schedule.has_rtc(root):
            raise RuntimeError("wake-up test: this machine has no hardware clock (RTC) to wake it up")
        if not wake.can_suspend(root):
            raise RuntimeError("wake-up test: this machine can't suspend to memory - the 'quiet' sleep mode is the one for it")
        wake.marker_path().unlink(missing_ok=True)
        run(["systemd-run", "--no-block", "--on-active=5", "/usr/local/bin/ssconfig", "waketest-run"])
        return [f"wake-up test: going to sleep in a few seconds, alarm in {wake.TEST_SECONDS} s"]
    if part.startswith("poweroff-wake:"):
        import schedule
        if not schedule.has_rtc(root):
            raise RuntimeError("this machine has no hardware clock (RTC) to switch it on again - plain power off only")
        try:
            wake_at = wake_epoch(part.split(":", 1)[1])
        except ValueError as e:
            raise RuntimeError(str(e))
        run(["systemd-run", "--no-block", "--on-active=5", "rtcwake", "-m", "off", "-t", str(wake_at)])
        return ["powering off in a few seconds; the hardware clock should switch it on at " + time.strftime("%Y-%m-%d %H:%M", time.localtime(wake_at))]
    if part == "power":
        pw = cfg["power"]
        _write(root, "etc/systemd/logind.conf.d/ssc-power.conf", serverconf.render_logind(pw))
        run(["systemctl", "mask" if pw["block_sleep"] else "unmask", *serverconf.SLEEP_TARGETS])
        run(["systemctl", "restart", "systemd-logind"])
        if pw["wol"]:
            for nic in _interfaces(root)[0]:
                run(["ethtool", "-s", nic, "wol", "g"])
        what = {"ignore": "closing the lid does nothing - the machine keeps running", "suspend": "closing the lid suspends", "poweroff": "closing the lid powers off"}[pw["lid"]]
        return [f"lid: {what}" + ("; suspend/hibernate blocked" if pw["block_sleep"] else "") + ("; Wake-on-LAN on" if pw["wol"] else "")]
    if part == "console":
        user = cfg["console"]["autologin_user"]
        rel = "etc/systemd/system/getty@tty1.service.d/autologin.conf"
        if user:
            if not _unix_user(root, user):
                raise RuntimeError(f"console: there is no normal user '{user}' on this system (auto-login needs the Linux login user)")
            _write(root, rel, "# Managed by ssconfig\n[Service]\nExecStart=\nExecStart=-/sbin/agetty --autologin " + user + " --noclear %I $TERM\n")
            note = f"console auto-login as {user} (from the next boot, or: systemctl restart getty@tty1)"
        else:
            (root / rel).unlink(missing_ok=True)
            note = "console auto-login off (from the next boot)"
        run(["systemctl", "daemon-reload"])
        return [note]
    if part in ("web", "links"):
        notes = []
        if part == "web" and cfg["web"]["https"]:
            import tls
            ips = [a.split("/")[0] for _, a in serverconf.parse_ip_json(run(["ip", "-j", "-4", "addr"]).stdout)]
            notes.append(tls.generate(cfg["network"]["hostname"], ips, run, root))
        run(["systemctl", "restart", "ssc-server"])
        return notes + ["web service restarted"]
    raise RuntimeError("unknown part " + part)


HEALTH_CHECK = None                                 # set below


def _healthy(cfg, tries=20, wait=2):
    """Does the web service answer /health on this machine? (After an update or a restart.)"""
    import ssl
    import urllib.request
    scheme = "https" if cfg["web"]["https"] else "http"
    ctx = ssl._create_unverified_context() if scheme == "https" else None          # it is our own machine
    for _ in range(tries):
        try:
            with urllib.request.urlopen(f"{scheme}://127.0.0.1:{cfg['web']['port']}/health", timeout=3, context=ctx) as r:
                if r.status == 200:
                    return True
        except (OSError, ValueError):
            pass
        time.sleep(wait)
    return False


HEALTH_CHECK = _healthy


def wake_epoch(spec, now=None):
    """'06:00' (next time it is 06:00) or '+480' (in 480 minutes) -> epoch seconds."""
    now = now or time.time()
    if spec.startswith("+") and spec[1:].isdigit() and 1 <= int(spec[1:]) <= 60 * 24 * 14:
        return int(now + int(spec[1:]) * 60)
    if serverconf.TIME_RE.match(spec):
        lt = time.localtime(now)
        t = time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, int(spec[:2]), int(spec[3:]), 0, 0, 0, -1))
        return int(t if t > now + 120 else t + 86400)
    raise ValueError("the switch-on time looks like 06:00, or +480 for minutes from now")


POWER_WAKE_RE = re.compile(r"^poweroff-wake:(\d{2}:\d{2}|\+\d{1,5})$")
ORDER = ("network", "firewall", "ssh", "console", "power", "schedule", "web", "links", "update")


def apply(parts, cfg=None, root=None, run=_run):
    cfg = cfg or serverconf.load()
    done, failed = [], []
    wanted = set(parts)
    power = [p for p in ("poweroff", "reboot") if p in wanted]
    power += sorted(p for p in wanted if POWER_WAKE_RE.match(p))
    power = power[:1]                                                                    # one power action at most, and always last
    tests = [p for p in ("waketest",) if p in wanted]
    restarts = sorted(p for p in wanted if p.startswith("restart:"))
    parts = [p for p in ORDER if p in wanted]
    if "links" in parts and "web" in parts:
        parts.remove("links")
    parts += restarts + (tests if not power else []) + power
    for p in parts:
        try:
            done += apply_part(p, cfg, root, run)
        except (RuntimeError, OSError, subprocess.SubprocessError) as e:
            failed.append(str(e))
    res = {"at": time.time(), "ok": not failed, "done": done, "failed": failed}
    d = serverconf.state_dir() / "apply"
    d.mkdir(parents=True, exist_ok=True)
    (d / "result.json").write_text(json.dumps(res))
    return res


def process_request(root=None, run=_run):
    """The web panel only drops a request file; this runs as root and re-validates everything from server.json."""
    req = serverconf.state_dir() / "apply" / "request.json"
    try:
        import sysinfo
        ok = lambda p: p in ORDER or p in ("reboot", "poweroff", "waketest") or bool(POWER_WAKE_RE.match(p)) or (p.startswith("restart:") and p[len("restart:"):] in sysinfo.RESTARTABLE)
        parts = [p for p in json.loads(req.read_text()).get("parts", []) if isinstance(p, str) and ok(p)]
        req.unlink()
    except (OSError, ValueError):
        return None
    return apply(parts, root=root, run=run)


def apply_all(root=None, run=_run):
    return apply(list(ORDER), root=root, run=run)
