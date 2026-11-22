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

if sys.platform == "win32":
    raise unittest.SkipTest("SSC Server runs on Linux; these tests need POSIX")

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
            auth.create("root2", "five5", "admin", self.con)             # 5 characters: too short for every role
        auth.create("sixsix", "sixsix", "guest", self.con)           # 6 is enough
        with self.assertRaises(auth.AuthError):
            auth.create("weak1", "123456", "user", self.con)        # but not a well-known one
        with self.assertRaises(auth.AuthError):
            auth.create("Bad Name", "longenoughpw", "user", self.con)
        auth.create("boss", "sixsix", "admin", self.con)             # admins too: 6 characters
        self.assertEqual(sorted(a["role"] for a in auth.accounts(self.con)), ["admin", "guest", "user"])
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
            self.assertNotIn("href='/admin'", b)                                # no link to the admin panel
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

    def test_wifi_api(self):
        g = Client(self.port)
        g.login("anna", "longenoughpw")
        self.assertEqual(g.req("POST", "/api/admin/wifi", {"ssid": "x", "passphrase": "correct horse"})[0], 403)
        a = Client(self.port)
        a.login("boss", "twelve-chars!")
        self.assertEqual(a.req("POST", "/api/admin/wifi", {"ssid": "Home"})[0], 400)                        # new network needs a password
        self.assertEqual(a.req("POST", "/api/admin/wifi", {"ssid": "Home", "passphrase": "short"})[0], 400)
        s, b, _ = a.req("POST", "/api/admin/wifi", {"ssid": "Home", "passphrase": "correct horse", "country": "at"})
        self.assertEqual(s, 200)
        self.assertNotIn("correct horse", b)
        self.assertNotIn("psk", json.loads(a.req("GET", "/api/admin/config")[1])["wifi"])
        self.assertEqual(json.loads(a.req("GET", "/api/admin/config")[1])["wifi"]["ssid"], "Home")
        self.assertEqual(a.req("POST", "/api/admin/wifi", {"ssid": "Home"})[0], 200)                        # same network: keep the key
        self.assertEqual(a.req("POST", "/api/admin/wifi", {"enabled": False})[0], 200)
        self.assertFalse(json.loads(a.req("GET", "/api/admin/config")[1])["wifi"]["enabled"])

    def test_web_panel_changes_to_console_and_power_are_applied(self):
        # the admin panel only drops a request file: every settings group that needs the root helper must be listed in it
        a = Client(self.port)
        a.login("boss", "twelve-chars!")
        req = serverconf.state_dir() / "apply" / "request.json"
        req.unlink(missing_ok=True)
        self.assertEqual(a.req("POST", "/api/admin/config", {"power": {"lid": "suspend"}})[0], 200)
        self.assertIn("power", json.loads(req.read_text())["parts"])
        req.unlink()
        self.assertEqual(a.req("POST", "/api/admin/config", {"console": {"autologin_user": "max"}})[0], 200)
        self.assertIn("console", json.loads(req.read_text())["parts"])
        a.req("POST", "/api/admin/config", {"power": {"lid": "ignore"}, "console": {"autologin_user": ""}})
        self.assertEqual(a.req("POST", "/api/admin/config", {"power": {"lid": "sideways"}})[0], 400)

    def test_preferences_are_per_user(self):
        a, u, g = Client(self.port), Client(self.port), Client(self.port)
        a.login("boss", "twelve-chars!"), u.login("anna", "longenoughpw"), g.login("gast", "longenoughpw")
        anon = Client(self.port)
        self.assertEqual(anon.req("GET", "/api/prefs")[0], 401)
        self.assertEqual(anon.req("POST", "/api/prefs", {"lang": "de"})[0], 401)
        self.assertEqual(json.loads(u.req("GET", "/api/prefs")[1]), {})
        s, b, _ = u.req("POST", "/api/prefs", {"lang": "fr", "theme": "dark", "accent": "teal", "evil": "<script>", "palette": "nope"})
        self.assertEqual(s, 200)
        self.assertEqual(json.loads(u.req("GET", "/api/prefs")[1]), {"lang": "fr", "theme": "dark", "accent": "teal"})      # junk dropped
        self.assertEqual(json.loads(a.req("GET", "/api/prefs")[1]), {})                                                       # other users unaffected
        a.req("POST", "/api/prefs", {"lang": "pl", "palette": "forest"})
        self.assertEqual(json.loads(a.req("GET", "/api/prefs")[1]), {"lang": "pl", "palette": "forest"})
        self.assertEqual(json.loads(u.req("GET", "/api/prefs")[1])["lang"], "fr")
        u.req("POST", "/api/prefs", {"theme": "light"})                                                                       # a change merges, it does not wipe
        self.assertEqual(json.loads(u.req("GET", "/api/prefs")[1]), {"lang": "fr", "theme": "light", "accent": "teal"})
        self.assertEqual(g.req("POST", "/api/prefs", {"lang": "es"})[0], 200)                                                 # guests (named accounts) keep theirs too
        self.assertEqual(json.loads(g.req("GET", "/api/prefs")[1])["lang"], "es")
        self.assertEqual(u.req("POST", "/api/prefs", None, {"Content-Type": "text/plain"})[0], 415)

    def test_pages_carry_the_runtime_and_only_app_pages_may_save_prefs(self):
        a = Client(self.port)
        a.login("boss", "twelve-chars!")
        page = a.req("GET", "/")[1]
        self.assertIn("root.SSCUI = API", page)
        self.assertIn("SSC_PREFS_API=true", page)
        self.assertIn("root.SSCUI = API", Client(self.port).req("GET", "/")[1])               # the login page too
        self.assertNotIn("SSC_PREFS_API=true", Client(self.port).req("GET", "/")[1])             # ...but it has no account to save to

    def test_watch_api_is_admin_only_and_validates(self):
        g = Client(self.port)
        g.login("anna", "longenoughpw")
        self.assertEqual(g.req("GET", "/api/watch")[0], 403)
        self.assertEqual(g.req("POST", "/api/watch/pair", {"address": "AA:BB:CC:DD:EE:FF"})[0], 403)
        a = Client(self.port)
        a.login("boss", "twelve-chars!")
        self.assertEqual(json.loads(a.req("GET", "/api/watch")[1])["address"], None)
        self.assertEqual(a.req("POST", "/api/watch/pair", {"address": "not an address; rm -rf"})[0], 400)
        self.assertEqual(a.req("POST", "/api/watch/pair", {"address": "AA:BB:CC:DD:EE:FF"})[0], 200)
        self.assertEqual(json.loads(a.req("GET", "/api/watch")[1])["address"], "AA:BB:CC:DD:EE:FF")
        a.req("POST", "/api/watch/forget", {})
        self.assertIsNone(json.loads(a.req("GET", "/api/watch")[1])["address"])

    def test_admin_untis_with_fields_and_clear_error_messages(self):
        a = Client(self.port)
        a.login("boss", "twelve-chars!")
        s, b, _ = a.req("POST", "/api/admin/untis", {"qr": "untis://setschool?url=a.webuntis.com&school=s&user=u&key=ABCD0189"})
        self.assertEqual(s, 400)
        self.assertIn("0 1 8 9", b)
        s, b, _ = a.req("POST", "/api/admin/untis", {"server": "a.webuntis.com", "school": "s", "user": "u"})
        self.assertEqual(s, 400)
        self.assertIn("key", b)
        s, b, _ = a.req("POST", "/api/admin/untis", {"server": "a.webuntis.com", "school": "s", "user": "u", "key": "ABCD EFGH"})
        self.assertEqual(s, 200)
        a.req("POST", "/api/admin/untis/clear", {})

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

    def test_install_refuses_a_copy_without_the_ssc_tools(self):
        import ssc_install as si
        import install_plan as p
        root = Path(tempfile.mkdtemp())
        ans = p.validate_answers({"password": "sixsix"}, [{"path": "/dev/vda", "size": 32 * 10 ** 9, "model": "", "removable": False}])
        log = []

        class Run(si.Runner):
            def __call__(self, cmd, check=True, input=None, env=None):
                self.log("$ " + " ".join(cmd))
                return __import__("subprocess").CompletedProcess(cmd, 0, "", "")      # pretend every command worked; nothing is copied
        with self.assertRaises(RuntimeError) as e:
            si.install(ans, Run(dry=False, log=log.append), root=root, efi=False, a="amd64")
        self.assertIn("usr/local/bin/ssconfig", str(e.exception))
        self.assertFalse(any("grub-install" in x for x in log))                      # it stopped before touching the boot loader

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

    def test_homework_subjects_are_resolved_whatever_shape_the_api_uses(self):
        from datetime import date
        master = {"subjects": [{"id": 1, "name": "M", "longName": "Mathematics"}, {"id": 2, "name": "E", "longName": "English"}, {"id": 3, "name": "BIO"}],
                  "teachers": [], "rooms": [], "klassen": []}

        def post(url, body):
            m = body["method"]
            if m == "getUserData2017":
                return {"result": {"userData": {"elemType": 5, "elemId": 7}, "masterData": master}}
            if m == "getTimetable2017":       # lesson 55 is Biology: only the timetable knows that
                return {"result": {"timetable": {"periods": [{"date": 20261123, "startTime": 800, "endTime": 845, "su": [{"id": 3}], "lessonId": 55}]}}}
            if m == "getHomeWork2017":
                return {"result": {"homeWorks": [
                    {"id": 1, "lessonId": 10, "dueDate": 20261125, "text": "a"},            # lesson says subject = id 1
                    {"id": 2, "lessonId": 11, "dueDate": 20261126, "text": "b"},            # lesson says subject = name "English"
                    {"id": 3, "lessonId": 55, "dueDate": 20261127, "text": "c"},            # not in the lessons list: from the timetable
                    {"id": 4, "dueDate": 20261128, "text": "d", "subject": 2},              # subject right on the homework (an id)
                    {"id": 5, "lessonId": 99, "dueDate": 20261129, "text": "e"}],           # nobody knows
                    "lessons": [{"id": 10, "subject": 1}, {"id": 11, "subject": "English"}]}}
            return {"result": {"exams": [
                {"id": 1, "date": "2026-11-30T00:00:00", "startTime": 900, "endTime": 1000, "subjectId": 1, "name": "Test"},
                {"id": 2, "examDate": 20261201, "startTime": 800, "subject": "E", "text": "Vocab"},
                {"id": 3, "examDate": 20261202, "subject": {"id": 3}, "examType": "Schularbeit"}]}}
        out = self.u.fetch(self.cfg, today=date(2026, 11, 23), weeks_back=0, weeks_ahead=0, post=post)
        import core
        hw = {h["text"]: h["subject"] for h in core.homework_items(out["homework"])}
        self.assertEqual(hw, {"a": "M", "b": "English", "c": "BIO", "d": "E", "e": ""})
        self.assertTrue(any("subject of 1 item" in n for n in out["notes"]))                 # the gap is reported, not hidden
        ex = core.exam_items(out["exams"])
        self.assertEqual([(e["date"], e["start"], e["subject"], e["text"]) for e in ex],
                         [("2026-11-30", "09:00", "M", "Test"), ("2026-12-01", "08:00", "E", "Vocab"), ("2026-12-02", "", "BIO", "Schularbeit")])

    def test_raw_call_shows_what_webuntis_sends(self):
        sent = []

        def post(url, body):
            sent.append(body["method"])
            return {"result": {"userData": {"elemType": 5, "elemId": 7}, "exams": [{"id": 1}]}}
        self.assertIn("exams", self.u.raw_call(self.cfg, "getExams2017", post=post))
        with self.assertRaises(ValueError):
            self.u.raw_call(self.cfg, "deleteEverything", post=post)

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


