"""Reader for the "SSC Install" file (autoinstall.ssci).

The .ssci file is PLAIN TEXT settings, never code: this module only splits lines at "=" and checks the names against a fixed
list; nothing in the file is evaluated or executed. Put the file on a USB stick (or any attached drive) and a machine booted
from the SSC Server installer installs itself with exactly these settings.

Settings that are not in the file are asked on screen, like in the normal installer; only the questions that remain are shown.
With  ask = no  the missing ones use defaults instead (fully unattended).

Format: plain text, one  key = value  per line. Lines starting with # or ; are comments (there are no end-of-line comments, so a
password may contain #). Quotes around a value are optional. [section] lines are shortcuts for a key prefix. JSON is accepted too.

    erase = yes                  REQUIRED: you accept that the target disk is erased
    disk = auto                  auto = the only internal disk, or /dev/sda ...
    admin = admin
    password = long-and-private
    hostname = ssc-server
    timezone = Europe/Vienna
    keymap = de
    network = dhcp               or: static  (with address, gateway, dns)
    wifi.ssid = Home
    wifi.password = ...
    account.anna = user:her-password
    untis = untis://setschool?url=...&school=...&user=...&key=...
    power.lid = ignore           any setting of the server: section.key = value
    schedule.enabled = yes

The file holds passwords in clear text: keep the stick private. Nothing is installed unless "erase = yes" is in the file, the
stick the file came from is never erased, and there is a countdown before anything is changed.
"""
import json
import re
from pathlib import Path

import serverconf

FILE_NAME = "autoinstall.ssci"
MAX_BYTES = 65536
BOOLS = {"yes": True, "true": True, "on": True, "1": True, "no": False, "false": False, "off": False, "0": False}

# keys the installer itself understands (everything else must be "section.key" of the server settings)
INSTALLER_KEYS = {"erase", "wait", "ask", "disk", "admin", "password", "timezone", "keymap", "autologin", "finish", "hostname", "ssh", "network",
                  "address", "gateway", "dns", "untis", "wifi.ssid", "wifi.password", "wifi.country", "wifi.hidden", "wifi.open"}


class SsciError(ValueError):
    pass


def _bool(v, where):
    if str(v).strip().lower() not in BOOLS:
        raise SsciError(f"{where}: expected yes or no, got '{v}'")
    return BOOLS[str(v).strip().lower()]


def parse(text):
    """-> ordered {key: value} (all strings). Raises SsciError with the line number."""
    text = str(text).lstrip("﻿")
    if len(text.encode("utf-8")) > MAX_BYTES:
        raise SsciError("the file is far too large for an SSC Install file")
    if text.lstrip().startswith("{"):
        try:
            data = json.loads(text)
        except ValueError as e:
            raise SsciError(f"not valid JSON: {e}")
        return _flatten_json(data)
    out, prefix = {}, ""
    for n, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line[0] in "#;":
            continue
        if line.startswith("[") and line.endswith("]"):
            prefix = line[1:-1].strip().lower()
            prefix = prefix + "." if prefix else ""
            continue
        if "=" not in line:
            raise SsciError(f"line {n}: expected  key = value")
        key, value = line.split("=", 1)
        key, value = key.strip().lower(), value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if not re.fullmatch(r"[a-z0-9_.@-]+", key):
            raise SsciError(f"line {n}: '{key}' is not a valid setting name")
        key = prefix + key if prefix and "." not in key else key
        if key in out:
            raise SsciError(f"line {n}: '{key}' is set twice")
        out[key] = value
    return out


def _flatten_json(data, prefix=""):
    if not isinstance(data, dict):
        raise SsciError("a JSON SSC Install file must be an object")
    out = {}
    for k, v in data.items():
        key = (prefix + str(k)).lower()
        if isinstance(v, dict):
            out.update(_flatten_json(v, key + "."))
        elif isinstance(v, (list, tuple)):
            out[key] = ", ".join(str(x) for x in v)
        elif isinstance(v, bool):
            out[key] = "yes" if v else "no"
        else:
            out[key] = str(v)
    if "wifi.passphrase" in out:
        out["wifi.password"] = out.pop("wifi.passphrase")
    if "network.mode" in out and "network" not in out:
        out["network"] = out.pop("network.mode")
    for alias in ("address", "gateway", "dns"):
        if f"network.{alias}" in out and alias not in out:
            out[alias] = out.pop(f"network.{alias}")
    return out


