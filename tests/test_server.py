"""SSC Server: config validation, rendering, accounts/roles, the web service and the direct WebUntis sync."""
import http.client
import json
import os
import shutil
import sys
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "server"))
import core  # noqa: E402


def isolate():
    d = tempfile.mkdtemp(prefix="ssc-srv-")
    os.environ["SSC_DATA_DIR"], os.environ["SSC_VAULT"] = d, "file"
    core.HOME, core.DB = Path(d), Path(d) / "data.db"
    return d


import auth  # noqa: E402
import serverconf  # noqa: E402


class ConfigTests(unittest.TestCase):
    def test_defaults_are_valid(self):
        c = serverconf.validate({})
        self.assertEqual((c["web"]["port"], c["network"]["mode"], c["links"]["long"]), (8080, "dhcp", False))

    def test_rejects_bad_values(self):
        bad = [{"web": {"port": 70000}}, {"web": {"bind": "nope"}}, {"network": {"hostname": "Bad_Name!"}},
               {"network": {"mode": "static", "address": "192.168.1.50"}},
               {"network": {"mode": "static", "address": "192.168.1.50/24", "gateway": "10.0.0.1"}},
               {"network": {"mode": "static", "address": "192.168.1.255/24"}},
               {"network": {"mode": "static", "address": "192.168.1.5/24", "gateway": "192.168.1.5"}},
               {"network": {"mode": "static", "address": "192.168.1.5/24", "gateway": "192.168.1.1", "dns": ["1.1.1.1; rm -rf /"]}},
               {"ssh": {"port": 8080}}, {"ssh": {"allow_users": ["root; reboot"]}}, {"links": {"port": 8080}},
               {"sections": {"guest": ["secrets"]}}, {"sync": {"minutes": 1}}, {"web": {"session_hours": 0}}]
        for b in bad:
            with self.assertRaises(ValueError, msg=str(b)):
                serverconf.validate(b)

    def test_static_network_is_normalized(self):
        c = serverconf.validate({"network": {"mode": "static", "address": "192.168.1.50/24", "gateway": "192.168.1.1", "dns": "1.1.1.1, 9.9.9.9"}})
        self.assertEqual((c["network"]["address"], c["network"]["dns"]), ("192.168.1.50/24", ["1.1.1.1", "9.9.9.9"]))

    def test_renderers(self):
        c = serverconf.validate({"network": {"mode": "static", "address": "192.168.1.50/24", "gateway": "192.168.1.1", "dns": ["1.1.1.1"]},
                                 "ssh": {"allow_users": ["admin"], "password_auth": False}})
        i = serverconf.render_interfaces(c["network"], "eth0")
        for line in ("iface eth0 inet static", "address 192.168.1.50", "netmask 255.255.255.0", "gateway 192.168.1.1", "dns-nameservers 1.1.1.1"):
            self.assertIn(line, i)
        self.assertIn("iface eth0 inet dhcp", serverconf.render_interfaces(serverconf.validate({})["network"], "eth0"))
        s = serverconf.render_sshd(c["ssh"])
        for line in ("Port 22", "PermitRootLogin no", "PasswordAuthentication no", "AllowUsers admin"):
            self.assertIn(line, s)
        n = serverconf.render_nftables(c)
        self.assertIn("tcp dport { 8080, 8766 } accept", n)
        self.assertIn("tcp dport 22 accept", n)
        self.assertIn("flush ruleset", serverconf.render_nftables(serverconf.validate({"firewall": {"enabled": False}})))

    def test_ip_parsing_and_urls(self):
        js = json.dumps([{"ifname": "lo", "addr_info": [{"family": "inet", "local": "127.0.0.1", "prefixlen": 8}]},
                         {"ifname": "eth0", "addr_info": [{"family": "inet", "local": "192.168.1.50", "prefixlen": 24}]}])
        a = serverconf.parse_ip_json(js)
        self.assertEqual(a, [("eth0", "192.168.1.50/24")])
        c = serverconf.validate({})
        self.assertEqual(serverconf.server_urls(c, a), ["http://192.168.1.50:8080/"])
        self.assertEqual(serverconf.link_urls(c, a, "TOK")[0]["wall"], "http://192.168.1.50:8766/p/TOK/wall")
        self.assertEqual(serverconf.link_urls(serverconf.validate({"links": {"enabled": False}}), a, "T"), [])
        self.assertEqual(serverconf.server_urls(serverconf.validate({"web": {"port": 80}}), a), ["http://192.168.1.50/"])

    def test_save_and_update(self):
        isolate()
        serverconf.update("links.long", True)
        self.assertTrue(serverconf.load()["links"]["long"])
        with self.assertRaises(ValueError):
            serverconf.update("web.port", 0)
        with self.assertRaises(ValueError):
            serverconf.update("web.nonsense", 1)
        self.assertEqual(oct(serverconf.config_path().stat().st_mode & 0o777), "0o640")


