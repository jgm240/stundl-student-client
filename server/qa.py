"""The installer's questions - one flow used by two programs:

  ssc-install   asks them on the machine (with its real disks) and installs
  sscigen       asks the same questions anywhere and writes an autoinstall.ssci file instead

Questions that already have an answer (from an autoinstall.ssci) are skipped, so a partial file only shows what is missing.
Every answer is checked as it is given. The screens are whiptail when it exists, otherwise plain text prompts (macOS, Windows/WSL).
"""
import getpass
import re
import shutil
import subprocess
import sys

import auth
import install_plan as plan
import serverconf


class Cancelled(Exception):
    pass


# ------------------------------------------------------------------ screens
class WhiptailUI:
    kind = "whiptail"

    def __init__(self, backtitle="SSC Server"):
        self.backtitle = backtitle

    def _wt(self, *args):
        r = subprocess.run(["whiptail", "--backtitle", self.backtitle, *args], stderr=subprocess.PIPE, text=True)
        return r.returncode, r.stderr.strip()

    @staticmethod
    def _h(text, extra=7):
        return str(min(24, extra + text.count("\n") + len(text) // 62))

    def msgbox(self, text):
        self._wt("--msgbox", text, self._h(text), "74")

    def inputbox(self, text, default=""):
        rc, v = self._wt("--inputbox", text, self._h(text, 8), "74", default)
        if rc:
            raise Cancelled()
        return v

    def passwordbox(self, text):
        rc, v = self._wt("--passwordbox", text, self._h(text, 8), "74")
        if rc:
            raise Cancelled()
        return v

    def yesno(self, text, default=True):
        rc, _ = self._wt(*(() if default else ("--defaultno",)), "--yesno", text, self._h(text), "74")
        return rc == 0

    def menu(self, text, rows):
        rc, v = self._wt("--menu", text, str(min(24, 9 + len(rows) + text.count("\n"))), "76", str(min(12, len(rows))), *[x for r in rows for x in r])
        if rc:
            raise Cancelled()
        return v


class TextUI:
    kind = "text"

    def msgbox(self, text):
        print("\n" + text)

    def inputbox(self, text, default=""):
        try:
            v = input(f"\n{text}" + (f" [{default}]" if default else "") + "\n> ").strip()
        except (EOFError, KeyboardInterrupt):
            raise Cancelled()
        return v or default

    def passwordbox(self, text):
        try:
            return getpass.getpass(f"\n{text}\n(not shown) > ")
        except (EOFError, KeyboardInterrupt):
            raise Cancelled()

    def yesno(self, text, default=True):
        v = self.inputbox(text + (" [Y/n]" if default else " [y/N]"), "").lower()
        return default if not v else v.startswith("y")

    def menu(self, text, rows):
        print("\n" + text)
        for i, (k, label) in enumerate(rows, 1):
            print(f"  {i}) {k}  -  {label}")
        while True:
            v = self.inputbox("Number or name", "1")
            for i, (k, _) in enumerate(rows, 1):
                if v == str(i) or v == k:
                    return k


def make_ui(backtitle="SSC Server"):
    if shutil.which("whiptail") and sys.stdin.isatty() and sys.stdout.isatty():
        return WhiptailUI(backtitle)
    return TextUI()


# ------------------------------------------------------------------ checked questions
def _ask(ui, text, default, check):
    """Ask until `check(answer)` stops raising ValueError."""
    while True:
        v = ui.inputbox(text, default).strip()
        try:
            return check(v) or v
        except (ValueError, auth.AuthError) as e:
            ui.msgbox("That doesn't work: " + str(e))


def _hostname(v):
    serverconf.validate({"network": {"hostname": v}})


def _admin(v):
    auth._check_name(v.lower())
    return v.lower()


def _tz(v):
    if not re.match(r"^[A-Za-z0-9_+./-]{1,40}$", v) or ".." in v:
        raise ValueError("a time zone like Europe/Vienna or UTC")


def _keymap(v):
    if not re.match(r"^[a-z0-9_-]{1,20}$", v):
        raise ValueError("a keyboard layout like us, de, at, fr")


def _disk(v):
    if v != "auto" and not re.fullmatch(r"/dev/[A-Za-z0-9/_-]+", v):
        raise ValueError("'auto' or a device such as /dev/sda")


def _time(v):
    if not serverconf.TIME_RE.match(v):
        raise ValueError("a time like 22:00 (24-hour clock)")


def _password(ui, role, what):
    while True:
        p1 = ui.passwordbox(f"{what} (at least {auth.MIN_LEN[role]} characters)")
        try:
            auth._check_password(role, p1)
        except auth.AuthError as e:
            ui.msgbox(str(e))
            continue
        if ui.passwordbox("Repeat the password") == p1:
            return p1
        ui.msgbox("The two passwords differ.")


def interview(ui, preset=None, disks=None, file_mode=False, wifi_card=None, erase_given=False, extras_preset=None):
    """Ask what is not in `preset` yet. -> (answers, extras) in the shape ssci_file.interpret() produces.

    disks        the installable disks (the installer); None = writing a file for another machine (sscigen)
    erase_given  the file already says erase = yes: don't ask for the confirmation again
    extras_preset  (sscigen --edit) the old file's erase / wait / finish: kept, not asked again
    Raises Cancelled when the user backs out."""
    a = dict(preset or {})
    extras = {"erase": bool(erase_given), "ask": True}
    keep = dict(extras_preset or {})
    if keep:
        extras.update({k: v for k, v in keep.items() if k in ("erase", "wait", "finish", "ask")})
    # --- disk
    if "disk" not in a:
        if disks is not None:
            if not disks:
                ui.msgbox("No disk found that is large enough (4 GB or more).")
                raise Cancelled()
            rows = [(d["path"], f"{plan.human(d['size'])}  {d['model']}{'  (removable)' if d['removable'] else ''}") for d in disks]
            a["disk"] = ui.menu("SSC Server will be installed on this disk.\nEVERYTHING ON IT WILL BE ERASED.", rows)
        else:
            a["disk"] = _ask(ui, "Disk to install on: 'auto' (the only internal disk) or a device such as /dev/sda", "auto", _disk)
    # --- consent to erase
    if disks is not None:
        if not erase_given:
            if ui.inputbox(f"This erases {a['disk']}.\nType ERASE (capital letters) to continue:", "") != "ERASE":
                raise Cancelled()
            extras["erase"] = True
    elif not (keep and keep.get("erase")):
        extras["erase"] = ui.yesno("Let the installer erase the disk by itself when it finds this file?\n"
                                   "Needed for a fully unattended install. If you say no, it asks for confirmation on screen.", default=False)
    # --- who and what
    if "hostname" not in a:
        a["hostname"] = _ask(ui, "Name of this server (hostname)", "ssc-server", _hostname)
    if "admin" not in a:
        a["admin"] = _ask(ui, "Name of the administrator account.\nIt is used for the web admin panel and for logging in on the machine and over SSH.", "admin", _admin)
    if "password" not in a:
        a["password"] = _password(ui, "admin", "Administrator password")
    # --- network
    net = dict(a.get("network") or {})
    if "mode" not in net:
        net["mode"] = ui.menu("Network", [("dhcp", "Automatic address (DHCP) - recommended"), ("static", "Fixed IP address")])
    if net["mode"] == "static":
        while not all(k in net for k in ("address", "gateway", "dns")):
            net.setdefault("address", ui.inputbox("Address with prefix, e.g. 192.168.1.50/24", ""))
            net.setdefault("gateway", ui.inputbox("Gateway (your router)", ""))
            net.setdefault("dns", ui.inputbox("DNS server(s), separated by comma", net["gateway"]))
        try:
            serverconf.validate({"network": {**net, "hostname": a["hostname"]}})
        except ValueError as e:
            ui.msgbox("That network setup doesn't work: " + str(e))
            for k in ("address", "gateway", "dns"):
                net.pop(k, None)
            a["network"] = net
            return interview(ui, {**a, "network": {"mode": "static"}}, disks, file_mode, wifi_card, erase_given, extras_preset)
    a["network"] = net
    # --- Wi-Fi
    if "wifi" not in a:
        if (file_mode or wifi_card) and ui.yesno("Connect this server to a Wi-Fi network?" + ("" if wifi_card is not False else "\n(Only used when the machine has a Wi-Fi card.)"), default=False):
            while True:
                ssid = ui.inputbox("Wi-Fi network name (SSID)", "")
                pw = ui.passwordbox("Wi-Fi password (leave empty for an open network)")
                cc = ui.inputbox("Country code (two letters, e.g. AT, DE, US)", "AT").upper()
                try:
                    serverconf.check_ssid(ssid)
                    if pw:
                        serverconf.derive_psk(ssid, pw)
                    serverconf.validate({"wifi": {"enabled": True, "ssid": ssid, "country": cc, "open": not pw}})
                    break
                except ValueError as e:
                    ui.msgbox("That doesn't work: " + str(e))
            a["wifi"] = {"ssid": ssid, "passphrase": pw, "open": not pw, "country": cc}
    # --- time, keyboard, access
    if "timezone" not in a:
        a["timezone"] = _ask(ui, "Time zone (e.g. Europe/Vienna, UTC)", "UTC", _tz)
    if "keymap" not in a:
        a["keymap"] = _ask(ui, "Keyboard layout (us, de, at, fr, ...)", "us", _keymap)
    if "ssh" not in a:
        a["ssh"] = ui.yesno("Turn on SSH (remote login)?", default=True)
    if "autologin" not in a:
        a["autologin"] = ui.yesno("Log in automatically on this server's own screen and keyboard?\n(Console only: SSH and the web panel still ask for a password.)\n\n"
                                  "Convenient, but anyone who can touch the machine then has the administrator account.", default=False)
    a.setdefault("settings", {})
    a.setdefault("accounts", [])
    a.setdefault("untis", "")
    # --- extras that only make sense in a file for another machine
    if file_mode:
        _file_extras(ui, a, extras, keep=bool(keep))
    return a, extras


def _file_extras(ui, a, extras, keep=False):
    s = a["settings"]
    while ui.yesno("Add another account (guest, user or admin)?", default=False):
        name = _ask(ui, "Account name", "", _admin)
        role = ui.menu("Role", [("guest", "Parent view"), ("user", "Parent view (more sections)"), ("admin", "Full app and admin panel")])
        a["accounts"].append((name, role, _password(ui, role, f"Password for {name}")))
    if ui.yesno("Enter the WebUntis (Untis Mobile) link now?\nIt is stored on the new server only, never shown again.", default=False):
        import untis_direct
        while True:
            link = ui.inputbox("Paste the untis://setschool?... link", "")
            if untis_direct.parse_qr(link):
                a["untis"] = link
                break
            ui.msgbox("That link doesn't work: " + untis_direct.explain_qr(link))
    lid = ui.menu("When the laptop lid is closed...", [("ignore", "do nothing - keep running (server)"), ("suspend", "suspend"), ("poweroff", "power off")])
    if lid != "ignore":
        s.setdefault("power", {})["lid"] = lid
    if ui.yesno("Use a sleep schedule (switch off at night, back on in the morning)?", default=False):
        off = _ask(ui, "Switch off at (24-hour)", "22:00", _time)
        on = _ask(ui, "Switch back on at", "06:00", _time)
        mode = ui.menu("Mode", [("quiet", "stop SSC, disks to sleep, screen off"), ("suspend", "suspend the machine and wake it up"), ("poweroff", "power off and switch on again")])
        s["schedule"] = {"enabled": True, "off": off, "on": on, "mode": mode}
    if ui.yesno("Require long Parent links?", default=False):
        s.setdefault("links", {})["long"] = True
    if keep:
        return
    extras["finish"] = ui.menu("When the installation is done, the machine should...", [("poweroff", "power off"), ("reboot", "restart")])
    extras["wait"] = int(_ask(ui, "Seconds of countdown before an unattended install changes anything (0-600)", "15",
                              lambda v: None if v.isdigit() and 0 <= int(v) <= 600 else (_ for _ in ()).throw(ValueError("a number of seconds"))))