def _coerce(section, key, value, where):
    default = serverconf.DEFAULTS[section][key]
    if isinstance(default, bool):
        return _bool(value, where)
    if isinstance(default, int):
        try:
            return int(value)
        except ValueError:
            raise SsciError(f"{where}: expected a number, got '{value}'")
    if isinstance(default, list):
        return [x for x in re.split(r"[,\s]+", value) if x]
    return value


def interpret(flat):
    """-> (answers for install_plan.validate_answers, extras). Unknown settings are errors, not silently ignored."""
    a, settings, accounts, extras = {}, {}, [], {}
    errors = []
    for key, value in flat.items():
        try:
            if key in INSTALLER_KEYS:
                continue
            if key.startswith("account."):
                name = key[len("account."):]
                role, _, pw = value.partition(":")
                if not pw:
                    raise SsciError(f"{key}: write  role:password  (role is guest, user or admin)")
                accounts.append((name, role.strip().lower(), pw))
                continue
            section, _, sub = key.partition(".")
            if section in serverconf.DEFAULTS and sub in serverconf.DEFAULTS[section] and not (section == "wifi" and sub in ("ssid", "country", "hidden", "open")):
                settings.setdefault(section, {})[sub] = _coerce(section, sub, value, key)
                continue
            raise SsciError(f"unknown setting '{key}'")
        except SsciError as e:
            errors.append(str(e))
    if errors:
        raise SsciError("; ".join(errors[:5]) + (f" (and {len(errors) - 5} more)" if len(errors) > 5 else ""))
    g = flat.get
    extras["erase"] = _bool(g("erase", "no"), "erase")
    try:
        extras["wait"] = max(0, min(600, int(g("wait", "15"))))
    except ValueError:
        raise SsciError("wait: expected a number of seconds")
    extras["ask"] = _bool(g("ask", "yes"), "ask")          # settings missing from the file: ask on screen (default) or use defaults (ask = no)
    extras["finish"] = g("finish", "poweroff").lower()
    if extras["finish"] not in ("poweroff", "reboot"):
        raise SsciError("finish: poweroff or reboot")
    for k in ("disk", "admin", "password", "hostname", "timezone", "keymap"):
        if k in flat:
            a[k] = flat[k]
    if "ssh" in flat:
        a["ssh"] = _bool(flat["ssh"], "ssh")
    if "autologin" in flat:
        a["autologin"] = _bool(flat["autologin"], "autologin")
    net = {}
    if "network" in flat:
        net["mode"] = flat["network"].lower()
    for k in ("address", "gateway", "dns"):
        if k in flat:
            net[k] = flat[k]
    if net:
        a["network"] = net
    if g("wifi.ssid"):
        a["wifi"] = {"ssid": flat["wifi.ssid"], "passphrase": g("wifi.password", ""), "country": g("wifi.country", ""),
                     "hidden": _bool(g("wifi.hidden", "no"), "wifi.hidden"), "open": _bool(g("wifi.open", "no" if g("wifi.password") else "yes"), "wifi.open")}
    a["settings"], a["accounts"], a["untis"] = settings, accounts, g("untis", "")
    return a, extras


def load(text):
    """Text of an autoinstall.ssci -> (answers, extras)."""
    return interpret(parse(text))


# ------------------------------------------------------------------ writing one (sscigen)
def _v(x):
    x = str(x)
    if "\n" in x or "\r" in x:
        raise SsciError("a value can't contain a line break")
    return '"' + x + '"' if x != x.strip() or x[:1] in "\"'" else x


def _yn(b):
    return "yes" if b else "no"


