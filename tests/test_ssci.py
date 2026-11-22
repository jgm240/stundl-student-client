"""autoinstall.ssci (SSC Install file), the shared installer questions, sscigen and the installer's auto flow."""
import os as _os, tempfile as _tf
_os.environ.setdefault("SSC_DATA_DIR", _tf.mkdtemp(prefix="ssc-test-"))      # before core is imported: the tests must never touch the real ~/.ssc
import json
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path

if sys.platform == "win32":
    raise unittest.SkipTest("the installer and its tools are POSIX only")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "server"))
import install_plan as plan  # noqa: E402
import qa  # noqa: E402
import serverconf  # noqa: E402
import ssci_file as sf  # noqa: E402
import sscigen  # noqa: E402

DISKS = [{"path": "/dev/sda", "size": 64 * 10 ** 9, "model": "X", "removable": False}]
FULL = """# a complete file
erase = yes
wait = 0
disk = auto
hostname = kiosk
admin = Boss
password = "secret #1 pass"
timezone = Europe/Vienna
keymap = de
ssh = yes
autologin = no
network = dhcp
"""


class AutoUI:
    """Answers the installer's questions without a person: by default 'the default', or what `says` has for a matching text."""
    kind = "auto"

    def __init__(self, says=None):
        self.says, self.asked = says or {}, []

    def _find(self, kind, text, default):
        self.asked.append((kind, text))
        for needle, val in self.says.items():
            if needle in text:
                return val
        return default

    def msgbox(self, text):
        self.asked.append(("msg", text))

    def inputbox(self, text, default=""):
        v = self._find("input", text, default)
        if v is qa.Cancelled:
            raise qa.Cancelled()
        return v

    def passwordbox(self, text):
        return self._find("password", text, "long-password-1")

    def yesno(self, text, default=True):
        return self._find("yesno", text, default)

    def menu(self, text, rows):
        return self._find("menu", text, rows[0][0])


class ParseTests(unittest.TestCase):
    def test_plain_text_format(self):
        d = sf.parse('# comment\n; also a comment\n\nErase = yes\nPassword = "secret #1 pass"\nhostname=kiosk\n[wifi]\nssid = Home\npassword = pw\n')
        self.assertEqual(d, {"erase": "yes", "password": "secret #1 pass", "hostname": "kiosk", "wifi.ssid": "Home", "wifi.password": "pw"})

    def test_errors_name_the_line(self):
        for text, needle in (("a = 1\nthis is not a setting\n", "line 2"), ("x = 1\nx = 2\n", "line 2"), ("bad key! = 1\n", "line 1"), ("{not json", "JSON")):
            with self.assertRaises(sf.SsciError) as e:
                sf.parse(text)
            self.assertIn(needle, str(e.exception))
        with self.assertRaises(sf.SsciError):
            sf.parse("a = " + "x" * 70000)

    def test_json_is_accepted_and_flattened(self):
        d = sf.parse(json.dumps({"erase": True, "disk": "auto", "network": {"mode": "static", "address": "10.0.0.5/24"}, "wifi": {"ssid": "H", "passphrase": "p"}, "dns": ["1.1.1.1", "9.9.9.9"]}))
        self.assertEqual((d["erase"], d["network"], d["address"], d["wifi.password"], d["dns"]), ("yes", "static", "10.0.0.5/24", "p", "1.1.1.1, 9.9.9.9"))

    def test_the_file_is_data_never_code(self):
        evil = "__import__('os').system('touch /tmp/pwned')"
        d = sf.parse(f"hostname = {evil}\n")
        self.assertEqual(d["hostname"], evil)                                   # just a string ...
        with self.assertRaises(ValueError):
            plan.validate_answers({"hostname": evil, "password": "long-password-1"}, DISKS)   # ... which the hostname check refuses
        with self.assertRaises(sf.SsciError):
            sf.parse(f"{evil} = 1\n")
        src = (ROOT / "server/ssci_file.py").read_text()
        for needle in ("eval(", "exec(", "__import__", "compile(", "pickle", "yaml.load"):
            self.assertNotIn(needle, src.replace('"__import__"', ""), needle)


