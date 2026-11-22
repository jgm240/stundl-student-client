"""The extended admin panel: system health, services, logs, power actions, backup / restore, sessions."""
import os as _os, tempfile as _tf
_os.environ.setdefault("SSC_DATA_DIR", _tf.mkdtemp(prefix="ssc-test-"))      # before core is imported: the tests must never touch the real ~/.ssc
import http.client
import json
import os
import sys
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path

if sys.platform == "win32":
    raise unittest.SkipTest("SSC Server is POSIX only")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "server"))
import core  # noqa: E402


_SAVED = {}


def setUpModule():
    _SAVED.update(env={k: os.environ.get(k) for k in ("SSC_DATA_DIR", "SSC_VAULT")}, home=core.HOME, db=core.DB)


def tearDownModule():                                   # later test modules must see the environment they started with
    for k, v in _SAVED["env"].items():
        os.environ.pop(k, None)
        if v is not None:
            os.environ[k] = v
    core.HOME, core.DB = _SAVED["home"], _SAVED["db"]


def isolate():
    d = tempfile.mkdtemp(prefix="ssc-adm-")
    os.environ["SSC_DATA_DIR"], os.environ["SSC_VAULT"] = d, "file"
    core.HOME, core.DB = Path(d), Path(d) / "data.db"
    return d


import applycfg  # noqa: E402
import auth  # noqa: E402
import serverconf  # noqa: E402
import sysinfo  # noqa: E402


class R:
    def __init__(self, rc=0, out="", err=""):
        self.returncode, self.stdout, self.stderr = rc, out, err


class SysinfoTests(unittest.TestCase):
    def fake_root(self):
        root = Path(tempfile.mkdtemp())
        (root / "etc").mkdir()
        (root / "etc/os-release").write_text('PRETTY_NAME="Debian GNU/Linux 12 (bookworm)"\nID=debian\n')
        (root / "etc/timezone").write_text("Europe/Vienna\n")
        (root / "proc").mkdir()
        (root / "proc/uptime").write_text("93784.52 180000.10\n")
        (root / "proc/meminfo").write_text("MemTotal:        4000000 kB\nMemFree:          100000 kB\nMemAvailable:    1000000 kB\nSwapTotal: 0 kB\n")
        z = root / "sys/class/thermal/thermal_zone0"
        z.mkdir(parents=True)
        (z / "temp").write_text("61500\n")
        (z / "type").write_text("x86_pkg_temp\n")
        ps = root / "sys/class/power_supply"
        (ps / "BAT0").mkdir(parents=True)
        (ps / "BAT0/type").write_text("Battery\n")
        (ps / "BAT0/capacity").write_text("87\n")
        (ps / "BAT0/status").write_text("Charging\n")
        (ps / "AC").mkdir()
        (ps / "AC/type").write_text("Mains\n")
        (ps / "AC/online").write_text("1\n")
        return root

    def test_parsers(self):
        self.assertEqual(sysinfo.parse_os_release('NAME=x\nPRETTY_NAME="Debian 12"\n'), "Debian 12")
        self.assertEqual(sysinfo.parse_uptime("93784.52 1"), 93784.52)
        self.assertIsNone(sysinfo.parse_uptime("junk"))
        self.assertEqual(sysinfo.parse_meminfo("MemTotal: 4000000 kB\nMemAvailable: 1000000 kB\n"), {"total_mb": 3906, "used_mb": 2929, "percent": 75})
        self.assertIsNone(sysinfo.parse_meminfo("nothing"))

    def test_info_from_a_fake_machine(self):
        root = self.fake_root()
        out = sysinfo.info(root, run=lambda c: R(0, "active\nactive\ninactive\nactive\nactive\ninactive\ninactive\nfailed\n"))
        self.assertEqual((out["os"], out["timezone"], out["uptime_s"]), ("Debian GNU/Linux 12 (bookworm)", "Europe/Vienna", 93784.52))
        self.assertEqual(out["memory"]["percent"], 75)
        self.assertEqual(out["temperatures"], [{"name": "x86_pkg_temp", "celsius": 61.5}])
        self.assertEqual(out["battery"], {"percent": 87, "status": "Charging", "ac_online": True})
        states = {s["name"]: s["state"] for s in out["services"]}
        self.assertEqual((states["ssc-server"], states["ssh"], states["getty@tty1"]), ("active", "active", "failed"))
        self.assertTrue(next(s for s in out["services"] if s["name"] == "ssc-server")["restartable"])
        self.assertFalse(next(s for s in out["services"] if s["name"] == "getty@tty1")["restartable"])

    def test_a_machine_without_battery_or_sensors(self):
        out = sysinfo.info(Path(tempfile.mkdtemp()), run=lambda c: R(0, ""))
        self.assertIsNone(out["battery"])
        self.assertEqual(out["temperatures"], [])
        self.assertTrue(all(s["state"] == "unknown" for s in out["services"]))

    def test_logs_are_allow_listed_and_bounded(self):
        seen = []
        run = lambda c: seen.append(c) or R(0, "line\n")
        self.assertEqual(sysinfo.journal("web", 100, run), "line\n")
        self.assertEqual(seen[0][:3], ["journalctl", "-u", "ssc-server"])
        sysinfo.journal("web", 99999, run)
        self.assertEqual(seen[1][seen[1].index("-n") + 1], "500")                           # never more than 500 lines
        sysinfo.journal("web", 1, run)
        self.assertEqual(seen[2][seen[2].index("-n") + 1], "10")
        for bad in ("../../etc/shadow", "ssh; reboot", "", "kernel"):
            with self.assertRaises(ValueError, msg=bad):
                sysinfo.journal(bad, 10, run)
        self.assertIn("can't read", sysinfo.journal("ssh", 10, lambda c: R(1, "", "no access")))
        self.assertIn("nothing logged", sysinfo.journal("ssh", 10, lambda c: R(0, "")))