def dump(a, extras):
    """Answers + extras (what qa.interview() returns) -> the text of an autoinstall.ssci that load() reads back unchanged."""
    L = ["# autoinstall.ssci - SSC Install file, written by sscigen.",
         "# Plain text settings: nothing in this file is executed. It holds passwords in clear text - keep it private.", "",
         f"erase = {_yn(extras.get('erase'))}", f"wait = {extras.get('wait', 15)}", f"finish = {extras.get('finish', 'poweroff')}"]
    if extras.get("ask") is False:
        L.append("ask = no")
    L.append("")
    for k in ("disk", "admin", "password", "hostname", "timezone", "keymap"):
        if k in a:
            L.append(f"{k} = {_v(a[k])}")
    for k in ("ssh", "autologin"):
        if k in a:
            L.append(f"{k} = {_yn(a[k])}")
    net = a.get("network") or {}
    if net.get("mode"):
        L.append(f"network = {net['mode']}")
    for k in ("address", "gateway", "dns"):
        if net.get(k):
            L.append(f"{k} = {_v(', '.join(net[k]) if isinstance(net[k], list) else net[k])}")
    w = a.get("wifi")
    if w:
        L += ["", f"wifi.ssid = {_v(w['ssid'])}"] + ([f"wifi.password = {_v(w['passphrase'])}"] if w.get("passphrase") else []) \
            + ([f"wifi.country = {w['country']}"] if w.get("country") else []) + ([f"wifi.hidden = {_yn(w['hidden'])}"] if w.get("hidden") else [])
    if a.get("accounts"):
        L.append("")
        L += [f"account.{n} = {r}:{_v(pw)}" for n, r, pw in a["accounts"]]
    if a.get("untis"):
        L += ["", f"untis = {_v(a['untis'])}"]
    extra = []
    for section, d in (a.get("settings") or {}).items():
        for k, val in d.items():
            extra.append(f"{section}.{k} = " + (_yn(val) if isinstance(val, bool) else ", ".join(val) if isinstance(val, list) else _v(val)))
    if extra:
        L += [""] + extra
    return "\n".join(L) + "\n"


def write(path, text):
    import os
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)          # holds passwords: owner only
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text)


# ------------------------------------------------------------------ finding the file on attached drives
def candidates(lsblk_json, skip=()):
    """lsblk -J -p -o NAME,FSTYPE,TYPE,PKNAME,MOUNTPOINT -> [(device, fstype, parent_disk)] of file systems worth looking into."""
    data = json.loads(lsblk_json or "{}").get("blockdevices", [])
    out = []

    def walk(node, disk):
        name = node.get("name", "")
        disk = name if node.get("type") == "disk" else disk
        fs = (node.get("fstype") or "").lower()
        if fs in ("vfat", "exfat", "ext2", "ext3", "ext4", "ntfs") and name not in skip and not str(node.get("mountpoint") or "").startswith("/run/live"):
            out.append((name, fs, disk))
        for ch in node.get("children", []) or []:
            walk(ch, disk)
    for n in data:
        walk(n, n.get("name", ""))
    return out


def pick_file(names):
    """The name of the SSC Install file in a directory listing (FAT is case-insensitive, so Autoinstall.SSCI counts)."""
    return next((n for n in names if n.lower() == FILE_NAME), None)


def read_from(mountpoint):
    d = Path(mountpoint)
    for base in (d, d / "ssc"):
        try:
            name = pick_file(p.name for p in base.iterdir() if p.is_file())
        except OSError:
            continue
        if name:
            with open(base / name, "rb") as f:
                return f.read(MAX_BYTES + 1).decode("utf-8", "replace")
    return None


EXAMPLE = '''# autoinstall.ssci - SSC Install file. Put it on a USB stick (not the installer stick itself unless you made it writable),
# boot the machine from the SSC Server installer, and it installs by itself.
# THE DISK IS ERASED. Settings you leave out are asked on screen as usual (put  ask = no  to use defaults instead, fully unattended).
# For a fully unattended install this line must say yes; when questions are asked, the usual "type ERASE" question is asked too.
erase = yes
#ask = no
wait = 15                      # seconds of countdown before anything is changed (power off to cancel)
finish = poweroff              # or: reboot

disk = auto                    # the only internal disk, or e.g. /dev/sda
admin = admin
password = change-me-please
hostname = ssc-server
timezone = Europe/Vienna
keymap = de
ssh = yes
autologin = no                 # log in on the machine's own screen without a password

network = dhcp                 # or: static
#address = 192.168.1.50/24
#gateway = 192.168.1.1
#dns = 1.1.1.1, 9.9.9.9

#wifi.ssid = HomeNetwork
#wifi.password = wifi-password
#wifi.country = AT

#account.anna = user:her-password       # more accounts: guest, user or admin
#untis = untis://setschool?url=...&school=...&user=...&key=...

# any setting of the server works as  section.key = value
#power.lid = ignore
#schedule.enabled = yes
#schedule.off = 22:00
#schedule.on = 06:00
#links.long = yes
'''