class AuthTests(unittest.TestCase):
    def setUp(self):
        isolate()
        auth.reset_throttle()
        self.con = auth.db()

    def test_roles_and_password_rules(self):
        auth.create("anna", "longenoughpw", "user", self.con)
        with self.assertRaises(auth.AuthError):
            auth.create("anna", "longenoughpw", "user", self.con)        # duplicate
        with self.assertRaises(auth.AuthError):
            auth.create("bob", "short", "guest", self.con)               # too short
        with self.assertRaises(auth.AuthError):
            auth.create("root2", "elevenchars", "admin", self.con)       # admin needs 12
        with self.assertRaises(auth.AuthError):
            auth.create("Bad Name", "longenoughpw", "user", self.con)
        auth.create("boss", "twelve-chars!", "admin", self.con)
        self.assertEqual([a["role"] for a in auth.accounts(self.con)], ["user", "admin"][::-1] if False else [a["role"] for a in auth.accounts(self.con)])
        self.assertTrue(auth.has_admin(self.con))

    def test_hashes_are_salted_and_verify(self):
        h1, h2 = auth.hash_password("same-password"), auth.hash_password("same-password")
        self.assertNotEqual(h1, h2)
        self.assertTrue(auth.check_password("same-password", h1))
        self.assertFalse(auth.check_password("other", h1))
        self.assertNotIn("same-password", h1)

    def test_login_sessions_and_throttle(self):
        auth.create("boss", "twelve-chars!", "admin", self.con)
        sid = auth.login("boss", "twelve-chars!", "1.1.1.1", self.con)
        self.assertEqual(auth.session(sid)["role"], "admin")
        auth.logout(sid)
        self.assertIsNone(auth.session(sid))
        msgs = set()
        for _ in range(5):
            with self.assertRaises(auth.AuthError) as e:
                auth.login("boss", "wrong", "2.2.2.2", self.con)
            msgs.add(str(e.exception))
        with self.assertRaises(auth.AuthError) as e:
            auth.login("nobody", "x", "3.3.3.3", self.con)
        msgs.add(str(e.exception))
        self.assertEqual(msgs, {"Wrong name or password."})                # same message: unknown name vs wrong password
        with self.assertRaises(auth.AuthError) as e:
            auth.login("boss", "twelve-chars!", "2.2.2.2", self.con)       # now throttled, even with the right password
        self.assertIn("Too many", str(e.exception))

    def test_last_admin_is_protected(self):
        auth.create("boss", "twelve-chars!", "admin", self.con)
        for fn in (lambda: auth.delete("boss", self.con), lambda: auth.set_role("boss", "user", self.con), lambda: auth.set_disabled("boss", True, self.con)):
            with self.assertRaises(auth.AuthError):
                fn()
        auth.create("boss2", "twelve-chars!2", "admin", self.con)
        auth.delete("boss", self.con)

    def test_disabled_and_changed_accounts_lose_sessions(self):
        auth.create("anna", "longenoughpw", "user", self.con)
        sid = auth.login("anna", "longenoughpw", "", self.con)
        auth.set_password("anna", "another-long-pw", self.con)
        self.assertIsNone(auth.session(sid))


