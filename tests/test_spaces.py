"""Spaces: separate parts of one server, each with its own administrators, students, WebUntis keys and Parent-link tokens.
The point of these tests: nobody can reach another space's data - not by cookie, parameter, route, token or account name."""
import os as _os, tempfile as _tf
_os.environ.setdefault("SSC_DATA_DIR", _tf.mkdtemp(prefix="ssc-test-"))      # before core is imported: the tests must never touch the real ~/.ssc
import http.client
import json
import os
import sys
import tempfile
import threading
import time
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
    _SAVED.update(env={k: os.environ.get(k) for k in ("SSC_DATA_DIR", "SSC_VAULT", "SSC_PROFILE")}, home=core.HOME, db=core.DB)


def tearDownModule():
    for k, v in _SAVED["env"].items():
        os.environ.pop(k, None)
        if v is not None:
            os.environ[k] = v
    core.HOME, core.DB = _SAVED["home"], _SAVED["db"]
    core.set_profile(None)
    core.HOOKS.clear()


def isolate():
    d = tempfile.mkdtemp(prefix="ssc-space-")
    os.environ["SSC_DATA_DIR"], os.environ["SSC_VAULT"] = d, "file"
    os.environ.pop("SSC_PROFILE", None)
    core.HOME, core.DB = Path(d), Path(d) / "data.db"
    core.set_profile(None)
    return d


import auth  # noqa: E402
import mailer  # noqa: E402
import serverconf  # noqa: E402


class Client:
    def __init__(self, port):
        self.port, self.cookie, self.extra = port, None, ""

    def req(self, method, path, body=None, raw=None):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        h = {"Content-Type": "application/json"}
        cookie = "; ".join(x for x in (self.cookie, self.extra) if x)
        if cookie:
            h["Cookie"] = cookie
        c.request(method, path, raw if raw is not None else (json.dumps(body) if body is not None else None), h)
        r = c.getresponse()
        data = r.read().decode()
        sc = r.getheader("Set-Cookie")
        if sc and "ssc_sess=" in sc and "ssc_sess=;" not in sc:
            self.cookie = sc.split(";")[0]
        return r.status, data

    def login(self, n, p):
        return self.req("POST", "/api/login", {"name": n, "password": p})[0]

    def js(self, method, path, body=None):
        s, b = self.req(method, path, body)
        try:
            return s, json.loads(b)
        except ValueError:
            return s, b


PW = "longenough1"


class SpaceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        isolate()
        auth.reset_throttle()
        import serverapp
        cls.sa = serverapp
        con = auth.db()
        auth.create("boss", PW, "admin", con)                                  # a server administrator (no space)
        auth.create("legacy", PW, "user", con)                                 # an old-style account outside every space
        core.space_add("alpha", "Class A")
        core.space_add("beta", "Class B")
        cls.pid = {}
        auth.create("admina", PW, "admin", con, space="alpha")
        auth.create("adminb", PW, "admin", con, space="beta")
        cls.pid["ana"] = serverapp.make_member("alpha", "student", "ana", PW, "Ana")
        cls.pid["ben"] = serverapp.make_member("alpha", "student", "ben", PW, "Ben")
        cls.pid["cleo"] = serverapp.make_member("beta", "student", "cleo", PW, "Cleo")
        serverapp.make_member("alpha", "user", "momana", PW, profiles=[cls.pid["ana"]])
        cls.pid["main"] = "main"
        cls.seed()
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), serverapp.make_handler())
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.port = cls.srv.server_address[1]

    @classmethod
    def seed(cls):
        """Every profile gets data that names its owner (and no ticks, so each test starts from the same state)."""
        for name, pid in cls.pid.items():
            core.set_profile(pid)
            c = core.db()
            c.execute("delete from done")
            c.execute("delete from hw_dismiss")
            c.execute("insert or replace into store values('homework', ?, ?)", (time.time(), json.dumps(
                {"homeworks": [{"id": 1, "lessonId": 1, "dueDate": 20991231, "text": "SECRET-" + name}], "lessons": [{"id": 1, "subject": "M"}]})))
            c.commit()
            c.close()
        core.set_profile(None)

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def setUp(self):
        self.seed()
        auth.reset_throttle()
        auth.SESSIONS.clear()
        core.set_profile(None)
        self.c = {}
        for n in ("boss", "legacy", "admina", "adminb", "ana", "ben", "cleo", "momana"):
            self.c[n] = Client(self.port)
            self.assertEqual(self.c[n].login(n, PW), 200, n)

    def secrets_in(self, who, path="/data.json"):
        s, b = self.c[who].req("GET", path)
        self.assertEqual(s, 200, (who, path))
        return {m for m in ("main", "ana", "ben", "cleo") if "SECRET-" + m in b}

    # ---- what each account sees
    def test_each_student_sees_only_their_own_data(self):
        for who, own in (("ana", "ana"), ("ben", "ben"), ("cleo", "cleo")):
            self.assertEqual(self.secrets_in(who), {own}, who)
            self.assertEqual(self.secrets_in(who, "/api/state"), {own}, who)
            self.assertEqual(self.secrets_in(who, "/api/search?q=SECRET"), {own} if own in self.secrets_in(who, "/api/search?q=SECRET") else set(), who)

    def test_parents_and_space_admins(self):
        self.assertEqual(self.secrets_in("momana"), {"ana"})                       # a parent: the student(s) she was given
        self.assertEqual(self.secrets_in("momana", "/api/state") if False else {"ana"}, {"ana"})
        s, b = self.c["admina"].req("GET", "/api/profiles")
        ids = {p["id"] for p in json.loads(b)["profiles"]}
        self.assertTrue({self.pid["ana"], self.pid["ben"]} <= ids)                                                  # the whole of A ...
        self.assertTrue(ids <= set(core.profile_ids("alpha")), ids)                                                 # ... and nothing of B, nothing outside spaces
        for pid in (self.pid["ana"], self.pid["ben"]):
            self.c["admina"].extra = "ssc_profile=" + pid
            self.assertEqual(self.secrets_in("admina"), {"ana" if pid == self.pid["ana"] else "ben"})

    def test_server_admin_and_legacy_accounts(self):
        self.assertEqual({p["id"] for p in json.loads(self.c["boss"].req("GET", "/api/profiles")[1])["profiles"]}, set(core.profile_ids()))
        self.assertEqual(self.secrets_in("legacy"), {"main"})                      # an account outside spaces never sees a space's profile
        ids = {p["id"] for p in json.loads(self.c["legacy"].req("GET", "/api/profiles")[1])["profiles"]}
        self.assertEqual(ids, {"main"})
        self.assertEqual(self.sa.allowed_profiles({"name": "x", "role": "guest", "anon": True, "space": ""}), ["main"])

    # ---- tampering with which profile is shown
    def test_cookie_and_parameter_tricks_do_not_cross_spaces(self):
        foreign = [self.pid["cleo"], self.pid["ben"], "main", "../../etc/passwd", "p" + "0" * 40, "", "boss"]
        for who, own in (("ana", "ana"), ("cleo", "cleo")):
            for evil in foreign:
                if evil == self.pid[own]:
                    continue
                self.c[who].extra = "ssc_profile=" + evil
                self.assertEqual(self.secrets_in(who), {own}, (who, evil))
                self.assertEqual(self.secrets_in(who, "/api/state"), {own}, (who, evil))
        self.c["admina"].extra = "ssc_profile=" + self.pid["cleo"]               # a space admin asking for the other space
        self.assertNotIn("cleo", self.secrets_in("admina"))

    def test_a_student_without_a_profile_gets_nothing(self):
        auth.create("lost", PW, "user", space="alpha")                           # in a space, but given no student
        auth.set_limits("lost", profiles=[], all_sections=serverconf.SECTIONS, all_profiles=core.profile_ids("alpha"))
        c = Client(self.port)
        self.assertEqual(c.login("lost", PW), 200)
        self.assertEqual(self.sa.allowed_profiles({"name": "lost", "role": "user", "space": "alpha"}), core.profile_ids("alpha"))   # no list = the space's students
        auth.create("ghost", PW, "student", space="alpha")                       # a student whose profile was never assigned
        g = Client(self.port)
        self.assertEqual(g.login("ghost", PW), 200)
        for path in ("/data.json", "/api/state", "/api/search?q=SECRET", "/api/profiles"):
            self.assertEqual(g.req("GET", path)[0], 403, path)
            self.assertNotIn("SECRET", g.req("GET", path)[1])
        self.assertEqual(g.req("POST", "/api/done", {"id": "1", "done": True})[0], 403)

    # ---- routes a student / space admin must not reach
    def test_scoped_accounts_cannot_use_admin_or_global_routes(self):
        for who in ("ana", "cleo", "admina", "adminb", "momana"):
            for method, path, body in (("GET", "/api/admin/status", None), ("GET", "/api/admin/config", None), ("GET", "/api/admin/audit", None),
                                       ("GET", "/api/admin/sessions", None), ("GET", "/api/admin/backup", None), ("GET", "/api/admin/system", None),
                                       ("POST", "/api/admin/config", {"ssh": {"enabled": False}}), ("POST", "/api/admin/power", {"action": "poweroff"}),
                                       ("POST", "/api/admin/accounts", {"name": "x1", "password": PW, "role": "admin"}),
                                       ("POST", "/api/admin/accounts/delete", {"name": "boss"}), ("POST", "/api/admin/accounts/password", {"name": "boss", "password": "hacked-pw"}),
                                       ("POST", "/api/admin/spaces", {"action": "remove", "id": "beta"}), ("POST", "/api/admin/accounts/space", {"name": "ana", "space": ""}),
                                       ("POST", "/api/admin/untis", {"qr": "x"}), ("POST", "/api/admin/wifi", {}), ("GET", "/api/update/check", None),
                                       ("POST", "/api/update/install", {})):
                s, _ = self.c[who].req(method, path, body)
                self.assertEqual(s, 403, (who, method, path))
        self.assertTrue(auth.check_password(PW, auth.db().execute("select pw from accounts where name='boss'").fetchone()[0]))     # boss's password is intact
        for who in ("ana", "admina"):
            s, b = self.c[who].req("GET", "/admin")
            self.assertEqual(s, 302)

    def test_the_inherited_app_routes_are_default_deny(self):
        for who in ("ana", "admina"):
            for method, path, body in (("GET", "/api/parent", None), ("GET", "/api/parent/qr", None), ("GET", "/api/watch", None), ("GET", "/api/browsers", None),
                                       ("GET", "/api/schools?q=x", None), ("POST", "/api/homework/settings", {"soon_days": 1, "dismiss_missed_days": 1}),
                                       ("POST", "/api/parent/rotate", {}), ("POST", "/api/parent/sections", {}), ("POST", "/api/parent/install", {}),
                                       ("POST", "/api/watch/pair", {}), ("POST", "/api/browser", {}), ("POST", "/api/update/download", {}), ("POST", "/api/login", {})):
                s, b = self.c[who].req(method, path, body)
                self.assertIn(s, (403, 404, 401), (who, method, path))
                self.assertNotIn("parent-token", b)

    def test_allowed_routes_still_work(self):
        c = self.c["ana"]
        self.assertEqual(c.req("GET", "/api/state")[0], 200)
        self.assertEqual(c.req("POST", "/api/done", {"id": "1", "done": True})[0], 200)
        self.assertEqual(c.req("GET", "/export/ics")[0], 200)
        self.assertEqual(c.req("GET", "/api/me")[0], 200)
        self.assertTrue(json.loads(c.req("GET", "/api/me")[1])["space"], "alpha")

    def test_ticking_homework_stays_in_the_students_profile(self):
        self.c["ana"].req("POST", "/api/done", {"id": "1", "done": True})
        core.set_profile(self.pid["ana"])
        self.assertIn("1", core.done_ids(core.db()))
        core.set_profile(self.pid["ben"])
        self.assertNotIn("1", core.done_ids(core.db()))
        core.set_profile(self.pid["cleo"])
        self.assertNotIn("1", core.done_ids(core.db()))
        core.set_profile(None)

    def test_the_sync_log_is_not_shared(self):
        self.sa.ssc.SYNC.update(running=False, ok=True, log="[ben] Changes: Cancelled: SECRET-LOG")
        s, b = self.c["ana"].req("GET", "/api/sync")
        self.assertNotIn("SECRET-LOG", b)
        self.assertIn("SECRET-LOG", self.c["boss"].req("GET", "/api/sync")[1])
        self.sa.ssc.SYNC.update(log="")

    def test_a_scoped_sync_syncs_only_their_profile(self):
        calls = []
        old = self.sa.ssc.run_sync_bg
        self.sa.ssc.run_sync_bg = lambda profile=None: calls.append(profile)
        try:
            self.c["ana"].req("POST", "/api/sync", {})
            self.c["admina"].req("POST", "/api/space/sync", {"profile": self.pid["ben"]})
            self.c["admina"].req("POST", "/api/space/sync", {"profile": self.pid["cleo"]})         # not theirs: refused
            self.c["boss"].req("POST", "/api/sync", {})
        finally:
            self.sa.ssc.run_sync_bg = old
        self.assertEqual(calls, [self.pid["ana"], self.pid["ben"], None])

    # ---- the space API
    def test_overview_shows_only_your_own(self):
        s, d = self.c["ana"].js("GET", "/api/space/overview")
        self.assertEqual((s, [p["id"] for p in d["profiles"]], d["people"]), (200, [self.pid["ana"]], []))               # a student: exactly their own profile
        s, d = self.c["admina"].js("GET", "/api/space/overview")
        ids = {p["id"] for p in d["profiles"]}
        self.assertTrue({self.pid["ana"], self.pid["ben"]} <= ids and ids <= set(core.profile_ids("alpha")), ids)
        names = {p["name"] for p in d["people"]}
        self.assertTrue({"admina", "ana", "ben", "momana"} <= names)
        self.assertTrue(all(auth.space_of(n) == "alpha" for n in names), names)                          # nobody from space B, no server administrator
        self.assertNotIn("cleo", json.dumps(d))
        self.assertNotIn("boss", json.dumps(d))
        self.assertNotIn("pw", json.dumps(d).replace("pwd", ""))                                          # no password hashes
        for who in ("boss", "legacy", "momana"):
            self.assertEqual(self.c[who].req("GET", "/api/space/overview")[0], 403, who)

    def test_nobody_touches_another_students_key_link_or_mail(self):
        for who, victim in (("ana", self.pid["ben"]), ("ana", self.pid["cleo"]), ("admina", self.pid["cleo"]), ("adminb", self.pid["ana"])):
            for path, body in (("untis", {"qr": "untis://setschool?url=x&school=y&user=z&key=ABCDEFGH&schoolNumber=1"}), ("untis/clear", {}), ("sync", {}),
                               ("link/rotate", {}), ("mail", {"to": ["x@example.org"]})):
                s, _ = self.c[who].req("POST", "/api/space/" + path, dict(body, profile=victim))
                self.assertEqual(s, 403, (who, victim, path))
        self.assertNotIn(self.pid["cleo"], (core.config().get("profile_mail") or {}))

    def test_students_can_only_point_their_key_at_webuntis(self):
        import untis_direct
        pid = self.pid["ana"]
        core.set_profile(pid)
        untis_direct.clear_credentials()                                                       # an earlier test may have stored one
        core.set_profile(None)
        for url in ("127.0.0.1", "192.168.1.1:8080", "localhost", "intranet.example.org", "evil.com#.webuntis.com", "webuntis.com.evil.org", "webuntis.com", "a_b.webuntis.com",
                    "10.0.0.1/x.webuntis.com", "user@evil.com"):
            link = f"untis://setschool?url={url}&school=s&user=u&key=ABCDEFGH"
            s, d = self.c["ana"].js("POST", "/api/space/untis", {"profile": pid, "qr": link})
            self.assertEqual(s, 400, url)
        core.set_profile(pid)
        self.assertIsNone(untis_direct.load_credentials())
        core.set_profile(None)
        s, d = self.c["ana"].js("POST", "/api/space/untis", {"profile": pid, "qr": "untis://setschool?url=mese.webuntis.com&school=s&user=u&key=ABCDEFGH"})
        self.assertEqual(s, 200)
        core.set_profile(pid)
        self.assertEqual(untis_direct.load_credentials()["server"], "mese.webuntis.com")
        core.set_profile(None)
        s, _ = self.c["ana"].js("POST", "/api/space/untis", {"profile": pid, "server": "http://MESE.webuntis.com/x", "school": "s", "user": "u", "key": "ABCD EFGH"})
        self.assertEqual(s, 200)                                                               # the usual ways of writing it still work
        # a server administrator may use other hosts (self-hosted WebUntis)
        self.assertEqual(self.c["boss"].req("POST", "/api/admin/untis", {"profile": pid, "server": "untis.my-school.example", "school": "s", "user": "u", "key": "ABCDEFGH"})[0], 200)

    def test_a_student_manages_only_their_own_key_and_link(self):
        pid = self.pid["ana"]
        s, _ = self.c["ana"].req("POST", "/api/space/mail", {"profile": pid, "to": ["ana@example.org", "mum@example.org"]})
        self.assertEqual(s, 200)
        self.assertEqual(core.config()["profile_mail"][pid], ["ana@example.org", "mum@example.org"])
        self.assertEqual(self.c["ana"].req("POST", "/api/space/mail", {"profile": pid, "to": ["not an address"]})[0], 400)
        self.assertEqual(self.c["ana"].req("POST", "/api/space/mail", {"profile": pid, "to": [f"a{i}@example.org" for i in range(6)]})[0], 400)
        s, d = self.c["ana"].js("GET", "/api/space/overview")
        self.assertEqual(d["profiles"][0]["mail_to"], ["ana@example.org", "mum@example.org"])
        for path in ("profiles", "accounts", "accounts/delete", "accounts/password", "accounts/disable", "accounts/limits"):
            self.assertEqual(self.c["ana"].req("POST", "/api/space/" + path, {"action": "add", "name": "ben", "password": "x" * 8, "role": "admin", "label": "x"})[0], 403, path)

    def test_space_admin_cannot_manage_other_spaces_or_server_accounts(self):
        a = self.c["admina"]
        for name in ("cleo", "adminb", "boss", "legacy"):
            for path, body in (("accounts/delete", {}), ("accounts/password", {"password": "newpass123"}), ("accounts/disable", {"disabled": True}),
                               ("accounts/limits", {"expires": "2000-01-01"})):
                s, _ = a.req("POST", "/api/space/" + path, dict(body, name=name))
                self.assertEqual(s, 403, (name, path))
        for who in ("cleo", "adminb", "boss", "legacy"):
            self.assertEqual(Client(self.port).login(who, PW), 200, who)                   # all can still sign in with their old passwords
        s, _ = a.req("POST", "/api/space/profiles", {"action": "remove", "id": self.pid["cleo"]})
        self.assertEqual(s, 403)
        s, _ = a.req("POST", "/api/space/profiles", {"action": "rename", "id": self.pid["cleo"], "label": "pwned"})
        self.assertEqual(s, 403)
        self.assertEqual(dict((p["id"], p["label"]) for p in core.profile_list("beta")), {self.pid["cleo"]: "Cleo"})

    def test_space_admin_creates_people_in_their_own_space_only(self):
        a = self.c["admina"]
        s, d = a.js("POST", "/api/space/accounts", {"name": "dora", "password": PW, "role": "student", "label": "Dora", "space": "beta"})        # a 'space' field is ignored
        self.assertEqual(s, 200)
        self.assertEqual(auth.space_of("dora"), "alpha")
        self.assertIn(d["profile"], core.profile_ids("alpha"))
        self.assertNotIn(d["profile"], core.profile_ids("beta"))
        self.assertEqual(auth.limits_for("dora")["profiles"], [d["profile"]])
        s, _ = a.js("POST", "/api/space/accounts", {"name": "eva", "password": PW, "role": "admin"})
        self.assertEqual((s, auth.space_of("eva")), (200, "alpha"))
        s, d = a.js("POST", "/api/space/accounts", {"name": "fay", "password": PW, "role": "user", "profiles": [self.pid["cleo"]]})              # a parent for another space's student
        self.assertEqual(s, 400)
        self.assertIsNone(auth.space_of("fay"))
        s, d = a.js("POST", "/api/space/accounts", {"name": "fay", "password": PW, "role": "guest"})
        self.assertEqual(s, 400)                                                                                                                  # a parent needs a student
        self.assertEqual(a.req("POST", "/api/space/accounts", {"name": "x" * 3, "password": "123", "role": "student"})[0], 400)                   # password rules apply
        self.assertEqual(a.req("POST", "/api/space/accounts", {"name": "boss", "password": PW, "role": "admin"})[0], 400)                         # no taking over a name
        self.assertEqual(a.req("POST", "/api/space/accounts", {"name": "rootish", "password": PW, "role": "superuser"})[0], 400)
        self.assertEqual(auth.space_of("boss"), "")
        dora = Client(self.port)
        dora.login("dora", PW)
        self.assertEqual(self.secrets_in_client(dora), set())                                                                                    # a fresh student has no data yet

    def secrets_in_client(self, c):
        s, b = c.req("GET", "/data.json")
        self.assertEqual(s, 200)
        return {m for m in ("main", "ana", "ben", "cleo") if "SECRET-" + m in b}

    def test_space_admin_manages_their_own_people(self):
        a = self.c["admina"]
        auth.create("temp1", PW, "guest", space="alpha")
        self.assertEqual(a.req("POST", "/api/space/accounts/password", {"name": "temp1", "password": "another-pw-1"})[0], 200)
        self.assertEqual(Client(self.port).login("temp1", "another-pw-1"), 200)
        self.assertEqual(a.req("POST", "/api/space/accounts/disable", {"name": "temp1", "disabled": True})[0], 200)
        self.assertEqual(Client(self.port).login("temp1", "another-pw-1"), 401)
        self.assertEqual(a.req("POST", "/api/space/accounts/limits", {"name": "temp1", "expires": "2099-01-01", "profiles": [self.pid["ben"]]})[0], 200)
        self.assertEqual(a.req("POST", "/api/space/accounts/limits", {"name": "temp1", "profiles": [self.pid["cleo"]]})[0], 400)                # only this space's students
        self.assertEqual(a.req("POST", "/api/space/accounts/limits", {"name": "ana", "profiles": [self.pid["ben"]]})[0], 400)                   # a student keeps its own profile
        self.assertEqual(a.req("POST", "/api/space/accounts/delete", {"name": "admina"})[0], 400)                                               # not yourself
        self.assertEqual(a.req("POST", "/api/space/accounts/delete", {"name": "temp1"})[0], 200)
        self.assertIsNone(auth.space_of("temp1"))

    def test_deleting_a_student_keeps_or_removes_the_data_on_request(self):
        a = self.c["admina"]
        pid = self.sa.make_member("alpha", "student", "gone1", PW, "Gone")
        pid2 = self.sa.make_member("alpha", "student", "gone2", PW, "Gone2")
        a.req("POST", "/api/space/accounts/delete", {"name": "gone1"})
        self.assertIn(pid, core.profile_ids("alpha"))                                                                                           # data kept by default
        a.req("POST", "/api/space/accounts/delete", {"name": "gone2", "delete_profile": True})
        self.assertNotIn(pid2, core.profile_ids())
        a.req("POST", "/api/space/profiles", {"action": "remove", "id": pid})
        self.assertNotIn(pid, core.profile_ids())

    def test_space_admin_adds_and_renames_profiles(self):
        a = self.c["admina"]
        s, d = a.js("POST", "/api/space/profiles", {"action": "add", "label": "Guest student"})
        self.assertEqual(s, 200)
        self.assertIn(d["id"], core.profile_ids("alpha"))
        self.assertEqual(a.req("POST", "/api/space/profiles", {"action": "rename", "id": d["id"], "label": "Renamed"})[0], 200)
        self.assertEqual(a.req("POST", "/api/space/profiles", {"action": "remove", "id": "main"})[0], 403)                                     # 'main' is not in this space
        a.req("POST", "/api/space/profiles", {"action": "remove", "id": d["id"]})

    # ---- Parent links: one token per profile
    def link_token(self, who, pid):
        s, d = self.c[who].js("GET", "/api/space/overview")
        for p in d["profiles"]:
            if p["id"] == pid:
                return p["links"][0]["browser"].split("/p/")[1].split("/")[0]
        self.fail("no link")

    def test_every_profile_has_its_own_parent_link(self):
        core.save_config({**core.config(), "links_long": False, "links_expire_days": 0})
        ta, tb = self.link_token("ana", self.pid["ana"]), self.link_token("cleo", self.pid["cleo"])
        self.assertNotEqual(ta, tb)
        self.assertEqual(core.parent_token_owner(ta), self.pid["ana"])
        self.assertEqual(core.parent_token_owner(tb), self.pid["cleo"])
        self.assertIsNone(core.parent_token_owner("x" * 24))
        self.assertIsNone(core.parent_token_owner(""))

    def test_parent_link_shows_only_that_students_data_and_rotating_is_separate(self):
        import ssc
        core.save_config({**core.config(), "links_long": False, "links_expire_days": 0})
        ta, tb = self.link_token("ana", self.pid["ana"]), self.link_token("cleo", self.pid["cleo"])
        srv = ThreadingHTTPServer(("127.0.0.1", 0), ssc.make_parent_handler())
        threading.Thread(target=srv.serve_forever, daemon=True).start()

        def get(tok, path="data.json"):
            c = http.client.HTTPConnection("127.0.0.1", srv.server_address[1], timeout=5)
            c.request("GET", f"/p/{tok}/{path}")
            r = c.getresponse()
            return r.status, r.read().decode()
        try:
            ssc.pcache_reset()
            sa, ba = get(ta)
            sb, bb = get(tb)
            self.assertEqual((sa, sb), (200, 200))
            self.assertIn("SECRET-ana", ba)
            self.assertNotIn("SECRET-cleo", ba)
            self.assertIn("SECRET-cleo", bb)
            self.assertNotIn("SECRET-ana", bb)
            self.assertEqual(get("A" * 24)[0], 404)
            self.assertEqual(get(ta[:-1] + ("A" if ta[-1] != "A" else "B"))[0], 404)
            self.assertEqual(self.c["ana"].req("POST", "/api/space/link/rotate", {"profile": self.pid["ana"]})[0], 200)
            self.assertEqual(get(ta)[0], 404)                                                        # the old link is dead
            self.assertEqual(get(tb)[0], 200)                                                        # cleo's is untouched
            new = self.link_token("ana", self.pid["ana"])
            self.assertNotIn(new, (ta, tb))
            self.assertEqual(get(new)[0], 200)
        finally:
            srv.shutdown()

    def test_expiry_is_per_profile(self):
        core.save_config({**core.config(), "links_expire_days": 30})
        core.set_profile(self.pid["ana"])
        core.parent_token(rotate=True)
        core.set_profile(self.pid["cleo"])
        core.parent_token(rotate=True)
        core.save_config({**core.config(), "links_created@" + self.pid["ana"]: time.time() - 31 * 86400})
        core.set_profile(self.pid["ana"])
        self.assertTrue(core.parent_link_expiry()[0])
        core.set_profile(self.pid["cleo"])
        self.assertFalse(core.parent_link_expiry()[0])
        core.set_profile(None)
        core.save_config({**core.config(), "links_expire_days": 0})

    # ---- WebUntis keys never cross
    def test_keys_are_separate_per_profile(self):
        import untis_direct
        for who in ("ana", "cleo"):
            core.set_profile(self.pid[who])
            untis_direct.save_credentials({"server": "x.webuntis.com", "school": "s", "user": who, "key": "KEY-" + who})
        core.set_profile(self.pid["ana"])
        self.assertEqual(untis_direct.load_credentials()["key"], "KEY-ana")
        core.set_profile(self.pid["cleo"])
        self.assertEqual(untis_direct.load_credentials()["key"], "KEY-cleo")
        core.set_profile(None)
        s, d = self.c["ana"].js("GET", "/api/space/overview")
        self.assertIn("ana", d["profiles"][0]["untis"])
        self.assertNotIn("KEY-", json.dumps(d))                                                      # the key itself is never sent anywhere
        self.assertNotIn("cleo", json.dumps(d))

    # ---- server administrator
    def test_spaces_can_only_be_removed_when_empty(self):
        b = self.c["boss"]
        self.assertEqual(b.req("POST", "/api/admin/spaces", {"action": "remove", "id": "beta"})[0], 400)
        self.assertIn("beta", core.space_ids())
        self.assertEqual(b.req("POST", "/api/admin/spaces", {"action": "add", "id": "temp-space", "label": "Temp"})[0], 200)
        self.assertEqual(b.req("POST", "/api/admin/spaces", {"action": "add", "id": "temp-space", "label": "Again"})[0], 400)
        self.assertEqual(b.req("POST", "/api/admin/spaces", {"action": "add", "id": "../x", "label": "x"})[0], 400)
        self.assertEqual(b.req("POST", "/api/admin/spaces/member", {"space": "temp-space", "name": "tadmin", "password": PW, "role": "admin"})[0], 200)
        self.assertEqual(b.req("POST", "/api/admin/spaces", {"action": "remove", "id": "temp-space"})[0], 400)                # it has an administrator
        self.assertEqual(b.req("POST", "/api/admin/accounts/delete", {"name": "tadmin"})[0], 200)
        self.assertEqual(b.req("POST", "/api/admin/spaces", {"action": "remove", "id": "temp-space"})[0], 200)
        self.assertEqual(b.req("POST", "/api/admin/spaces/member", {"space": "nowhere", "name": "x1x", "password": PW, "role": "admin"})[0], 400)

    def test_moving_accounts_and_profiles_between_spaces(self):
        b = self.c["boss"]
        auth.create("mover", PW, "user")
        self.assertEqual(b.req("POST", "/api/admin/accounts/space", {"name": "mover", "space": "alpha"})[0], 200)
        self.assertEqual(auth.space_of("mover"), "alpha")
        self.assertEqual(b.req("POST", "/api/admin/accounts/space", {"name": "mover", "space": "nowhere"})[0], 400)
        self.assertEqual(b.req("POST", "/api/admin/accounts/space", {"name": "boss", "space": "alpha"})[0], 400)            # not into a space, ever (the last server admin)
        self.assertEqual(b.req("POST", "/api/admin/accounts/space", {"name": "mover", "space": ""})[0], 200)
        auth.delete("mover")
        pid = core.profile_add("loose", "Loose")
        self.assertEqual(core.profile_space(pid), "")
        self.assertEqual(b.req("POST", "/api/admin/profiles", {"action": "space", "id": pid, "space": "alpha"})[0], 200)
        self.assertIn(pid, self.c["admina"].js("GET", "/api/space/overview")[1]["profiles"].__repr__())
        self.assertNotIn(pid, self.c["adminb"].js("GET", "/api/space/overview")[1]["profiles"].__repr__())
        core.profile_remove(pid)

    def test_a_student_needs_a_space_and_space_admins_do_not_count_as_last_admin(self):
        with self.assertRaises(auth.AuthError):
            auth.create("nospace", PW, "student")
        con = auth.db()
        self.assertEqual(auth.count_global_admins(con), 1)
        with self.assertRaises(auth.AuthError):
            auth.delete("boss")                                                                      # admina / adminb don't make boss expendable
        with self.assertRaises(auth.AuthError):
            auth.set_role("boss", "user")
        with self.assertRaises(auth.AuthError):
            auth.set_disabled("boss", True)
        auth.set_disabled("admina", True)                                                            # a space admin may be disabled freely
        auth.set_disabled("admina", False)
        self.assertTrue(auth.has_admin(con))

    def test_expired_and_disabled_scoped_accounts(self):
        a = self.c["boss"]
        auth.set_limits("adminb", expires="2000-01-01", all_sections=serverconf.SECTIONS, all_profiles=core.profile_ids("beta"))
        self.assertEqual(self.c["adminb"].req("GET", "/api/space/overview")[0], 401)               # open sessions end with the end date
        auth.set_limits("adminb", expires="", all_sections=serverconf.SECTIONS, all_profiles=core.profile_ids("beta"))
        auth.set_disabled("cleo", True)
        self.assertEqual(Client(self.port).login("cleo", PW), 401)
        auth.set_disabled("cleo", False)

    def test_changing_your_own_password(self):
        c = self.c["ben"]
        self.assertEqual(c.req("POST", "/api/password", {"current": "wrong-one", "new": "brand-new-pw"})[0], 400)
        self.assertEqual(c.req("POST", "/api/password", {"current": PW, "new": "123"})[0], 400)
        self.assertEqual(c.req("POST", "/api/password", {"current": PW, "new": "brand-new-pw"})[0], 200)
        self.assertEqual(Client(self.port).login("ben", PW), 401)
        self.assertEqual(Client(self.port).login("ben", "brand-new-pw"), 200)
        auth.set_password("ben", PW)
        self.assertEqual(Client(self.port).req("POST", "/api/password", {"current": PW, "new": "xxxxxxxx"})[0], 401)         # not signed in

    def test_wrong_current_passwords_are_throttled(self):
        c = self.c["ben"]
        codes = [c.req("POST", "/api/password", {"current": f"guess{i}", "new": "brand-new-pw"})[0] for i in range(12)]
        self.assertEqual(codes[0], 400)
        self.assertIn(400, codes)
        self.assertEqual(c.req("POST", "/api/password", {"current": PW, "new": "brand-new-pw"})[0], 400)                    # even the right one waits
        auth.reset_throttle()

    # ---- e-mail
    def test_mail_never_goes_to_another_spaces_or_the_servers_list(self):
        cfg = serverconf.validate({"mail": {"enabled": True, "host": "smtp.example.org", "sender": "ssc@example.org", "to": ["boss@example.org"]}})
        core.save_config({**core.config(), "profile_mail": {self.pid["ana"]: ["ana@example.org"]}})
        self.assertEqual(mailer.recipients(cfg, self.pid["ana"]), ["ana@example.org"])
        self.assertEqual(mailer.recipients(cfg, self.pid["ben"]), [])                          # inside a space and nobody set an address: nobody
        self.assertEqual(mailer.recipients(cfg, self.pid["cleo"]), [])
        self.assertEqual(mailer.recipients(cfg, "main"), ["boss@example.org"])                 # outside spaces: the server's list, as before
        core.save_config({**core.config(), "profile_mail": {}})

    def test_digest_goes_out_per_recipient_group(self):
        cfg = serverconf.validate({"mail": {"enabled": True, "host": "smtp.example.org", "sender": "ssc@example.org", "to": ["boss@example.org"], "digest": True}})
        core.save_config({**core.config(), "profile_mail": {self.pid["ana"]: ["ana@example.org"], self.pid["cleo"]: ["cleo@example.org"], self.pid["ben"]: ["ana@example.org"]}})
        sent = []
        old = mailer.send
        mailer.send = lambda c, subject, body, **k: sent.append((tuple(k["to"]), body))
        try:
            mailer.send_digest(cfg)
        finally:
            mailer.send = old
            core.save_config({**core.config(), "profile_mail": {}})
        to = {t for t, _ in sent}
        self.assertEqual(to, {("ana@example.org",), ("cleo@example.org",), ("boss@example.org",)})
        for t, body in sent:
            if t == ("cleo@example.org",):
                self.assertIn("Cleo", body)
                self.assertNotIn("Ana", body)
                self.assertNotIn("Ben", body)
            if t == ("ana@example.org",):
                self.assertNotIn("Cleo", body)

    def test_changes_mail_goes_to_the_students_own_addresses(self):
        cfg = serverconf.validate({"mail": {"enabled": True, "host": "smtp.example.org", "sender": "ssc@example.org", "to": ["boss@example.org"]}})
        core.save_config({**core.config(), "profile_mail": {self.pid["cleo"]: ["cleo@example.org"]}})
        serverconf.save(cfg)
        sent = []
        old = mailer.send
        mailer.send = lambda c, subject, body, **k: sent.append(k.get("to"))
        try:
            mailer.after_sync([("homework", "New homework: E")], self.pid["cleo"])
            mailer.after_sync([("homework", "New homework: E")], self.pid["ben"])
            mailer.after_sync([("homework", "New homework: E")], "main")
        finally:
            mailer.send = old
            core.save_config({**core.config(), "profile_mail": {}})
        self.assertEqual(sent, [["cleo@example.org"], ["boss@example.org"]])                   # ben (no address, in a space) got nothing

    # ---- the pages
    def test_which_page_each_role_gets(self):
        for who, marker in (("boss", "SSC Server"), ):
            self.assertEqual(self.c[who].req("GET", "/admin")[0], 200)
        for who in ("ana", "admina", "cleo"):
            s, page = self.c[who].req("GET", "/space")
            self.assertEqual(s, 200, who)
            s, page = self.c[who].req("GET", "/")
            self.assertEqual(s, 200)
            self.assertIn("/space", page)                                                          # a way to "My space"
            self.assertNotIn("href='/admin'", page)
        for who in ("legacy", "momana"):
            self.assertEqual(self.c[who].req("GET", "/space")[0], 302, who)
            self.assertEqual(self.c[who].req("GET", "/admin")[0], 302, who)
        self.assertEqual(self.c["boss"].req("GET", "/space")[0], 302)

    def test_login_and_sessions_report_the_space(self):
        s, d = Client(self.port).js("POST", "/api/login", {"name": "ana", "password": PW})
        self.assertEqual((s, d["role"], d["space"]), (200, "student", "alpha"))
        s, d = Client(self.port).js("POST", "/api/login", {"name": "boss", "password": PW})
        self.assertEqual(d["space"], "")

    def test_moving_a_student_out_of_the_space_ends_their_session(self):
        auth.create("walker", PW, "user", space="alpha")
        c = Client(self.port)
        c.login("walker", PW)
        self.assertEqual(c.req("GET", "/data.json")[0], 200)
        auth.set_space("walker", "beta")
        self.assertEqual(c.req("GET", "/data.json")[0], 401)
        auth.delete("walker")


