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
    if part == "power":
        pw = cfg["power"]
        _write(root, "etc/systemd/logind.conf.d/ssc-power.conf", serverconf.render_logind(pw))
        run(["systemctl", "mask" if pw["block_sleep"] else "unmask", *serverconf.SLEEP_TARGETS])
        run(["systemctl", "restart", "systemd-logind"])
        what = {"ignore": "closing the lid does nothing - the machine keeps running", "suspend": "closing the lid suspends", "poweroff": "closing the lid powers off"}[pw["lid"]]
        return [f"lid: {what}" + ("; suspend/hibernate blocked" if pw["block_sleep"] else "")]
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
        run(["systemctl", "restart", "ssc-server"])
        return ["web service restarted"]
    raise RuntimeError("unknown part " + part)


ORDER = ("network", "firewall", "ssh", "console", "power", "web", "links")


def apply(parts, cfg=None, root=None, run=_run):
    cfg = cfg or serverconf.load()
    done, failed = [], []
    parts = [p for p in ORDER if p in set(parts)]
    if "links" in parts and "web" in parts:
        parts.remove("links")
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
        parts = [p for p in json.loads(req.read_text()).get("parts", []) if p in ORDER]
        req.unlink()
    except (OSError, ValueError):
        return None
    return apply(parts, root=root, run=run)


def apply_all(root=None, run=_run):
    return apply(list(ORDER), root=root, run=run)