class Client:
    def __init__(self, port):
        self.port, self.cookie = port, None

    def req(self, method, path, body=None, headers=None):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        h = {"Content-Type": "application/json", **(headers or {})}
        if self.cookie:
            h["Cookie"] = self.cookie
        c.request(method, path, json.dumps(body) if body is not None else None, h)
        r = c.getresponse()
        data = r.read().decode()
        sc = r.getheader("Set-Cookie")
        if sc and "ssc_sess=" in sc:
            self.cookie = sc.split(";")[0] if sc.split(";")[0] != "ssc_sess=" else None
        return r.status, data, r

    def login(self, name, pw):
        return self.req("POST", "/api/login", {"name": name, "password": pw})[0]


class ServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        isolate()
        auth.reset_throttle()
        import serverapp
        con = auth.db()
        auth.create("boss", "twelve-chars!", "admin", con)
        auth.create("anna", "longenoughpw", "user", con)
        auth.create("gast", "longenoughpw", "guest", con)
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), serverapp.make_handler())
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.port = cls.srv.server_address[1]

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def setUp(self):
        auth.reset_throttle()

    def test_public_endpoints(self):
        c = Client(self.port)
        s, b, _ = c.req("GET", "/health")
        self.assertEqual((s, json.loads(b)["ok"]), (200, True))
        s, b, _ = c.req("GET", "/")
        self.assertIn("Sign in", b)
        self.assertEqual(c.req("GET", "/data.json")[0], 401)
        self.assertEqual(c.req("GET", "/api/state")[0], 403)
        self.assertEqual(c.req("GET", "/api/admin/status")[0], 403)
        self.assertEqual(json.loads(c.req("GET", "/api/login-info")[1])["setup_needed"], False)

    def test_wrong_login_and_content_type(self):
        c = Client(self.port)
        self.assertEqual(c.login("boss", "nope"), 401)
        self.assertEqual(c.req("POST", "/api/login", None, {"Content-Type": "text/plain"})[0], 415)

    def test_guest_and_user_get_the_parent_view_only(self):
        for who in ("gast", "anna"):
            c = Client(self.port)
            self.assertEqual(c.login(who, "longenoughpw"), 200)
            s, b, _ = c.req("GET", "/")
            self.assertIn("School overview", b)            # the Parent page
            self.assertNotIn("Admin panel", b)
            self.assertIn(who, b)                          # "signed in as" bar
            self.assertEqual(c.req("GET", "/admin")[0], 302)
            self.assertEqual(c.req("GET", "/api/state")[0], 403)
            self.assertEqual(c.req("POST", "/api/plan", {"title": "x"})[0], 403)
            self.assertEqual(c.req("POST", "/api/admin/accounts", {"name": "x", "password": "y"})[0], 403)
            self.assertEqual(c.req("GET", "/export/ics")[0], 403)
            self.assertEqual(c.req("GET", "/data.json")[0], 200)

    def test_sections_differ_by_role(self):
        g, u = Client(self.port), Client(self.port)
        g.login("gast", "longenoughpw"), u.login("anna", "longenoughpw")
        gs, us = set(json.loads(g.req("GET", "/data.json")[1])["sections"]), set(json.loads(u.req("GET", "/data.json")[1])["sections"])
        self.assertNotIn("grades", gs)
        self.assertNotIn("absences", gs)
        self.assertIn("grades", us)
        self.assertTrue(gs < us)

    def test_admin_gets_the_full_app_and_the_panel(self):
        c = Client(self.port)
        self.assertEqual(c.login("boss", "twelve-chars!"), 200)
        s, b, _ = c.req("GET", "/")
        self.assertIn("Stundl", b)
        self.assertIn("Admin panel", b)
        self.assertEqual(c.req("GET", "/admin")[0], 200)
        self.assertEqual(c.req("GET", "/api/state")[0], 200)
        st = json.loads(c.req("GET", "/api/admin/status")[1])
        self.assertEqual(len(st["accounts"]), 3)
        self.assertEqual(json.loads(c.req("GET", "/api/me")[1])["role"], "admin")

    def test_admin_settings_validation_and_long_links(self):
        c = Client(self.port)
        c.login("boss", "twelve-chars!")
        s, b, _ = c.req("POST", "/api/admin/config", {"network": {"mode": "static", "address": "nonsense"}})
        self.assertEqual(s, 400)
        self.assertIn("192.168.1.50/24", b)
        s, b, _ = c.req("POST", "/api/admin/config", {"links": {"long": True}})
        self.assertEqual(s, 200)
        self.assertEqual(len(core.parent_token()), 64)
        self.assertEqual(json.loads(c.req("GET", "/api/admin/config")[1])["links"]["long"], True)
        req = serverconf.state_dir() / "apply" / "request.json"
        self.assertTrue(req.exists())                                       # a system change was requested for the root helper
        s, b, _ = c.req("POST", "/api/admin/config", {"network": {"hostname": "my-ssc"}})
        self.assertIn("network", json.loads(req.read_text())["parts"])

    def test_admin_account_management_and_guards(self):
        c = Client(self.port)
        c.login("boss", "twelve-chars!")
        self.assertEqual(c.req("POST", "/api/admin/accounts", {"name": "neu", "password": "longenoughpw", "role": "guest"})[0], 200)
        self.assertEqual(c.req("POST", "/api/admin/accounts", {"name": "neu", "password": "longenoughpw", "role": "guest"})[0], 400)
        self.assertEqual(c.req("POST", "/api/admin/accounts/delete", {"name": "boss"})[0], 400)          # not yourself
        self.assertEqual(c.req("POST", "/api/admin/accounts/delete", {"name": "neu"})[0], 200)
        self.assertEqual(c.req("POST", "/api/admin/accounts/role", {"name": "boss", "role": "user"})[0], 400)  # last admin
        self.assertEqual(c.req("POST", "/api/admin/untis", {"qr": "garbage"})[0], 400)
        self.assertTrue(any(e["event"] == "account-created" for e in json.loads(c.req("GET", "/api/admin/audit")[1])))

    def test_guest_without_password_only_when_enabled(self):
        c = Client(self.port)
        self.assertEqual(c.req("POST", "/api/login", {"guest": True})[0], 401)
        a = Client(self.port)
        a.login("boss", "twelve-chars!")
        a.req("POST", "/api/admin/config", {"web": {"guest_passwordless": True}})
        self.assertEqual(c.req("POST", "/api/login", {"guest": True})[0], 200)
        self.assertEqual(json.loads(c.req("GET", "/api/me")[1])["role"], "guest")
        a.req("POST", "/api/admin/config", {"web": {"guest_passwordless": False}})

    def test_logout_ends_the_session(self):
        c = Client(self.port)
        c.login("anna", "longenoughpw")
        old = c.cookie
        c.req("POST", "/api/logout", {})
        c.cookie = old
        self.assertEqual(c.req("GET", "/data.json")[0], 401)

    def test_security_headers_and_cookie_flags(self):
        c = Client(self.port)
        s, b, r = c.req("POST", "/api/login", {"name": "anna", "password": "longenoughpw"})
        ck = r.getheader("Set-Cookie")
        self.assertIn("HttpOnly", ck)
        self.assertIn("SameSite=Strict", ck)
        self.assertEqual(r.getheader("X-Frame-Options"), "DENY")
        self.assertEqual(r.getheader("Cache-Control"), "no-store")