IW_SCAN = """BSS aa:bb:cc:dd:ee:01(on wlan0)
\tfreq: 2412
\tsignal: -48.00 dBm
\tSSID: HomeNet
\tRSN:\t * Version: 1
BSS aa:bb:cc:dd:ee:02(on wlan0)
\tsignal: -70.00 dBm
\tSSID: HomeNet
\tRSN:\t * Version: 1
BSS aa:bb:cc:dd:ee:03(on wlan0)
\tsignal: -60.00 dBm
\tSSID: Cafe Open
\tcapability: ESS (0x0401)
BSS aa:bb:cc:dd:ee:04(on wlan0)
\tsignal: -50.00 dBm
\tSSID:
BSS aa:bb:cc:dd:ee:05(on wlan0)
\tsignal: -52.00 dBm
\tSSID: \\x00\\x00\\x00
"""


class WifiTests(unittest.TestCase):
    def setUp(self):
        self.d = isolate()
        auth.reset_throttle()

    def test_psk_matches_the_ieee_80211i_test_vector(self):
        self.assertEqual(serverconf.derive_psk("IEEE", "password"), "f42c6fc52df0ebef9ebb4b90b38a5f902e83fe1b135a70e23aed762e9710a12e")
        for bad in ("short", "x" * 64, "pässwörd-long"):
            with self.assertRaises(ValueError):
                serverconf.derive_psk("Net", bad)

    def test_validation(self):
        ok = serverconf.validate({"wifi": {"enabled": True, "ssid": "Home Net", "country": "at"}})["wifi"]
        self.assertEqual((ok["ssid"], ok["country"]), ("Home Net", "AT"))
        for bad in ({"enabled": True, "ssid": ""}, {"enabled": True, "ssid": "x" * 33}, {"enabled": True, "ssid": "a\nb"},
                    {"enabled": True, "ssid": "ok", "country": "Austria"}):
            with self.assertRaises(ValueError, msg=str(bad)):
                serverconf.validate({"wifi": bad})
        serverconf.validate({"wifi": {"enabled": False}})          # off needs no name

    def test_wpa_conf_is_injection_proof(self):
        w = serverconf.validate({"wifi": {"enabled": True, "ssid": 'evil"} network={ psk=1', "hidden": True, "country": "DE"}})["wifi"]
        txt = serverconf.render_wpa(w, "ab" * 32)
        self.assertEqual(txt.count("network={"), 1)                 # SSID is hex: it can't open another block
        self.assertIn("scan_ssid=1", txt)
        self.assertIn("country=DE", txt)
        self.assertEqual(serverconf.ssid_from_conf(txt), w["ssid"])
        self.assertEqual(serverconf.psk_from_conf(txt), "ab" * 32)
        self.assertIn("key_mgmt=NONE", serverconf.render_wpa(dict(w, open=True)))
        with self.assertRaises(ValueError):
            serverconf.render_wpa(w, None)

    def test_interfaces_with_wifi(self):
        c = serverconf.validate({"wifi": {"enabled": True, "ssid": "Home"}})
        t = serverconf.render_network(c, "eth0", "wlan0")
        self.assertIn("allow-hotplug eth0\niface eth0 inet dhcp", t)
        self.assertIn("iface wlan0 inet dhcp\n    wpa-conf /etc/wpa_supplicant/ssc-wifi.conf", t)
        st = serverconf.validate({"wifi": {"enabled": True, "ssid": "Home"}, "network": {"mode": "static", "address": "10.0.0.5/24", "gateway": "10.0.0.1"}})
        self.assertIn("iface wlan0 inet static\n    address 10.0.0.5", serverconf.render_network(st, "eth0", "wlan0"))
        self.assertNotIn("wpa-conf", serverconf.render_network(serverconf.validate({}), "eth0", "wlan0"))     # off: plain wired config
        self.assertNotIn("wpa-conf", serverconf.render_network(c, "eth0", None))                            # no card: wired config

    def test_scan_parser(self):
        n = serverconf.parse_iw_scan(IW_SCAN)
        self.assertEqual([(x["ssid"], x["secured"]) for x in n], [("HomeNet", True), ("Cafe Open", False)])
        self.assertEqual(n[0]["signal_dbm"], -48.0)               # strongest access point per name; hidden/blank entries dropped

    def _fake_root(self):
        root = Path(self.d) / "root"
        for n in ("eth0", "wlan0"):
            (root / "sys/class/net" / n).mkdir(parents=True)
        (root / "sys/class/net/wlan0/wireless").mkdir()
        return root

    def test_apply_writes_a_root_only_conf_and_consumes_the_staged_key(self):
        import applycfg
        root = self._fake_root()
        seen = []

        class R:
            returncode, stderr, stdout = 0, "", ""
        run = lambda c: seen.append(c) or R()
        cfg = serverconf.validate({"wifi": {"enabled": True, "ssid": "Home", "country": "AT"}})
        serverconf.stage_wifi_psk("Home", serverconf.derive_psk("Home", "correct horse"))
        res = applycfg.apply(["network"], cfg, root, run)
        self.assertTrue(res["ok"], res)
        conf = root / "etc/wpa_supplicant/ssc-wifi.conf"
        self.assertEqual(oct(conf.stat().st_mode & 0o777), "0o600")
        self.assertEqual(serverconf.psk_from_conf(conf.read_text()), serverconf.derive_psk("Home", "correct horse"))
        self.assertNotIn("correct horse", conf.read_text())
        self.assertFalse((serverconf.state_dir() / "apply" / "wifi.psk").exists())
        self.assertIn(["ifup", "wlan0"], seen)
        self.assertIn("wpa-conf", (root / "etc/network/interfaces.d/ssc").read_text())
        # same network again without a new password: the stored key is kept
        res = applycfg.apply(["network"], serverconf.validate({"wifi": {"enabled": True, "ssid": "Home", "country": "DE"}}), root, run)
        self.assertTrue(res["ok"], res)
        self.assertIn("country=DE", conf.read_text())
        # another network without a password: refused, nothing half-written
        res = applycfg.apply(["network"], serverconf.validate({"wifi": {"enabled": True, "ssid": "Other"}}), root, run)
        self.assertFalse(res["ok"])
        self.assertIn("no password", res["failed"][0])
        # turning Wi-Fi off removes the credentials file
        applycfg.apply(["network"], serverconf.validate({}), root, run)
        self.assertFalse(conf.exists())

    def test_ssconfig_wifi_set_and_flags(self):
        import ssconfig
        ssconfig.addresses = lambda: []
        os.environ["SSC_WIFI_PASSPHRASE"] = "correct horse"
        try:
            self.assertEqual(ssconfig.main(["wifi", "set", "Home", "--country=at", "--hidden"]), 0)
        finally:
            del os.environ["SSC_WIFI_PASSPHRASE"]
        w = serverconf.load()["wifi"]
        self.assertEqual((w["enabled"], w["ssid"], w["country"], w["hidden"]), (True, "Home", "AT", True))
        self.assertNotIn("correct horse", serverconf.config_path().read_text())          # the passphrase is never in server.json
        staged = json.loads((serverconf.state_dir() / "apply" / "wifi.psk").read_text())
        self.assertEqual(staged["psk"], serverconf.derive_psk("Home", "correct horse"))
        self.assertEqual(oct((serverconf.state_dir() / "apply" / "wifi.psk").stat().st_mode & 0o777), "0o600")
        os.environ["SSC_WIFI_PASSPHRASE"] = "short"
        try:
            self.assertEqual(ssconfig.main(["wifi", "set", "Home"]), 1)
        finally:
            del os.environ["SSC_WIFI_PASSPHRASE"]
        self.assertEqual(ssconfig.main(["wifi", "off"]), 0)
        self.assertFalse(serverconf.load()["wifi"]["enabled"])

    def test_installer_answers_with_wifi(self):
        import install_plan as p
        disks = [{"path": "/dev/sda", "size": 32 * 10 ** 9, "model": "", "removable": False}]
        a = p.validate_answers({"password": "twelve-chars!", "wifi": {"ssid": "Home", "passphrase": "correct horse", "country": "at"}}, disks)
        self.assertEqual(a["wifi"]["psk"], serverconf.derive_psk("Home", "correct horse"))
        self.assertEqual(a["wifi"]["country"], "AT")
        self.assertIsNone(p.validate_answers({"password": "twelve-chars!"}, disks)["wifi"])
        with self.assertRaises(ValueError):
            p.validate_answers({"password": "twelve-chars!", "wifi": {"ssid": "Home", "passphrase": "short"}}, disks)