class PowerPartsTests(unittest.TestCase):
    def setUp(self):
        self.d = isolate()

    def apply(self, parts):
        seen = []
        res = applycfg.apply(parts, serverconf.validate({}), Path(self.d) / "root", lambda c: seen.append(c) or R())
        return res, seen

    def test_reboot_and_poweroff_are_delayed_and_last(self):
        res, seen = self.apply(["reboot", "web", "restart:ssh"])
        self.assertTrue(res["ok"], res)
        cmds = [" ".join(c) for c in seen]
        self.assertIn("systemd-run --no-block --on-active=5 systemctl reboot", cmds)           # delayed: the result can still be written
        self.assertLess(cmds.index("systemctl restart ssc-server"), cmds.index("systemctl restart ssh"))
        self.assertEqual(cmds[-1], "systemd-run --no-block --on-active=5 systemctl reboot")    # always the very last thing
        res, seen = self.apply(["reboot", "poweroff"])
        self.assertEqual(len([c for c in seen if c[0] == "systemd-run"]), 1)                  # one power action at most
        self.assertIn("poweroff", seen[-1])

    def test_only_allow_listed_services_restart(self):
        res, seen = self.apply(["restart:nftables"])
        self.assertTrue(res["ok"])
        res, seen = self.apply(["restart:cron"])
        self.assertEqual(seen, [])                                                             # not in the list: ignored, nothing runs

    def test_the_request_file_is_re_checked_by_the_root_helper(self):
        d = serverconf.state_dir() / "apply"
        d.mkdir(parents=True)
        (d / "request.json").write_text(json.dumps({"parts": ["restart:ssh", "restart:evil; reboot", "rm -rf /", "reboot", 5, "poweroff"]}))
        seen = []
        res = applycfg.process_request(Path(self.d) / "r", lambda c: seen.append(c) or R())
        flat = [" ".join(c) for c in seen]
        self.assertIn("systemctl restart ssh", flat)
        self.assertFalse(any("evil" in c or "rm -rf" in c for c in flat))
        self.assertEqual(len([c for c in flat if c.startswith("systemd-run")]), 1)

    def test_ssconfig_commands(self):
        import ssconfig
        ssconfig.addresses = lambda: []
        self.assertEqual(ssconfig.main(["system"]), 0)
        f = Path(self.d) / "backup.json"
        self.assertEqual(ssconfig.main(["backup", str(f)]), 0)
        self.assertEqual(oct(f.stat().st_mode & 0o777), "0o600")
        ssconfig.main(["set", "web.session_hours", "5"])
        self.assertEqual(ssconfig.main(["restore", str(f)]), 0)
        self.assertEqual(serverconf.load()["web"]["session_hours"], 12)                           # back to what the backup said
        f.write_text('{"settings": {"web": {"port": 99999}}}')
        self.assertEqual(ssconfig.main(["restore", str(f)]), 1)                                  # invalid: refused, nothing changed
        self.assertEqual(serverconf.load()["web"]["port"], 8080)
        self.assertEqual(ssconfig.main(["restore", "/no/such/file"]), 1)