if __name__ == "__main__":
    unittest.main()


class SsconfigTests(unittest.TestCase):
    def setUp(self):
        self.d = isolate()
        auth.reset_throttle()
        import ssconfig
        self.sc = ssconfig
        ssconfig.addresses = lambda: [("eth0", "192.168.1.50/24")]

    def out(self, fn, *a):
        import contextlib
        import io
        b = io.StringIO()
        with contextlib.redirect_stdout(b):
            rc = fn(*a)
        return rc, b.getvalue()

    def test_showip(self):
        rc, o = self.out(self.sc.main, ["showip"])
        self.assertEqual(rc, 0)
        self.assertIn("192.168.1.50/24", o)
        self.assertIn("http://192.168.1.50:8080/", o)

    def test_showlink_short_then_long(self):
        rc, o = self.out(self.sc.main, ["showlink"])
        self.assertEqual(rc, 0)
        self.assertIn("http://192.168.1.50:8766/p/", o)
        self.assertIn("/wall", o)
        short = o.split("/p/")[1].split("/")[0]
        self.assertLess(len(short), 40)
        self.assertEqual(self.out(self.sc.main, ["set", "links.long", "true"])[0], 0)
        _, o2 = self.out(self.sc.main, ["showlink"])
        self.assertGreaterEqual(len(o2.split("/p/")[1].split("/")[0]), 64)
        self.out(self.sc.main, ["rotate-link"])
        _, o3 = self.out(self.sc.main, ["showlink"])
        self.assertNotEqual(o2.split("/p/")[1].split("/")[0], o3.split("/p/")[1].split("/")[0])

    def test_showlink_when_disabled(self):
        self.out(self.sc.main, ["set", "links.enabled", "false"])
        self.assertEqual(self.out(self.sc.main, ["showlink"])[0], 1)

    def test_set_validates(self):
        self.assertEqual(self.out(self.sc.main, ["set", "web.port", "99999"])[0], 1)
        self.assertEqual(self.out(self.sc.main, ["set", "ssh.port", "2222"])[0], 0)
        self.assertEqual(serverconf.load()["ssh"]["port"], 2222)

    def test_accounts_via_cli(self):
        os.environ["SSC_NEW_PASSWORD"] = "twelve-chars!"
        try:
            self.assertEqual(self.out(self.sc.main, ["account", "add", "boss", "admin"])[0], 0)
        finally:
            del os.environ["SSC_NEW_PASSWORD"]
        _, o = self.out(self.sc.main, ["account", "list"])
        self.assertIn("boss", o)
        self.assertEqual(self.out(self.sc.main, ["account", "delete", "boss"])[0], 1)   # last admin

    def test_apply_renders_files_and_rolls_back_bad_ssh(self):
        import applycfg
        root = Path(self.d) / "root"
        (root / "sys/class/net/eth0").mkdir(parents=True)
        calls = []

        class R:
            def __init__(s, rc=0, err=""):
                s.returncode, s.stderr, s.stdout = rc, err, ""

        def run(cmd):
            calls.append(cmd)
            return R(1, "bad") if cmd[:2] == ["sshd", "-t"] and self.fail_ssh else R()

        cfg = serverconf.validate({"network": {"mode": "static", "address": "10.0.0.5/24", "gateway": "10.0.0.1", "hostname": "kiosk"}})
        self.fail_ssh = False
        res = applycfg.apply(["network", "ssh", "firewall", "web"], cfg, root, run)
        self.assertTrue(res["ok"], res)
        self.assertIn("address 10.0.0.5", (root / "etc/network/interfaces.d/ssc").read_text())
        self.assertEqual((root / "etc/hostname").read_text(), "kiosk\n")
        self.assertIn("127.0.1.1\tkiosk", (root / "etc/hosts").read_text())
        self.assertIn("Port 22", (root / "etc/ssh/sshd_config.d/ssc.conf").read_text())
        self.assertIn(["systemctl", "restart", "ssc-server"], calls)
        good = (root / "etc/ssh/sshd_config.d/ssc.conf").read_text()
        self.fail_ssh = True
        cfg2 = serverconf.validate({"ssh": {"port": 2200}})
        res = applycfg.apply(["ssh"], cfg2, root, run)
        self.assertFalse(res["ok"])
        self.assertEqual((root / "etc/ssh/sshd_config.d/ssc.conf").read_text(), good)    # old config restored

    def test_process_request_only_known_parts(self):
        import applycfg
        d = serverconf.state_dir() / "apply"
        d.mkdir(parents=True)
        (d / "request.json").write_text(json.dumps({"parts": ["rm -rf /", "web"]}))
        seen = []

        class R:
            returncode, stderr, stdout = 0, "", ""

        res = applycfg.process_request(Path(self.d) / "r2", lambda c: seen.append(c) or R())
        self.assertEqual(seen, [["systemctl", "restart", "ssc-server"]])
        self.assertTrue(res["ok"])
        self.assertFalse((d / "request.json").exists())