class AutologinTests(unittest.TestCase):
    def setUp(self):
        self.d = isolate()
        self.root = Path(self.d) / "root"
        (self.root / "etc").mkdir(parents=True)
        (self.root / "etc/passwd").write_text("root:x:0:0::/root:/bin/bash\nssc:x:998:998::/var/lib/ssc:/usr/sbin/nologin\nmax:x:1000:1000::/home/max:/bin/bash\n")

    def run_part(self, cfg):
        import applycfg
        seen = []

        class R:
            returncode, stderr, stdout = 0, "", ""
        res = applycfg.apply(["console"], cfg, self.root, lambda c: seen.append(c) or R())
        return res, seen

    def test_validation(self):
        self.assertEqual(serverconf.validate({"console": {"autologin_user": " max "}})["console"]["autologin_user"], "max")
        for bad in ("root", "Max Power", "a;b", "x" * 40):
            with self.assertRaises(ValueError, msg=bad):
                serverconf.validate({"console": {"autologin_user": bad}})
        self.assertEqual(serverconf.validate({})["console"]["autologin_user"], "")        # off by default

    def test_apply_writes_and_removes_the_dropin(self):
        res, seen = self.run_part(serverconf.validate({"console": {"autologin_user": "max"}}))
        self.assertTrue(res["ok"], res)
        f = self.root / "etc/systemd/system/getty@tty1.service.d/autologin.conf"
        self.assertIn("--autologin max --noclear", f.read_text())
        self.assertIn(["systemctl", "daemon-reload"], seen)
        # console only: nothing is written for SSH, PAM or the web service, and only tty1 is touched
        written = sorted(str(x.relative_to(self.root)) for x in self.root.rglob("*") if x.is_file() and x.name != "passwd")
        self.assertEqual(written, ["etc/systemd/system/getty@tty1.service.d/autologin.conf"])
        self.assertNotIn("PermitEmptyPasswords", f.read_text())
        res, _ = self.run_part(serverconf.validate({}))
        self.assertFalse(f.exists())

    def test_apply_refuses_unknown_and_system_users(self):
        for u in ("nobody", "ssc", "ghost"):                       # missing, or uid < 1000: no auto-login
            res, _ = self.run_part(serverconf.validate({"console": {"autologin_user": u}}))
            self.assertFalse(res["ok"], u)
            self.assertFalse((self.root / "etc/systemd/system/getty@tty1.service.d/autologin.conf").exists())

    def test_ssconfig_autologin(self):
        import ssconfig
        import applycfg
        ssconfig.addresses = lambda: []
        os.environ["SSC_ROOT"] = str(self.root)
        try:
            self.assertEqual(applycfg.default_login_user(), "max")
            self.assertEqual(ssconfig.main(["autologin", "on"]), 0)
            self.assertEqual(serverconf.load()["console"]["autologin_user"], "max")
            self.assertEqual(ssconfig.main(["autologin", "on", "root"]), 1)
            self.assertEqual(ssconfig.main(["autologin", "off"]), 0)
            self.assertEqual(serverconf.load()["console"]["autologin_user"], "")
        finally:
            del os.environ["SSC_ROOT"]

    def test_web_login_is_unaffected(self):
        # the web panel has no passwordless admin path: only an explicit guest option exists, and it never yields the admin role
        auth.reset_throttle()
        con = auth.db()
        auth.create("boss", "sixsix", "admin", con)
        with self.assertRaises(auth.AuthError):
            auth.login("boss", "", "9.9.9.9", con)
        self.assertEqual(auth.guest_session()[:0], "")
        self.assertEqual(auth.session(auth.guest_session())["role"], "guest")

    def test_installer_answer(self):
        import install_plan as p
        disks = [{"path": "/dev/sda", "size": 32 * 10 ** 9, "model": "", "removable": False}]
        self.assertFalse(p.validate_answers({"password": "sixsix"}, disks)["autologin"])
        self.assertTrue(p.validate_answers({"password": "sixsix", "autologin": True}, disks)["autologin"])