class Client:
    def __init__(self, port):
        self.port, self.cookie = port, None

    def req(self, method, path, body=None):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        h = {"Content-Type": "application/json"}
        if self.cookie:
            h["Cookie"] = self.cookie
        c.request(method, path, json.dumps(body) if body is not None else None, h)
        r = c.getresponse()
        data = r.read().decode()
        sc = r.getheader("Set-Cookie")
        if sc and "ssc_sess=" in sc:
            self.cookie = sc.split(";")[0]
        return r.status, data

    def login(self, n, p):
        return self.req("POST", "/api/login", {"name": n, "password": p})[0]


class WebTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        isolate()
        auth.reset_throttle()
        import serverapp
        con = auth.db()
        auth.create("boss", "twelve-chars!", "admin", con)
        auth.create("anna", "longenoughpw", "user", con)
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), serverapp.make_handler())
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.port = cls.srv.server_address[1]

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def setUp(self):
        auth.reset_throttle()
        auth.SESSIONS.clear()                                          # every test starts with exactly its own sign-ins
        self.a, self.u = Client(self.port), Client(self.port)
        self.a.login("boss", "twelve-chars!")
        self.u.login("anna", "longenoughpw")
        self.req = serverconf.state_dir() / "apply" / "request.json"
        self.req.unlink(missing_ok=True)

    def test_everything_is_admin_only(self):
        anon = Client(self.port)
        for method, path, body in (("GET", "/api/admin/system", None), ("GET", "/api/admin/logs?unit=web", None), ("GET", "/api/admin/sessions", None), ("GET", "/api/admin/backup", None),
                                   ("POST", "/api/admin/power", {"action": "reboot"}), ("POST", "/api/admin/service", {"name": "ssh", "action": "restart"}),
                                   ("POST", "/api/admin/restore", {"settings": {}}), ("POST", "/api/admin/accounts/signout", {"name": "anna"})):
            for who in (anon, self.u):
                self.assertEqual(who.req(method, path, body)[0], 403, (method, path))
        self.assertFalse(self.req.exists())

    def test_system_logs_sessions(self):
        s, b = self.a.req("GET", "/api/admin/system")
        self.assertEqual(s, 200)
        d = json.loads(b)
        for k in ("os", "kernel", "uptime_s", "services", "time", "timezone"):
            self.assertIn(k, d)
        s, b = self.a.req("GET", "/api/admin/logs?unit=web&n=20")
        self.assertEqual((s, json.loads(b)["unit"]), (200, "web"))
        self.assertEqual(self.a.req("GET", "/api/admin/logs?unit=etc/shadow")[0], 400)
        sess = json.loads(self.a.req("GET", "/api/admin/sessions")[1])
        self.assertEqual(sorted(x["name"] for x in sess), ["anna", "boss"])
        self.assertTrue(all("id" not in x and "sid" not in x for x in sess))                      # session ids stay secret

    def test_power_and_service_actions_go_through_the_root_helper(self):
        self.assertEqual(self.a.req("POST", "/api/admin/power", {"action": "explode"})[0], 400)
        self.assertFalse(self.req.exists())
        self.assertEqual(self.a.req("POST", "/api/admin/power", {"action": "reboot"})[0], 200)
        self.assertEqual(json.loads(self.req.read_text())["parts"], ["reboot"])
        self.req.unlink()
        self.assertEqual(self.a.req("POST", "/api/admin/service", {"name": "cron", "action": "restart"})[0], 400)
        self.assertEqual(self.a.req("POST", "/api/admin/service", {"name": "ssh", "action": "stop"})[0], 400)
        self.assertFalse(self.req.exists())
        self.assertEqual(self.a.req("POST", "/api/admin/service", {"name": "ssh", "action": "restart"})[0], 200)
        self.assertEqual(json.loads(self.req.read_text())["parts"], ["restart:ssh"])
        events = [e["event"] for e in json.loads(self.a.req("GET", "/api/admin/audit")[1])]
        self.assertIn("power-reboot", events)
        self.assertIn("service-restart", events)

    def test_backup_and_restore(self):
        s, b = self.a.req("GET", "/api/admin/backup")
        data = json.loads(b)
        self.assertEqual((s, data["ssc_backup"]), (200, 1))
        text = json.dumps(data["settings"]).lower()
        for secret in ("psk", "passphrase", "hash", "secret", "token", "key\""):
            self.assertNotIn(secret, text, secret)                                                    # the settings hold no secrets
        self.assertNotIn("password", text.replace("password_auth", "").replace("guest_passwordless", ""))
        self.a.req("POST", "/api/admin/config", {"web": {"session_hours": 3}, "power": {"lid": "suspend"}})
        self.req.unlink(missing_ok=True)
        s, b = self.a.req("POST", "/api/admin/restore", {"settings": data["settings"]})
        self.assertEqual(s, 200)
        now = json.loads(self.a.req("GET", "/api/admin/config")[1])
        self.assertEqual((now["web"]["session_hours"], now["power"]["lid"]), (data["settings"]["web"]["session_hours"], data["settings"]["power"]["lid"]))
        self.assertIn("power", json.loads(self.req.read_text())["parts"])                          # the system part is applied by the root helper
        self.assertEqual(self.a.req("POST", "/api/admin/restore", {"settings": {"web": {"port": 0}}})[0], 400)
        self.assertEqual(self.a.req("POST", "/api/admin/restore", {"settings": "nope"})[0], 400)
        self.assertEqual(json.loads(self.a.req("GET", "/api/admin/config")[1])["web"]["port"], data["settings"]["web"]["port"])   # a refused restore changes nothing

    def test_sign_out_everywhere(self):
        self.assertEqual(self.u.req("GET", "/data.json")[0], 200)
        s, b = self.a.req("POST", "/api/admin/accounts/signout", {"name": "anna"})
        self.assertEqual((s, json.loads(b)["closed"]), (200, 1))
        self.assertEqual(self.u.req("GET", "/data.json")[0], 401)
        self.assertEqual(self.a.req("GET", "/api/admin/sessions")[0], 200)                        # the admin is still in

    def test_the_panel_has_the_new_tab(self):
        html = (ROOT / "server/admin.html").read_text(encoding="utf-8")
        for needle in ('"System"', "powerAct('reboot')", "powerAct('poweroff')", "/api/admin/logs", "downloadBackup", "restoreBackup", "signOutAll", "svcRestart", "waitBack"):
            self.assertIn(needle, html, needle)


if __name__ == "__main__":
    unittest.main()