class InterpretTests(unittest.TestCase):
    def test_complete_file(self):
        a, x = sf.load(FULL)
        self.assertEqual((x["erase"], x["wait"], x["finish"], x["ask"]), (True, 0, "poweroff", True))
        self.assertEqual((a["admin"], a["password"], a["ssh"], a["autologin"], a["network"]), ("Boss", "secret #1 pass", True, False, {"mode": "dhcp"}))
        ans = plan.validate_answers(a, DISKS)
        self.assertEqual((ans["admin"], ans["disk"], ans["hostname"], ans["timezone"]), ("boss", "/dev/sda", "kiosk", "Europe/Vienna"))

    def test_server_settings_are_coerced_and_checked(self):
        a, _ = sf.load("power.lid = suspend\nschedule.enabled = yes\nschedule.days = mon, tue\nweb.port = 8081\nlinks.long = on\nsections.guest = today, homework\n")
        s = a["settings"]
        self.assertEqual((s["power"]["lid"], s["schedule"]["enabled"], s["schedule"]["days"], s["web"]["port"], s["links"]["long"]), ("suspend", True, ["mon", "tue"], 8081, True))
        ans = plan.validate_answers({**a, "password": "long-password-1"}, DISKS)
        self.assertEqual(ans["settings"]["web"]["port"], 8081)
        for bad in ("power.lid = sideways\n", "web.port = 99999\n", "schedule.off = 25:00\nschedule.enabled = yes\n"):
            a2, _ = sf.load(bad)
            with self.assertRaises(ValueError, msg=bad):
                plan.validate_answers({**a2, "password": "long-password-1"}, DISKS)

    def test_unknown_settings_and_typos_are_errors(self):
        for text, name in (("hostnme = x\n", "hostnme"), ("web.nonsense = 1\n", "web.nonsense"), ("bogus.key = 1\n", "bogus.key"), ("web.port = many\n", "web.port")):
            with self.assertRaises(sf.SsciError) as e:
                sf.load(text)
            self.assertIn(name, str(e.exception))
        for text in ("ssh = maybe\n", "erase = perhaps\n", "wait = soon\n", "finish = explode\n", "account.anna = nopassword\n"):
            with self.assertRaises(sf.SsciError, msg=text):
                sf.load(text)

    def test_defaults_are_safe(self):
        _, x = sf.load("hostname = a\n")
        self.assertEqual((x["erase"], x["ask"], x["wait"], x["finish"]), (False, True, 15, "poweroff"))      # nothing is erased unless the file says so
        _, x = sf.load("wait = 99999\n")
        self.assertEqual(x["wait"], 600)

    def test_accounts_wifi_and_untis(self):
        link = "untis://setschool?url=a.webuntis.com&school=s&user=u&key=ABCDEFGH"
        a, _ = sf.load(f"account.anna = user:her pass\naccount.gast = guest:guestpass1\nwifi.ssid = Home\nwifi.password = correct horse\nwifi.country = at\nuntis = {link}\n")
        ans = plan.validate_answers({**a, "password": "long-password-1"}, DISKS)
        self.assertEqual(ans["accounts"], [("anna", "user", "her pass"), ("gast", "guest", "guestpass1")])
        self.assertEqual(ans["wifi"]["psk"], serverconf.derive_psk("Home", "correct horse"))
        self.assertEqual(ans["untis"]["user"], "u")
        for bad in ({"account.x1": "root:longenough"}, {"account.anna": "user:short"}, {"untis": "untis://setschool?url=a.webuntis.com"}):
            a2, _ = sf.load("\n".join(f"{k} = {v}" for k, v in bad.items()))
            with self.assertRaises(ValueError, msg=str(bad)):
                plan.validate_answers({**a2, "password": "long-password-1"}, DISKS)
        a3, _ = sf.load("admin = boss\naccount.boss = user:longenough\n")
        with self.assertRaises(ValueError):
            plan.validate_answers({**a3, "password": "long-password-1"}, DISKS)                          # same name as the admin

    def test_console_autologin_must_be_the_admin(self):
        a, _ = sf.load("admin = boss\nconsole.autologin_user = max\n")
        with self.assertRaises(ValueError):
            plan.validate_answers({**a, "password": "long-password-1"}, DISKS)
        a, _ = sf.load("admin = boss\nautologin = yes\n")
        self.assertEqual(plan.validate_answers({**a, "password": "long-password-1"}, DISKS)["settings"]["console"]["autologin_user"], "boss")