class PowerTests(unittest.TestCase):
    def setUp(self):
        self.d = isolate()
        self.root = Path(self.d) / "root"
        self.root.mkdir()

    def apply(self, power):
        import applycfg
        seen = []

        class R:
            returncode, stderr, stdout = 0, "", ""
        res = applycfg.apply(["power"], serverconf.validate({"power": power}), self.root, lambda c: seen.append(c) or R())
        return res, seen

    def test_default_keeps_a_closed_laptop_running(self):
        p = serverconf.validate({})["power"]
        self.assertEqual((p["lid"], p["block_sleep"]), ("ignore", True))
        txt = serverconf.render_logind(p)
        for line in ("HandleLidSwitch=ignore", "HandleLidSwitchExternalPower=ignore", "HandleLidSwitchDocked=ignore", "IdleAction=ignore", "HandleSuspendKey=ignore"):
            self.assertIn(line, txt)

    def test_image_ships_the_same_default(self):
        shipped = (ROOT / "server/iso/overlay/etc/systemd/logind.conf.d/ssc-power.conf").read_text()
        self.assertEqual(shipped, serverconf.render_logind(serverconf.validate({})["power"]))     # live system and fresh installs behave the same
        hook = (ROOT / "server/iso/hooks/0100-ssc.hook.chroot").read_text()
        self.assertIn("systemctl mask sleep.target suspend.target hibernate.target hybrid-sleep.target", hook)

    def test_validation(self):
        for lid in ("suspend", "poweroff", "ignore"):
            self.assertEqual(serverconf.validate({"power": {"lid": lid}})["power"]["lid"], lid)
        for bad in ("hibernate", "", "ignore; reboot", None):
            with self.assertRaises(ValueError, msg=str(bad)):
                serverconf.validate({"power": {"lid": bad}})

    def test_apply_writes_the_dropin_masks_sleep_and_restarts_logind(self):
        res, seen = self.apply({"lid": "ignore", "block_sleep": True})
        self.assertTrue(res["ok"], res)
        conf = (self.root / "etc/systemd/logind.conf.d/ssc-power.conf").read_text()
        self.assertIn("HandleLidSwitch=ignore", conf)
        self.assertIn(["systemctl", "mask", *serverconf.SLEEP_TARGETS], seen)
        self.assertIn(["systemctl", "restart", "systemd-logind"], seen)
        self.assertIn("keeps running", res["done"][0])
        res, seen = self.apply({"lid": "suspend", "block_sleep": False})
        self.assertIn("HandleLidSwitch=suspend", (self.root / "etc/systemd/logind.conf.d/ssc-power.conf").read_text())
        self.assertNotIn("HandleSuspendKey", (self.root / "etc/systemd/logind.conf.d/ssc-power.conf").read_text())
        self.assertIn(["systemctl", "unmask", *serverconf.SLEEP_TARGETS], seen)

    def test_ssconfig_lid_and_sleep(self):
        import ssconfig
        ssconfig.addresses = lambda: []
        self.assertEqual(ssconfig.main(["lid", "status"]), 0)
        self.assertEqual(ssconfig.main(["lid", "poweroff"]), 0)
        self.assertEqual(serverconf.load()["power"]["lid"], "poweroff")
        self.assertEqual(ssconfig.main(["lid", "sideways"]), 2)
        self.assertEqual(ssconfig.main(["sleep", "allow"]), 0)
        self.assertFalse(serverconf.load()["power"]["block_sleep"])
        self.assertEqual(ssconfig.main(["lid", "ignore"]), 0)
        self.assertEqual(ssconfig.main(["sleep", "block"]), 0)
        self.assertEqual(serverconf.load()["power"], {"lid": "ignore", "block_sleep": True})