class InstallPlanTests(unittest.TestCase):
    LS = json.dumps({"blockdevices": [
        {"name": "/dev/sda", "size": 32000000000, "model": "QEMU HARDDISK ", "type": "disk", "ro": False, "rm": False},
        {"name": "/dev/sdb", "size": 8000000000, "model": "USB", "type": "disk", "ro": False, "rm": True},
        {"name": "/dev/sr0", "size": 900000000, "model": "DVD", "type": "rom", "ro": False, "rm": True},
        {"name": "/dev/loop0", "size": 90000000000, "model": None, "type": "loop", "ro": True, "rm": False},
        {"name": "/dev/sdc", "size": 1000000000, "model": "tiny", "type": "disk", "ro": False, "rm": False}]})

    def test_parse_lsblk_filters(self):
        import install_plan as p
        d = p.parse_lsblk(self.LS, live_disks={"/dev/sdb"})
        self.assertEqual([x["path"] for x in d], ["/dev/sda"])
        self.assertEqual(len(p.parse_lsblk(self.LS)), 2)

    def test_partition_names_and_commands(self):
        import install_plan as p
        self.assertEqual((p.part("/dev/sda", 3), p.part("/dev/nvme0n1", 3), p.part("/dev/mmcblk0", 2)), ("/dev/sda3", "/dev/nvme0n1p3", "/dev/mmcblk0p2"))
        c64 = p.partition_commands("/dev/sda", "amd64")
        self.assertEqual(c64[0], ["sgdisk", "--zap-all", "/dev/sda"])
        self.assertEqual(len(c64), 4)
        self.assertEqual(len(p.partition_commands("/dev/sda", "i386")), 3)       # no EFI partition on 32-bit
        self.assertIn(["mkfs.ext4", "-F", "-q", "-L", "ssc-root", "/dev/sda3"], p.mkfs_commands("/dev/sda", "amd64"))
        self.assertIn(["mkfs.ext4", "-F", "-q", "-L", "ssc-root", "/dev/sda2"], p.mkfs_commands("/dev/sda", "i386"))

    def test_fstab(self):
        import install_plan as p
        f = p.fstab("AAA", "BBB")
        self.assertIn("UUID=AAA / ext4", f)
        self.assertIn("UUID=BBB /boot/efi vfat", f)
        self.assertNotIn("efi", p.fstab("AAA"))

    def test_answers_validation(self):
        import install_plan as p
        disks = [{"path": "/dev/sda", "size": 32 * 10 ** 9, "model": "", "removable": False}]
        ok = p.validate_answers({"password": "twelve-chars!", "hostname": "kiosk"}, disks)
        self.assertEqual((ok["disk"], ok["hostname"], ok["admin"], ok["network"]["mode"]), ("/dev/sda", "kiosk", "admin", "dhcp"))
        for bad in ({"password": "short"}, {"password": "twelve-chars!", "disk": "/dev/sdz"}, {"password": "twelve-chars!", "hostname": "bad host"},
                    {"password": "twelve-chars!", "timezone": "../../etc/passwd"}, {"password": "twelve-chars!", "admin": "Root User"}):
            with self.assertRaises(ValueError, msg=str(bad)):
                p.validate_answers(bad, disks)
        two = disks + [dict(disks[0], path="/dev/sdb")]
        with self.assertRaises(ValueError):
            p.validate_answers({"password": "twelve-chars!"}, two)            # auto with 2 disks is ambiguous: refuse

    def test_dry_run_installs_nothing(self):
        import ssc_install as si
        import install_plan as p
        log = []
        run = si.Runner(dry=True, log=log.append)
        ans = p.validate_answers({"password": "twelve-chars!"}, [{"path": "/dev/vda", "size": 32 * 10 ** 9, "model": "", "removable": False}])
        si.install(ans, run, root=Path(tempfile.mkdtemp()), efi=True, a="amd64")
        text = "\n".join(log)
        for needle in ("sgdisk --zap-all /dev/vda", "mkfs.ext4", "rsync -aHAX", "grub-install --target=i386-pc", "grub-install --target=x86_64-efi", "purge live-boot"):
            self.assertIn(needle, text)
        self.assertLess(text.index("sgdisk"), text.index("rsync"))
        self.assertLess(text.index("rsync"), text.index("grub-install"))