class DumpTests(unittest.TestCase):
    def test_round_trip(self):
        a = {"disk": "/dev/sda", "admin": "boss", "password": '  odd "pass" #x ', "hostname": "k", "timezone": "UTC", "keymap": "de", "ssh": True, "autologin": False,
             "network": {"mode": "static", "address": "10.0.0.5/24", "gateway": "10.0.0.1", "dns": "1.1.1.1"}, "wifi": {"ssid": "My Net", "passphrase": "pw pw pw pw", "country": "AT", "open": False},
             "accounts": [("anna", "user", "her pass")], "untis": "untis://setschool?url=a.webuntis.com&school=s&user=u&key=ABCDEFGH",
             "settings": {"power": {"lid": "suspend"}, "schedule": {"enabled": True, "off": "22:00", "on": "06:00", "days": ["mon", "fri"]}, "links": {"long": True}}}
        text = sf.dump(a, {"erase": True, "wait": 7, "finish": "reboot"})
        a2, x2 = sf.load(text)
        self.assertEqual((x2["erase"], x2["wait"], x2["finish"]), (True, 7, "reboot"))
        for k in ("disk", "admin", "password", "hostname", "timezone", "keymap", "ssh", "autologin", "network", "untis"):
            self.assertEqual(a2[k], a[k], k)
        self.assertEqual(a2["accounts"], a["accounts"])
        self.assertEqual(a2["wifi"]["passphrase"], "pw pw pw pw")
        self.assertEqual(a2["settings"]["schedule"]["days"], ["mon", "fri"])
        self.assertEqual(a2["settings"]["power"]["lid"], "suspend")
        with self.assertRaises(sf.SsciError):
            sf.dump({"password": "line\nbreak"}, {})

    def test_written_file_is_private(self):
        f = Path(tempfile.mkdtemp()) / "autoinstall.ssci"
        sf.write(f, "x = 1\n")
        self.assertEqual(stat.S_IMODE(f.stat().st_mode), 0o600)                                           # it holds passwords


class FindTests(unittest.TestCase):
    TREE = json.dumps({"blockdevices": [
        {"name": "/dev/sda", "fstype": None, "type": "disk", "children": [{"name": "/dev/sda1", "fstype": "ext4", "type": "part", "mountpoint": None}]},
        {"name": "/dev/sdb", "fstype": None, "type": "disk", "children": [{"name": "/dev/sdb1", "fstype": "vfat", "type": "part"}, {"name": "/dev/sdb2", "fstype": "swap", "type": "part"}]},
        {"name": "/dev/sdc", "fstype": "ext4", "type": "disk"},
        {"name": "/dev/loop0", "fstype": "squashfs", "type": "loop", "mountpoint": "/usr/lib/live/mount/rootfs"},
        {"name": "/dev/sr0", "fstype": "iso9660", "type": "rom", "mountpoint": "/run/live/medium"}]})

    def test_candidates(self):
        c = sf.candidates(self.TREE)
        self.assertEqual(c, [("/dev/sda1", "ext4", "/dev/sda"), ("/dev/sdb1", "vfat", "/dev/sdb"), ("/dev/sdc", "ext4", "/dev/sdc")])          # no swap, squashfs, optical
        self.assertEqual([x[0] for x in sf.candidates(self.TREE, skip=("/dev/sda1",))], ["/dev/sdb1", "/dev/sdc"])

    def test_reading_the_file(self):
        d = Path(tempfile.mkdtemp())
        self.assertIsNone(sf.read_from(d))
        (d / "Autoinstall.SSCI").write_text("erase = yes\n")                                                # FAT is case-insensitive
        self.assertEqual(sf.read_from(d), "erase = yes\n")
        d2 = Path(tempfile.mkdtemp())
        (d2 / "ssc").mkdir()
        (d2 / "ssc" / "autoinstall.ssci").write_text("x = 1\n")
        self.assertEqual(sf.read_from(d2), "x = 1\n")
        d3 = Path(tempfile.mkdtemp())
        (d3 / "autoinstall.ssci.txt").write_text("no")
        (d3 / "notes.ssci").write_text("no")
        self.assertIsNone(sf.read_from(d3))                                                              # exact name only