class ImageCommandsTests(unittest.TestCase):
    def test_commands_are_on_every_users_path(self):
        # Debian: normal users have /usr/local/bin on PATH but not /usr/local/sbin, so `ssconfig` would be "command not found"
        for c in ("ssconfig", "ssc-install", "ssc-imgen"):
            f = ROOT / "server/iso/overlay/usr/local/bin" / c
            self.assertTrue(f.exists(), c)
            self.assertTrue(os.access(f, os.X_OK), c)
            self.assertTrue(f.read_text().startswith("#!/bin/sh"), c)
            self.assertFalse((ROOT / "server/iso/overlay/usr/local/sbin" / c).exists(), c)
        unit = (ROOT / "server/iso/overlay/etc/systemd/system/ssc-autoinstall.service").read_text()
        self.assertIn("/usr/local/bin/ssc-install --auto", unit)
        self.assertTrue((ROOT / "server/iso/overlay/usr/local/sbin/ssc-live-menu").exists())        # root-only service script stays
        self.assertIn("/usr/local/sbin/ssc-live-menu", (ROOT / "server/iso/overlay/etc/systemd/system/ssc-live-console.service").read_text())

    def test_ssconfig_wrapper_escalates_itself(self):
        sh = (ROOT / "server/iso/overlay/usr/local/bin/ssconfig").read_text()
        self.assertIn("exec sudo", sh)                       # plain `ssconfig` works for the admin user, no `sudo` needed
        self.assertIn("/opt/ssc/server/ssconfig.py", sh)