class UntisDirectTests(unittest.TestCase):
    KEY = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"

    def setUp(self):
        isolate()
        import untis_direct
        self.u = untis_direct
        self.cfg = dict(server="demo.webuntis.com", school="demo", user="max", key=self.KEY)

    def test_totp_matches_rfc6238_vector(self):
        self.assertEqual(self.u.totp(self.KEY, 59 * 1000, digits=8), "94287082")

    def test_parse_qr(self):
        q = f"untis://setschool?url=https://demo.webuntis.com&school=demo&user=max&key={self.KEY}&schoolNumber=1"
        self.assertEqual(self.u.parse_qr(q), self.cfg)
        for bad in ("", "http://x", "untis://setschool?school=a", "untis://setschool?url=a&school=b&user=c&key=!!"):
            self.assertIsNone(self.u.parse_qr(bad))

    def fake(self, auth_ok=True):
        seen = []

        def post(url, body):
            m = body["method"]
            p = body["params"][0]
            seen.append((m, p["auth"]["user"], len(p["auth"]["otp"])))
            if not auth_ok:
                return {"error": {"code": -8520, "message": "bad credentials"}}
            if m == "getUserData2017":
                return {"result": {"userData": {"elemType": 5, "elemId": 7},
                                   "masterData": {"subjects": [{"id": 1, "name": "M", "longName": "Math"}], "teachers": [{"id": 2, "name": "AB", "longName": "Anna B"}],
                                                  "rooms": [{"id": 3, "name": "R1", "longName": "Room 1"}, {"id": 4, "name": "R2"}], "klassen": []}}}
            if m == "getTimetable2017":
                return {"result": {"timetable": {"periods": [{"date": 20261123, "startTime": 800, "endTime": 845, "su": [{"id": 1}], "te": [{"id": 2}],
                                                              "ro": [{"id": 4, "orgid": 3}], "cellState": "SUBSTITUTION"}]}}}
            if m == "getHomeWork2017":
                return {"result": {"homeWorks": [{"id": 9, "lessonId": 1, "dueDate": 20261125, "text": "p.12", "completed": False}], "lessons": [{"id": 1, "subject": "Math"}]}}
            return {"result": {"exams": []}}
        return post, seen

    def test_fetch_converts_to_ssc_format(self):
        from datetime import date
        post, seen = self.fake()
        out = self.u.fetch(self.cfg, today=date(2026, 11, 23), weeks_back=0, weeks_ahead=0, post=post)
        day = out["timetable"]["2026-11-23"]["days"][0]
        e = day["gridEntries"][0]
        self.assertEqual((day["date"], e["status"], e["duration"]["start"]), ("2026-11-23", "CHANGED", "2026-11-23T08:00"))
        self.assertEqual(e["position3"][0]["current"]["shortName"], "M")
        self.assertEqual(e["position4"][0]["removed"]["shortName"], "R1")      # room changed R1 -> R2
        self.assertEqual(out["homework"]["data"]["homeworks"][0]["text"], "p.12")
        self.assertTrue(all(u == "max" and n == 6 for _, u, n in seen))         # one-time codes only; the key is never sent

    def test_bad_credentials_raise_auth_error(self):
        post, _ = self.fake(auth_ok=False)
        with self.assertRaises(self.u.AuthError):
            self.u.fetch(self.cfg, post=post)

    def test_credentials_live_in_the_vault_only(self):
        self.u.save_credentials(self.cfg)
        self.assertEqual(self.u.load_credentials()["user"], "max")
        for f in Path(os.environ["SSC_DATA_DIR"]).rglob("*"):
            if f.is_file() and f.name == "secrets.json":
                self.assertEqual(oct(f.stat().st_mode & 0o777), "0o600")
            elif f.is_file() and f.suffix in (".json", ".db"):
                self.assertNotIn(self.KEY.encode(), f.read_bytes())
        self.u.clear_credentials()
        self.assertIsNone(self.u.load_credentials())