class QuestionTests(unittest.TestCase):
    def test_only_missing_questions_are_asked(self):
        ui = AutoUI()
        a, x = sf.load("erase = yes\ndisk = auto\nadmin = boss\npassword = long-password-1\nnetwork = dhcp\nssh = yes\nautologin = no\nkeymap = de\n")
        out, extras = qa.interview(ui, a, disks=DISKS, erase_given=x["erase"])
        asked = " | ".join(t for _, t in ui.asked)
        self.assertIn("Name of this server", asked)                                  # hostname and time zone are missing: asked
        self.assertIn("Time zone", asked)
        for gone in ("Choose", "Administrator password", "Keyboard layout", "Turn on SSH", "Log in automatically", "Network", "ERASE"):
            self.assertNotIn(gone, asked, gone)                                       # everything the file said is not asked again
        self.assertEqual((out["admin"], out["keymap"], out["hostname"], out["timezone"]), ("boss", "de", "ssc-server", "UTC"))

    def test_a_complete_preset_asks_nothing(self):
        ui = AutoUI()
        a, x = sf.load(FULL)
        qa.interview(ui, a, disks=DISKS, erase_given=True)
        self.assertEqual([k for k, _ in ui.asked if k != "msg"], [])

    def test_wrong_answers_are_asked_again(self):
        answers = iter(["bad host!", "also bad", "good-name"])
        ui = AutoUI()
        ui._find = lambda kind, text, default: next(answers) if "Name of this server" in text else default
        out, _ = qa.interview(ui, {"disk": "auto", "erase": True}, disks=None, file_mode=False)
        self.assertEqual(out["hostname"], "good-name")
        self.assertEqual(len([1 for k, t in ui.asked if k == "msg" and "doesn't work" in t]), 2)

    def test_installer_confirms_the_erase_unless_the_file_did(self):
        ui = AutoUI({"ERASE": "nope"})
        with self.assertRaises(qa.Cancelled):
            qa.interview(ui, {"disk": "/dev/sda"}, disks=DISKS)
        ui = AutoUI({"ERASE": "ERASE"})
        self.assertTrue(qa.interview(ui, {"disk": "/dev/sda"}, disks=DISKS)[1]["erase"])

    def test_file_mode_asks_the_extras(self):
        ui = AutoUI({"Add another account": False, "WebUntis": False, "sleep schedule": True, "Switch off at": "23:00", "Switch back on at": "07:00", "long Parent": True,
                     "laptop lid": "suspend", "countdown": "5", "installation is done": "reboot"})
        a, x = qa.interview(ui, None, disks=None, file_mode=True)
        self.assertEqual((a["settings"]["power"]["lid"], a["settings"]["schedule"]["off"], a["settings"]["links"]["long"]), ("suspend", "23:00", True))
        self.assertEqual((x["wait"], x["finish"], x["erase"]), (5, "reboot", False))                # erase asked as a question, default no

    def test_static_network_and_wifi(self):
        ui = AutoUI({"Network": "static", "Address with prefix": "10.0.0.9/24", "Gateway": "10.0.0.1", "Connect this server to a Wi-Fi": True, "Wi-Fi network name": "Home"})
        a, _ = qa.interview(ui, {"disk": "auto"}, disks=None, file_mode=False, wifi_card=True)
        self.assertEqual((a["network"]["mode"], a["network"]["address"], a["wifi"]["ssid"]), ("static", "10.0.0.9/24", "Home"))

    def test_cancelling_stops_everything(self):
        with self.assertRaises(qa.Cancelled):
            qa.interview(AutoUI({"Name of this server": qa.Cancelled}), {"disk": "auto"}, disks=None)