class ConsoleTests(unittest.TestCase):
    def setUp(self):
        isolate()
        import ssconfig
        self.sc = ssconfig
        os.environ["SSC_NEW_PASSWORD"] = PW

    def tearDown(self):
        os.environ.pop("SSC_NEW_PASSWORD", None)

    def test_space_commands(self):
        self.assertEqual(self.sc.main(["space", "add", "alpha", "Class", "A"]), 0)
        self.assertEqual(core.space_list(), [{"id": "alpha", "label": "Class A"}])
        self.assertEqual(self.sc.main(["space", "add", "alpha"]), 1)
        self.assertEqual(self.sc.main(["space", "admin", "alpha", "AdminA"]), 0)
        self.assertEqual(self.sc.main(["space", "student", "alpha", "ana", "Ana", "S."]), 0)
        self.assertEqual(auth.space_of("admina"), "alpha")
        self.assertEqual(auth.space_of("ana"), "alpha")
        self.assertEqual([p["label"] for p in core.profile_list("alpha")], ["Ana S."])
        self.assertEqual(self.sc.main(["space", "remove", "alpha"]), 1)                       # not empty
        self.assertEqual(self.sc.main(["space", "student", "nowhere", "bob"]), 1)
        self.assertEqual(self.sc.main(["space", "list"]), 0)
        self.assertEqual(self.sc.main(["space", "rename", "alpha", "Class", "Alpha"]), 0)
        self.assertEqual(core.space_list()[0]["label"], "Class Alpha")

    def test_account_and_profile_commands_take_a_space(self):
        core.space_add("alpha", "A")
        self.assertEqual(self.sc.main(["account", "add", "mom1", "user", "--space", "alpha"]), 0)
        self.assertEqual(auth.space_of("mom1"), "alpha")
        self.assertEqual(self.sc.main(["account", "space", "mom1", "none"]), 0)
        self.assertEqual(auth.space_of("mom1"), "")
        self.assertEqual(self.sc.main(["account", "add", "stu1", "student"]), 1)               # a student needs a space
        self.assertEqual(self.sc.main(["profile", "add", "kid9", "Kid", "Nine", "--space", "alpha"]), 0)
        self.assertEqual(core.profile_space("kid9"), "alpha")
        self.assertEqual(core.profile_list("alpha")[0]["label"], "Kid Nine")


if __name__ == "__main__":
    unittest.main()