class UntisInputTests(unittest.TestCase):
    BASE = "untis://setschool?url=mese.webuntis.com&school=My%20School&user=anna.m&key="

    def setUp(self):
        isolate()
        import untis_direct
        self.u = untis_direct

    def test_forgiving_about_how_the_key_and_link_were_copied(self):
        for link in (self.BASE + "ABCDEFGHIJKLMNOP", self.BASE + "abcd efgh ijkl mnop", self.BASE + "ABCD-EFGH-IJKL-MNOP", "'" + self.BASE + "ABCDEFGHIJKLMNOP'",
                     "<" + self.BASE + "ABCDEFGHIJKLMNOP>", "untis://setschool?url=https://mese.webuntis.com/WebUntis/&school=x&user=a&key=ABCDEFGHIJKLMNOP"):
            c = self.u.parse_qr(link)
            self.assertIsNotNone(c, link)
            self.assertEqual(c["key"], "ABCDEFGHIJKLMNOP")
            self.assertEqual(c["server"], "mese.webuntis.com")
        self.assertEqual(self.u.parse_qr(self.BASE + "ABCDEFGHIJKLMNOP")["school"], "My School")

    def test_errors_name_the_part_that_is_wrong(self):
        self.assertIn("0 1 8 9", self.u.explain_qr(self.BASE + "ABCD0189"))
        self.assertIn("untis://", self.u.explain_qr("https://x.com/?url=a"))
        self.assertIn("school", self.u.explain_qr("untis://setschool?url=a.webuntis.com&user=u&key=ABCDEFGH"))
        self.assertIn("key", self.u.explain_qr("untis://setschool?url=a.webuntis.com&school=s&user=u"))
        self.assertIn("nothing", self.u.explain_qr("  "))
        self.assertEqual(self.u.explain_qr(self.BASE + "ABCDEFGHIJKLMNOP"), "")
        self.assertIsNone(self.u.parse_qr(self.BASE + "ABCD0189"))

    def test_four_separate_values(self):
        c = self.u.check_credentials("https://mese.webuntis.com/WebUntis/", " My School ", "anna.m", "abcd efgh")
        self.assertEqual(c, dict(server="mese.webuntis.com", school="My School", user="anna.m", key="ABCDEFGH"))
        for bad in (("", "s", "u", "ABCDEFGH"), ("localhost", "s", "u", "ABCDEFGH"), ("a.b.com", "", "u", "ABCDEFGH"), ("a.b.com", "s", "", "ABCDEFGH"), ("a.b.com", "s", "u", ""), ("a.b.com", "s", "u", "ABC!")):
            with self.assertRaises(ValueError, msg=str(bad)):
                self.u.check_credentials(*bad)

    def test_login_hint(self):
        self.assertIn("login name", self.u.login_hint("bad credentials"))
        self.assertEqual(self.u.login_hint("something unrelated"), "")

    def test_ssc_untis_command_with_fields(self):
        import subprocess
        env = dict(os.environ, SSC_VAULT="file")
        r = subprocess.run([sys.executable, str(ROOT / "ssc.py"), "untis", "set", "--server", "a.webuntis.com", "--school", "s", "--user", "u", "--key", "ABCDEFGH"],
                           capture_output=True, text=True, env=env, stdin=subprocess.DEVNULL)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("u @ s", r.stdout)
        r = subprocess.run([sys.executable, str(ROOT / "ssc.py"), "untis", "set", self.BASE + "ABCD0189"], capture_output=True, text=True, env=env, stdin=subprocess.DEVNULL)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("0 1 8 9", r.stderr)