class SscigenTests(unittest.TestCase):
    def test_check_reports_what_is_wrong_or_missing(self):
        self.assertEqual(sscigen.check_text(FULL)[0], [])
        problems, _ = sscigen.check_text(FULL.replace("erase = yes", "erase = no"))
        self.assertTrue(any("erase = yes" in p for p in problems))                        # a complete file without consent can't install unattended
        problems, _ = sscigen.check_text(FULL.replace('"secret #1 pass"', "abc"))
        self.assertTrue(problems)                                                          # password too short
        problems, _ = sscigen.check_text("hostnme = x\n")
        self.assertIn("hostnme", problems[0])
        problems, notes = sscigen.check_text("erase = yes\nadmin = boss\npassword = long-password-1\n")
        self.assertEqual(problems, [])
        self.assertTrue(any("not in the file" in n and "hostname" in n for n in notes))   # the rest is asked on screen

    def test_it_writes_a_file_the_installer_accepts(self):
        out = Path(tempfile.mkdtemp()) / "autoinstall.ssci"
        ui = AutoUI({"Let the installer erase": True, "Add another account": False, "WebUntis": False, "sleep schedule": False, "long Parent": False, "countdown": "3"})
        old = qa.make_ui
        qa.make_ui = lambda *a, **k: ui
        try:
            self.assertEqual(sscigen.main(["-o", str(out)]), 0)
        finally:
            qa.make_ui = old
        text = out.read_text()
        self.assertEqual(stat.S_IMODE(out.stat().st_mode), 0o600)
        self.assertIn("erase = yes", text)
        self.assertEqual(sscigen.check_text(text)[0], [])
        self.assertEqual(sscigen.main(["--check", str(out)]), 0)
        a, x = sf.load(text)
        self.assertEqual(plan.validate_answers(a, DISKS)["admin"], "admin")

    def test_edit_keeps_what_is_there(self):
        src = Path(tempfile.mkdtemp()) / "old.ssci"
        src.write_text("hostname = keepme\nadmin = boss\npassword = long-password-1\nerase = yes\n")
        out = src.parent / "new.ssci"
        ui = AutoUI({"Add another account": False, "WebUntis": False, "sleep schedule": False, "long Parent": False})
        old = qa.make_ui
        qa.make_ui = lambda *a, **k: ui
        try:
            self.assertEqual(sscigen.main(["--edit", str(src), "-o", str(out)]), 0)
        finally:
            qa.make_ui = old
        self.assertIn("hostname = keepme", out.read_text())
        self.assertNotIn("Name of this server", " ".join(t for _, t in ui.asked))

    def test_cancel_writes_nothing(self):
        out = Path(tempfile.mkdtemp()) / "x.ssci"
        old = qa.make_ui
        qa.make_ui = lambda *a, **k: AutoUI({"Disk to install on": qa.Cancelled})
        try:
            self.assertEqual(sscigen.main(["-o", str(out)]), 1)
        finally:
            qa.make_ui = old
        self.assertFalse(out.exists())


class AutoInstallFlowTests(unittest.TestCase):
    def setUp(self):
        import ssc_install as si
        self.si = si
        self.calls = {"finish": [], "ask": []}
        self.saved = (si.find_ssci, si.finish, si.ask, si.countdown)
        si.finish = lambda raw, disks, run, interactive=False, action="poweroff": self.calls["finish"].append((raw, [d["path"] for d in disks], interactive, action)) or 0
        si.countdown = lambda *a, **k: self.calls.setdefault("countdown", []).append(a)

    def tearDown(self):
        self.si.find_ssci, self.si.finish, self.si.ask, self.si.countdown = self.saved

    def found(self, text, disk="/dev/sdz"):
        self.si.find_ssci = lambda *a, **k: (text, "/dev/sdz1", disk)

    def test_complete_file_with_consent_installs_after_a_countdown(self):
        self.found(FULL.replace("wait = 0", "wait = 20") + "finish = reboot\n")
        self.assertEqual(self.si.auto(self.si.Runner(dry=True), DISKS), 0)
        self.assertEqual(len(self.calls["finish"]), 1)
        self.assertEqual((self.calls["finish"][0][2], self.calls["finish"][0][3]), (False, "reboot"))
        self.assertEqual(self.calls["countdown"][0][0], 20)

    def test_without_consent_nothing_happens(self):
        self.found(FULL.replace("erase = yes", "erase = no"))
        self.assertEqual(self.si.auto(self.si.Runner(dry=True), DISKS), 1)
        self.assertEqual(self.calls["finish"], [])
        self.found(FULL.replace("erase = yes\n", ""))
        self.assertEqual(self.si.auto(self.si.Runner(dry=True), DISKS), 1)
        self.assertEqual(self.calls["finish"], [])

    def test_missing_settings_are_asked(self):
        self.found("erase = yes\nadmin = boss\npassword = long-password-1\n")
        self.si.ask = lambda disks, preset=None, erase_given=False: self.calls["ask"].append((preset, erase_given)) or ({**preset, "hostname": "asked-name"}, {"erase": True})
        self.assertEqual(self.si.auto(self.si.Runner(dry=True), DISKS), 0)
        preset, erase_given = self.calls["ask"][0]
        self.assertEqual((preset["admin"], erase_given), ("boss", True))
        self.assertEqual((self.calls["finish"][0][0]["hostname"], self.calls["finish"][0][2]), ("asked-name", True))       # interactive finish
        self.assertNotIn("countdown", self.calls)                                                                           # somebody is there: no countdown

    def test_ask_no_uses_defaults_but_still_needs_consent(self):
        self.found("ask = no\nadmin = boss\npassword = long-password-1\nerase = yes\nwait = 0\n")
        self.assertEqual(self.si.auto(self.si.Runner(dry=True), DISKS), 0)
        self.assertEqual(self.calls["ask"], [])
        self.found("ask = no\nadmin = boss\npassword = long-password-1\n")
        self.assertEqual(self.si.auto(self.si.Runner(dry=True), DISKS), 1)

    def test_the_stick_the_file_came_from_is_never_erased(self):
        two = DISKS + [{"path": "/dev/sdz", "size": 64 * 10 ** 9, "model": "stick", "removable": True}]
        self.found(FULL, disk="/dev/sdz")
        self.assertEqual(self.si.auto(self.si.Runner(dry=True), two), 0)
        self.assertEqual(self.calls["finish"][0][1], ["/dev/sda"])                                                          # with disk = auto this is why only one is left
        self.calls["finish"].clear()
        self.found(FULL.replace("disk = auto", "disk = /dev/sdz"), disk="/dev/sdz")
        self.assertEqual(self.si.auto(self.si.Runner(dry=True), two), 1)                                                    # naming the stick itself: refused
        self.assertEqual(self.calls["finish"], [])

    def test_a_broken_file_and_no_file(self):
        self.found("this is not valid\n")
        self.assertEqual(self.si.auto(self.si.Runner(dry=True), DISKS), 1)
        self.si.find_ssci = lambda *a, **k: None
        self.assertEqual(self.si.auto(self.si.Runner(dry=True), DISKS), 3)
        def two(*a, **k):
            raise sf.SsciError("found more than one autoinstall.ssci")
        self.si.find_ssci = two
        self.assertEqual(self.si.auto(self.si.Runner(dry=True), DISKS), 1)

    def test_install_creates_extra_accounts_settings_and_the_untis_key(self):
        link = "untis://setschool?url=a.webuntis.com&school=s&user=u&key=ABCDEFGH"
        a, _ = sf.load(FULL + f"account.anna = user:her-password\npower.lid = suspend\nuntis = {link}\nschedule.enabled = yes\n")
        ans = plan.validate_answers(a, DISKS)
        log = []
        self.si.install(ans, self.si.Runner(dry=True, log=log.append), root=Path(tempfile.mkdtemp()), efi=False, a="amd64")
        text = "\n".join(log)
        self.assertIn("ssconfig.py account add boss admin", text)
        self.assertIn("ssconfig.py account add anna user", text)
        self.assertIn("untis_direct", text)
        self.assertNotIn("her-password", text)                                                                              # passwords are not echoed
        self.assertIn('"lid": "suspend"', text)


class WiringTests(unittest.TestCase):
    def test_the_service_has_a_screen_and_does_not_fail_when_there_is_no_file(self):
        u = (ROOT / "server/iso/overlay/etc/systemd/system/ssc-autoinstall.service").read_text()
        self.assertIn("StandardInput=tty", u)
        self.assertIn("SuccessExitStatus=3", u)
        self.assertNotIn("SSCANS", u)
        self.assertIn("ConditionKernelCommandLine=!ssc.noautoinstall", u)

    def test_commands_exist_in_the_image(self):
        for c in ("sscigen", "ssc-install"):
            f = ROOT / "server/iso/overlay/usr/local/bin" / c
            self.assertTrue(f.exists() and os.access(f, os.X_OK), c)
        self.assertTrue(os.access(ROOT / "sscigen", os.X_OK))


if __name__ == "__main__":
    unittest.main()
