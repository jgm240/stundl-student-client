"""Wake-up test + Wake-on-LAN, health alerts, HTTPS, account limits, e-mail, automatic updates, several children (profiles)."""
import os as _os, tempfile as _tf
_os.environ.setdefault("SSC_DATA_DIR", _tf.mkdtemp(prefix="ssc-test-"))      # before core is imported: the tests must never touch the real ~/.ssc
import base64
import datetime
import hashlib
import http.client
import json
import os
import shutil
import smtplib
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
    d = tempfile.mkdtemp(prefix="ssc-feat-")
    os.environ["SSC_DATA_DIR"], os.environ["SSC_VAULT"] = d, "file"
    os.environ.pop("SSC_PROFILE", None)
    core.HOME, core.DB = Path(d), Path(d) / "data.db"
    core.set_profile(None)
    return d


import applycfg  # noqa: E402
import auth  # noqa: E402
import autoupdate  # noqa: E402
import health  # noqa: E402
import mailer  # noqa: E402
import serverconf  # noqa: E402
import tls  # noqa: E402
import wake  # noqa: E402


class R:
    def __init__(self, rc=0, out="", err=""):
        self.returncode, self.stdout, self.stderr = rc, out, err


# ------------------------------------------------------------------ settings
class SettingsTests(unittest.TestCase):
    def test_defaults_are_off_and_valid(self):
        c = serverconf.validate({})
        self.assertFalse(c["mail"]["enabled"])
        self.assertFalse(c["power"]["wol"])
        self.assertFalse(c["autoupdate"]["enabled"])
        self.assertEqual(c["links"]["expire_days"], 0)

    def test_mail_validation(self):
        ok = serverconf.validate({"mail": {"enabled": True, "host": "smtp.example.org", "to": "a@b.org, c@d.at", "sender": "ssc@example.org"}})
        self.assertEqual(ok["mail"]["to"], ["a@b.org", "c@d.at"])
        for bad in ({"enabled": True, "host": "smtp.example.org", "to": ["a@b.org"], "sender": "nope"},
                    {"to": ["a@b.org\nBcc: x@y.org"]},                                  # header injection
                    {"enabled": True, "host": "smtp.example.org", "to": [], "sender": "a@b.org"},
                    {"enabled": True, "host": "", "to": ["a@b.org"], "sender": "a@b.org"},
                    {"enabled": True, "host": "bad host", "to": ["a@b.org"], "sender": "a@b.org"},
                    {"security": "ftp"}, {"kinds": ["messages"]}, {"digest_time": "25:00"}, {"to": ["a@b.org"] * 11}, {"port": 0}):
            with self.assertRaises(ValueError, msg=bad):
                serverconf.validate({"mail": bad})

    def test_other_limits(self):
        for bad in ({"links": {"expire_days": -1}}, {"links": {"expire_days": "x"}}, {"wol": {"devices": [{"name": "x", "mac": "zz"}]}},
                    {"wol": {"devices": [{"name": "", "mac": "aa:bb:cc:dd:ee:ff"}]}}, {"health": {"disk_warn": 90, "disk_crit": 80}},
                    {"health": {"temp_warn": 5}}, {"autoupdate": {"time": "4am"}}):
            with self.assertRaises(ValueError, msg=bad):
                serverconf.validate(bad)
        self.assertEqual(serverconf.normalize_mac("AA-BB-CC-DD-EE-FF"), "aa:bb:cc:dd:ee:ff")
        self.assertEqual(serverconf.normalize_mac("aabbccddeeff"), "aa:bb:cc:dd:ee:ff")

    def test_wol_is_written_into_the_network_file(self):
        on = serverconf.validate({"power": {"wol": True}})
        off = serverconf.validate({})
        self.assertIn("post-up ethtool -s eth0 wol g", serverconf.render_network(on, "eth0"))
        self.assertNotIn("ethtool", serverconf.render_network(off, "eth0"))
        wifi = serverconf.validate({"power": {"wol": True}, "wifi": {"enabled": True, "ssid": "Home"}})
        out = serverconf.render_network(wifi, "eth0", "wlan0")
        self.assertIn("ethtool -s eth0 wol g", out)                                  # the wired port only
        self.assertNotIn("ethtool -s wlan0", out)


# ------------------------------------------------------------------ wake
class WakeTests(unittest.TestCase):
    def setUp(self):
        self.d = isolate()

    def test_magic_packet(self):
        p = wake.magic_packet("AA:BB:CC:DD:EE:FF")
        self.assertEqual(len(p), 102)
        self.assertEqual(p[:6], b"\xff" * 6)
        self.assertEqual(p[6:12], bytes.fromhex("aabbccddeeff"))
        self.assertEqual(p[-6:], bytes.fromhex("aabbccddeeff"))
        with self.assertRaises(ValueError):
            wake.magic_packet("nonsense")

    def test_send_goes_to_every_broadcast_address(self):
        sent = []

        class Sock:
            def setsockopt(self, *a):
                pass

            def sendto(self, data, addr):
                sent.append((data, addr))

            def close(self):
                pass
        targets = wake.broadcasts(["192.168.1.5/24", "10.0.0.7/16", "junk"])
        self.assertEqual(targets, ["255.255.255.255", "192.168.1.255", "10.0.255.255"])
        self.assertEqual(wake.send("aa:bb:cc:dd:ee:ff", targets, make_socket=Sock), 3)
        self.assertTrue(all(a[1] == 9 and len(d) == 102 for d, a in sent))

    def test_own_macs_skips_wifi_and_virtual(self):
        root = Path(tempfile.mkdtemp())
        for name, mac in (("eth0", "AA:BB:CC:00:00:01"), ("wlan0", "AA:BB:CC:00:00:02"), ("docker0", "AA:BB:CC:00:00:03"), ("lo", "00:00:00:00:00:00")):
            (root / "sys/class/net" / name).mkdir(parents=True)
            (root / "sys/class/net" / name / "address").write_text(mac + "\n")
        (root / "sys/class/net/wlan0/wireless").mkdir()
        self.assertEqual(wake.own_macs(root), [{"interface": "eth0", "mac": "aa:bb:cc:00:00:01"}])

    def test_verdicts(self):
        base = {"started": 1000, "seconds": 120, "rc": 0, "error": ""}
        self.assertEqual(wake.verdict(dict(base, finished=None))[0], "running")
        self.assertEqual(wake.verdict(dict(base, finished=1122))[0], "ok")                       # woke at the alarm
        self.assertEqual(wake.verdict(dict(base, finished=1020))[0], "early")                    # something else woke it
        self.assertEqual(wake.verdict(dict(base, finished=1900))[0], "late")                     # somebody pressed a key: the alarm did not work
        v = wake.verdict(dict(base, finished=1001, rc=1, error="rtcwake: no alarm"))
        self.assertEqual(v[0], "failed")
        self.assertIn("rtcwake: no alarm", v[1])

    def test_run_test_records_start_and_end(self):
        clock = iter([1000.0, 1121.5])
        seen = []
        m = wake.run_test(lambda c: seen.append(c) or R(), now=lambda: next(clock))
        self.assertEqual(seen, [["rtcwake", "-m", "mem", "-s", "120"]])
        self.assertEqual((m["started"], m["finished"]), (1000.0, 1121.5))
        last = wake.last_test()
        self.assertEqual(last["verdict"][0], "ok")
        self.assertIsNone(wake.last_test.__call__() and None)

    def test_failed_suspend_is_reported(self):
        wake.run_test(lambda c: R(1, "", "rtcwake: cannot set alarm"), now=time.time)
        self.assertEqual(wake.last_test()["verdict"][0], "failed")


# ------------------------------------------------------------------ root helper parts
class RootPartsTests(unittest.TestCase):
    def setUp(self):
        self.d = isolate()
        self.root = Path(self.d) / "root"
        (self.root / "sys/class/rtc/rtc0").mkdir(parents=True)
        (self.root / "sys/power").mkdir(parents=True)
        (self.root / "sys/power/state").write_text("freeze mem disk\n")

    def apply(self, parts, cfg=None, run=None):
        seen = []
        res = applycfg.apply(parts, cfg or serverconf.validate({}), self.root, run or (lambda c: seen.append(c) or R()))
        return res, seen

    def test_waketest_needs_rtc_and_suspend(self):
        res, seen = self.apply(["waketest"])
        self.assertTrue(res["ok"], res)
        self.assertIn(["systemd-run", "--no-block", "--on-active=5", "/usr/local/bin/ssconfig", "waketest-run"], seen)
        shutil.rmtree(self.root / "sys/class/rtc")
        res, _ = self.apply(["waketest"])
        self.assertFalse(res["ok"])
        self.assertIn("no hardware clock", res["failed"][0])
        (self.root / "sys/class/rtc/rtc0").mkdir(parents=True)
        (self.root / "sys/power/state").write_text("freeze\n")
        res, _ = self.apply(["waketest"])
        self.assertIn("can't suspend", res["failed"][0])

    def test_power_off_and_wake(self):
        res, seen = self.apply(["poweroff-wake:+90", "web"])
        self.assertTrue(res["ok"], res)
        last = seen[-1]
        self.assertEqual(last[:5], ["systemd-run", "--no-block", "--on-active=5", "rtcwake", "-m"])
        self.assertEqual(last[5], "off")
        self.assertAlmostEqual(int(last[7]), time.time() + 90 * 60, delta=30)
        shutil.rmtree(self.root / "sys/class/rtc")
        res, _ = self.apply(["poweroff-wake:+90"])
        self.assertFalse(res["ok"])

    def test_wake_epoch(self):
        now = time.mktime((2026, 11, 22, 23, 0, 0, 0, 0, -1))
        self.assertEqual(applycfg.wake_epoch("+60", now), int(now + 3600))
        morning = applycfg.wake_epoch("06:00", now)
        self.assertEqual(time.strftime("%Y-%m-%d %H:%M", time.localtime(morning)), "2026-11-23 06:00")        # the next 06:00, not the past one
        for bad in ("6am", "+0", "+99999999", "25:00", ""):
            with self.assertRaises(RuntimeError, msg=bad):
                applycfg.wake_epoch(bad, now)

    def test_only_valid_parts_come_through_the_request_file(self):
        req = serverconf.state_dir() / "apply" / "request.json"
        req.parent.mkdir(parents=True, exist_ok=True)
        req.write_text(json.dumps({"parts": ["waketest", "poweroff-wake:06:00", "poweroff-wake:; rm -rf /", "poweroff-wake:+5x", "format-disk"]}))
        seen = []
        res = applycfg.process_request(self.root, lambda c: seen.append(c) or R())
        flat = [" ".join(c) for c in seen]
        self.assertFalse(any("rm -rf" in c or "format" in c for c in flat))
        self.assertEqual(len([c for c in flat if "rtcwake -m off" in c]), 1)              # one power action at most

    def test_wol_applies_ethtool(self):
        (self.root / "sys/class/net/eth0").mkdir(parents=True)
        res, seen = self.apply(["power"], serverconf.validate({"power": {"wol": True}}))
        self.assertTrue(res["ok"], res)
        self.assertIn(["ethtool", "-s", "eth0", "wol", "g"], seen)

    def test_https_makes_a_certificate_before_restarting(self):
        cfg = serverconf.validate({"web": {"https": True}, "network": {"hostname": "schule"}})
        order = []

        def run(c):
            order.append(c[0])
            if c[0] == "ip":
                return R(0, json.dumps([{"ifname": "eth0", "addr_info": [{"family": "inet", "local": "192.168.1.9", "prefixlen": 24}]}]))
            if c[0] == "openssl":
                Path(c[c.index("-keyout") + 1]).write_text("KEY")
                Path(c[c.index("-out") + 1]).write_text("CERT")
                self.assertIn("IP:192.168.1.9", c[c.index("-addext") + 1])
                self.assertIn("DNS:schule", c[c.index("-addext") + 1])
            return R()
        res, _ = self.apply(["web"], cfg, run)
        self.assertTrue(res["ok"], res)
        self.assertLess(order.index("openssl"), order.index("systemctl"))                # the certificate exists before the service restarts
        self.assertTrue(tls.exists(self.root))
        n = order.count("openssl")
        self.apply(["web"], cfg, run)
        self.assertEqual(order.count("openssl"), n)                                      # nothing changed: the same certificate is kept

    def test_failed_certificate_stops_the_restart(self):
        cfg = serverconf.validate({"web": {"https": True}})
        res, seen = self.apply(["web"], cfg, lambda c: R(1, "", "openssl: boom") if c[0] == "openssl" else R(0, "[]"))
        self.assertFalse(res["ok"])
        self.assertIn("could not make a certificate", res["failed"][0])

    def test_update_rolls_back_when_the_new_version_does_not_start(self):
        import updater
        calls = []
        old = (updater.install_server, updater.rollback, applycfg.HEALTH_CHECK)
        try:
            updater.install_server = lambda **k: calls.append("install") or "1.2.3"
            updater.rollback = lambda **k: calls.append("rollback") or "1.0.0"
            applycfg.HEALTH_CHECK = lambda cfg: False
            res, _ = self.apply(["update"])
            self.assertFalse(res["ok"])
            self.assertEqual(calls, ["install", "rollback"])
            self.assertIn("previous version (1.0.0) was put back", res["failed"][0])
            calls.clear()
            applycfg.HEALTH_CHECK = lambda cfg: True
            res, _ = self.apply(["update"])
            self.assertTrue(res["ok"], res)
            self.assertEqual(calls, ["install"])
        finally:
            updater.install_server, updater.rollback, applycfg.HEALTH_CHECK = old


class TlsTests(unittest.TestCase):
    def test_fingerprint(self):
        der = b"not really a certificate"
        pem = "-----BEGIN CERTIFICATE-----\n" + base64.encodebytes(der).decode() + "-----END CERTIFICATE-----\n"
        h = hashlib.sha256(der).hexdigest().upper()
        self.assertEqual(tls.fingerprint(pem), ":".join(h[i:i + 2] for i in range(0, 64, 2)))
        self.assertEqual(tls.fingerprint("junk"), "")

    @unittest.skipUnless(shutil.which("openssl"), "openssl not installed")
    def test_a_real_certificate_loads(self):
        import ssl
        import subprocess
        root = Path(tempfile.mkdtemp())
        tls.generate("schule", ["192.168.1.9"], lambda c: subprocess.run(c, capture_output=True, text=True), root)
        ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER).load_cert_chain(root / "etc/ssc/tls/cert.pem", root / "etc/ssc/tls/key.pem")
        self.assertEqual(len(tls.describe(root)["fingerprint"].split(":")), 32)

    def test_bad_hostname_is_refused(self):
        with self.assertRaises(RuntimeError):
            tls.generate("bad host; rm", [], lambda c: R(), Path(tempfile.mkdtemp()))


# ------------------------------------------------------------------ health
class HealthTests(unittest.TestCase):
    H = serverconf.DEFAULTS["health"]

    def setUp(self):
        isolate()

    def ids(self, info, scan=None, **k):
        return {a["id"]: a["level"] for a in health.alerts(info, scan, self.H, **k)}

    def test_thresholds(self):
        self.assertEqual(self.ids({"disk": {"percent": 50, "used_gb": 1, "total_gb": 2}}), {})
        self.assertEqual(self.ids({"disk": {"percent": 88, "used_gb": 1, "total_gb": 2}}), {"disk": "warn"})
        self.assertEqual(self.ids({"disk": {"percent": 97, "used_gb": 1, "total_gb": 2}}), {"disk": "crit"})
        self.assertEqual(self.ids({"temperatures": [{"celsius": 60}, {"celsius": 83}]}), {"temp": "warn"})
        self.assertEqual(self.ids({"temperatures": [{"celsius": 95}]}), {"temp": "crit"})
        self.assertEqual(self.ids({"memory": {"percent": 97}}), {"mem": "warn"})

    def test_battery_and_power_cut(self):
        self.assertEqual(self.ids({"battery": {"percent": 80, "ac_online": True}}), {})
        self.assertEqual(self.ids({"battery": {"percent": 80, "ac_online": False}}), {"ac": "warn"})          # a power cut
        self.assertEqual(self.ids({"battery": {"percent": 10, "ac_online": False}}), {"battery": "crit"})

    def test_services_smart_and_stale_data(self):
        self.assertEqual(self.ids({"services": [{"name": "ssh", "label": "SSH", "state": "failed"}, {"name": "x", "label": "X", "state": "inactive"}]}), {"svc:ssh": "crit"})
        self.assertEqual(self.ids({}, {"disks": [{"dev": "/dev/sda", "ok": False, "model": "WD"}, {"dev": "/dev/sdb", "ok": None, "model": ""}]}), {"smart:/dev/sda": "crit"})
        now = time.time()
        self.assertEqual(self.ids({}, None, synced_at=now - 7 * 3600, sync_minutes=30, has_key=True, now=now), {"stale": "warn"})
        self.assertEqual(self.ids({}, None, synced_at=now - 600, sync_minutes=30, has_key=True, now=now), {})
        self.assertEqual(self.ids({}, None, synced_at=0, has_key=True, now=now), {})                         # never synced is not "stale"
        self.assertEqual(self.ids({}, None, synced_at=now - 9e5, has_key=False, now=now), {})

    def test_off_means_off(self):
        self.assertEqual(health.alerts({"disk": {"percent": 99, "used_gb": 1, "total_gb": 1}}, None, dict(self.H, enabled=False)), [])

    def test_alerts_are_announced_once(self):
        a = [{"id": "disk", "level": "warn", "text": "x"}]
        self.assertEqual(health.new_alerts(a, now=1), a)
        self.assertEqual(health.new_alerts(a, now=2), [])                                    # still there: no second mail
        self.assertEqual(health.new_alerts([], now=3), [])
        self.assertEqual(health.new_alerts(a, now=4), a)                                     # resolved and back: tell again

    def test_smart_scan(self):
        self.assertTrue(health.parse_smart_health("SMART overall-health self-assessment test result: PASSED"))
        self.assertFalse(health.parse_smart_health("SMART overall-health self-assessment test result: FAILED!"))
        self.assertIsNone(health.parse_smart_health("Smartctl open device: failed"))

        def run(c):
            if c[0] == "lsblk":
                return R(0, "sda disk WDC WD10\nloop0 loop \nsr0 rom QEMU\nnvme0n1 disk Samsung SSD\n")
            return R(0, "SMART overall-health self-assessment test result: " + ("FAILED!" if "sda" in c[-1] else "PASSED"))
        disks = health.scan(run, now=lambda: 5)["disks"]
        self.assertEqual([(d["dev"], d["ok"]) for d in disks], [("/dev/sda", False), ("/dev/nvme0n1", True)])
        health.write_scan({"at": 5, "disks": disks})
        self.assertEqual(health.read_scan()["disks"][0]["dev"], "/dev/sda")


# ------------------------------------------------------------------ accounts
class LimitsTests(unittest.TestCase):
    def setUp(self):
        isolate()
        auth.reset_throttle()
        auth.SESSIONS.clear()
        self.con = auth.db()
        auth.create("anna", "longenoughpw", "user", self.con)
        auth.create("boss", "twelve-chars!", "admin", self.con)
        self.kw = dict(all_sections=serverconf.SECTIONS, all_profiles=["main", "kid2"])

    def test_expiry_blocks_login_and_open_sessions(self):
        auth.set_limits("anna", expires=(datetime.date.today() + datetime.timedelta(days=3)).isoformat(), **self.kw)
        sid = auth.login("anna", "longenoughpw")
        self.assertTrue(auth.session(sid))
        auth.set_limits("anna", expires=(datetime.date.today() - datetime.timedelta(days=1)).isoformat(), **self.kw)
        self.assertIsNone(auth.session(sid))                                               # the open session ends too
        with self.assertRaisesRegex(auth.AuthError, "expired"):
            auth.login("anna", "longenoughpw")
        auth.set_limits("anna", expires="", **self.kw)                                     # cleared
        self.assertTrue(auth.login("anna", "longenoughpw"))

    def test_wrong_password_does_not_reveal_expiry(self):
        auth.set_limits("anna", expires="2000-01-01", **self.kw)
        with self.assertRaisesRegex(auth.AuthError, "Wrong name or password"):
            auth.login("anna", "wrong-password")

    def test_the_day_itself_still_works(self):
        auth.set_limits("anna", expires=datetime.date.today().isoformat(), **self.kw)
        self.assertTrue(auth.login("anna", "longenoughpw"))

    def test_validation(self):
        with self.assertRaises(auth.AuthError):
            auth.set_limits("boss", expires="2030-01-01", **self.kw)                       # an admin can't lock everybody out
        with self.assertRaises(auth.AuthError):
            auth.set_limits("anna", sections=["secrets"], **self.kw)
        with self.assertRaises(auth.AuthError):
            auth.set_limits("anna", profiles=["nobody"], **self.kw)
        with self.assertRaises(auth.AuthError):
            auth.set_limits("anna", expires="soon", **self.kw)
        with self.assertRaises(auth.AuthError):
            auth.set_limits("ghost", expires="", **self.kw)

    def test_sections_and_children_are_stored(self):
        auth.set_limits("anna", sections=["today", "homework"], profiles=["kid2"], **self.kw)
        a = next(x for x in auth.accounts() if x["name"] == "anna")
        self.assertEqual((a["sections"], a["profiles"]), (["today", "homework"], ["kid2"]))
        auth.set_limits("anna", sections=[], profiles=[], **self.kw)
        self.assertEqual(auth.limits_for("anna")["sections"], [])


# ------------------------------------------------------------------ profiles
class ProfileTests(unittest.TestCase):
    def setUp(self):
        isolate()

    def tearDown(self):
        core.set_profile(None)

    def test_registry(self):
        self.assertEqual(core.profile_ids(), ["main"])
        core.profile_add("Anna", "Anna S.")
        self.assertEqual(core.profile_ids(), ["main", "anna"])
        for bad in ("main", "anna", "", "a b", "../x", "x" * 21):
            with self.assertRaises(ValueError, msg=bad):
                core.profile_add(bad, "x")
        core.profile_rename("anna", "Annabelle")
        core.profile_rename("main", "Max")
        self.assertEqual([p["label"] for p in core.profile_list()], ["Max", "Annabelle"])
        with self.assertRaises(ValueError):
            core.profile_remove("main")

    def test_every_profile_has_its_own_database_and_key(self):
        core.profile_add("anna", "Anna")
        main_path = core.db_path()
        core.set_profile("anna")
        self.assertNotEqual(core.db_path(), main_path)
        self.assertEqual(core.vkey("untis-mobile"), "untis-mobile@anna")
        con = core.db()
        con.execute("insert into store values('homework', 1, '[]')")
        con.commit()
        con.close()
        core.set_profile(None)
        self.assertEqual(core.vkey("untis-mobile"), "untis-mobile")                       # the main profile keeps the old names: nothing to migrate
        self.assertEqual(core.db().execute("select count(*) from store").fetchone()[0], 0)
        import untis_direct
        untis_direct.save_credentials({"server": "a.webuntis.com", "school": "s", "user": "main", "key": "K1"})
        core.set_profile("anna")
        self.assertIsNone(untis_direct.load_credentials())
        untis_direct.save_credentials({"server": "a.webuntis.com", "school": "s", "user": "anna", "key": "K2"})
        self.assertEqual(untis_direct.load_credentials()["user"], "anna")
        core.set_profile(None)
        self.assertEqual(untis_direct.load_credentials()["user"], "main")

    def test_invalid_ids_fall_back_to_main(self):
        for bad in ("../../etc", "A", "x" * 30, ""):
            core.set_profile(bad)
            self.assertEqual(core.profile_id(), "main")

    def test_removing_a_profile_deletes_its_data(self):
        core.profile_add("anna", "Anna")
        core.set_profile("anna")
        core.db().close()
        self.assertTrue(core.db_path().exists())
        core.set_profile(None)
        core.profile_remove("anna")
        self.assertFalse((core.HOME / "profiles" / "anna").exists())
        self.assertEqual(core.profile_ids(), ["main"])

    def test_sync_runs_once_per_profile_with_its_own_environment(self):
        import ssc
        import untis_direct
        core.save_config({**core.config(), "sync_mode": "direct"})
        core.profile_add("anna", "Anna")
        untis_direct.save_credentials({"server": "a", "school": "s", "user": "m", "key": "k"})
        core.set_profile("anna")
        untis_direct.save_credentials({"server": "a", "school": "s", "user": "a", "key": "k"})
        core.set_profile(None)
        self.assertEqual(ssc.sync_profiles(), ["main", "anna"])
        self.assertEqual(ssc.sync_profiles("anna"), ["anna"])
        jobs = []
        old = ssc.run_bg
        ssc.run_bg = lambda state, cmd, env=None: jobs.append(cmd)
        try:
            ssc.run_sync_bg()
            ssc.run_sync_bg("anna")
        finally:
            ssc.run_bg = old
        self.assertEqual([e["SSC_PROFILE"] for _, e in jobs[0]], ["main", "anna"])
        self.assertEqual([e["SSC_PROFILE"] for _, e in jobs[1]], ["anna"])


# ------------------------------------------------------------------ mail
class FakeSMTP:
    sent, log = [], []

    def __init__(self, host, port, timeout=0, **kw):
        FakeSMTP.log.append(("connect", host, port, "context" in kw))

    def starttls(self, context=None):
        FakeSMTP.log.append(("starttls", context is not None))

    def login(self, u, p):
        FakeSMTP.log.append(("login", u, p))

    def send_message(self, msg, from_addr=None, to_addrs=None):
        FakeSMTP.sent.append((msg, from_addr, to_addrs))

    def quit(self):
        pass


class MailTests(unittest.TestCase):
    def setUp(self):
        isolate()
        FakeSMTP.sent, FakeSMTP.log = [], []
        core.HOOKS.clear()
        self.cfg = serverconf.validate({"mail": {"enabled": True, "host": "smtp.example.org", "user": "ssc", "sender": "ssc@example.org", "to": ["parent@example.org"]}})

    def test_message_headers_cannot_be_injected(self):
        m = mailer.build_message(self.cfg["mail"], "3 changes\nBcc: evil@example.org", "body")
        self.assertNotIn("\n", m["Subject"])
        self.assertIsNone(m["Bcc"])
        self.assertEqual(m["To"], "parent@example.org")
        self.assertEqual(m["Subject"], "[SSC] 3 changes Bcc: evil@example.org")

    def test_send_uses_starttls_login_and_recipients(self):
        mailer.send(self.cfg, "Hi", "text", smtp_class=FakeSMTP, password="secret")
        self.assertEqual([x[0] for x in FakeSMTP.log], ["connect", "starttls", "login"])
        self.assertEqual(FakeSMTP.log[2], ("login", "ssc", "secret"))
        msg, sender, to = FakeSMTP.sent[0]
        self.assertEqual((sender, to), ("ssc@example.org", ["parent@example.org"]))

    def test_ssl_mode_and_no_login(self):
        cfg = serverconf.validate({"mail": {**self.cfg["mail"], "security": "ssl", "user": ""}})
        mailer.send(cfg, "Hi", "text", smtp_class=FakeSMTP)
        self.assertNotIn("starttls", [x[0] for x in FakeSMTP.log])
        self.assertNotIn("login", [x[0] for x in FakeSMTP.log])

    def test_errors_are_readable(self):
        class Refuse(FakeSMTP):
            def login(self, u, p):
                raise smtplib.SMTPAuthenticationError(535, b"no")

        class Down(FakeSMTP):
            def __init__(self, *a, **k):
                raise OSError("Connection refused")
        with self.assertRaisesRegex(mailer.MailError, "refused the user name or password"):
            mailer.send(self.cfg, "x", "y", smtp_class=Refuse, password="p")
        with self.assertRaisesRegex(mailer.MailError, "Connection refused"):
            mailer.send(self.cfg, "x", "y", smtp_class=Down, password="p")
        with self.assertRaisesRegex(mailer.MailError, "no mail password"):
            mailer.send(self.cfg, "x", "y", smtp_class=FakeSMTP)
        with self.assertRaisesRegex(mailer.MailError, "incomplete"):
            mailer.send(serverconf.validate({}), "x", "y", smtp_class=FakeSMTP)

    def test_password_lives_in_the_vault_only(self):
        self.assertFalse(mailer.has_password())
        mailer.set_password("app-password-1")
        self.assertTrue(mailer.has_password())
        serverconf.save(self.cfg)
        for name in ("server.json", "config.json"):
            f = Path(core.HOME) / name
            if f.exists():
                self.assertNotIn("app-password-1", f.read_text())                          # never in the settings or the backup source
        mailer.set_password("")
        self.assertFalse(mailer.has_password())

    def test_changes_mail_filters_kinds_and_never_includes_messages(self):
        ch = [("timetable", "Cancelled: M 2026-11-24"), ("message", "New message: PRIVATE"), ("homework", "New homework: E"), ("grade", "New grade: M 1")]
        subject, body = mailer.format_changes(ch, "main", ["timetable", "homework"])
        self.assertEqual(subject, "2 changes")
        self.assertIn("Cancelled", body)
        self.assertNotIn("PRIVATE", body)
        self.assertNotIn("grade", body.lower())
        self.assertIsNone(mailer.format_changes([("message", "x")], "main", list(serverconf.MAIL_KINDS)))
        self.assertIsNone(mailer.format_changes(ch, "main", []))

    def test_big_change_lists_are_cut(self):
        _, body = mailer.format_changes([("timetable", f"Cancelled: L{i}") for i in range(100)], "main", ["timetable"])
        self.assertIn("and 60 more", body)

    def test_the_child_is_named_when_there_are_several(self):
        core.profile_add("anna", "Anna")
        subject, body = mailer.format_changes([("homework", "New homework: E")], "anna", ["homework"])
        self.assertIn("Anna", subject)
        self.assertIn("Changes for Anna", body)

    def test_recording_changes_triggers_the_mail_hook(self):
        serverconf.save(self.cfg)
        sent = []
        orig = mailer.send
        mailer.send = lambda cfg, subject, body, **k: sent.append((subject, body))
        try:
            mailer.register()
            mailer.register()                                                              # registering twice must not double the mails
            core.record_changes(core.db(), [("homework", "New homework: E")])
            core.record_changes(core.db(), [])
            self.assertEqual(len(sent), 1)
            serverconf.save(dict(self.cfg, mail=dict(self.cfg["mail"], enabled=False)))
            core.record_changes(core.db(), [("homework", "New homework: M")])
            self.assertEqual(len(sent), 1)                                                 # switched off: nothing
        finally:
            mailer.send = orig

    def test_a_failing_mail_hook_never_breaks_the_sync(self):
        core.HOOKS.append(lambda changes, pid: 1 / 0)
        core.record_changes(core.db(), [("homework", "x")])                                # must not raise
        self.assertEqual(core.db().execute("select count(*) from changes").fetchone()[0], 1)

    def test_digest(self):
        today = datetime.date(2026, 11, 23)
        tomorrow = "2026-11-24"
        st = {"timetable": {"w": [{"date": tomorrow, "start": f"{tomorrow}T08:00", "subject": "M", "room": "R1", "teacher": "AB", "cancelled": False},
                                  {"date": tomorrow, "start": f"{tomorrow}T09:00", "subject": "E", "room": "", "teacher": "", "cancelled": True}]},
              "homework": [{"subject": "M", "text": "p. 42", "due": "2026-11-26", "done": False, "dismissed": False},
                           {"subject": "E", "text": "done already", "due": "2026-11-25", "done": True, "dismissed": False},
                           {"subject": "B", "text": "far away", "due": "2027-01-01", "done": False, "dismissed": False}],
              "exams": [{"date": "2026-12-01", "subject": "M", "text": "fractions"}, {"date": "2026-11-01", "subject": "E", "text": "past"}]}
        body = mailer.digest_text(st, "main", today)
        self.assertIn("08:00  M  R1  AB", body)
        self.assertIn("(CANCELLED)", body)
        self.assertIn("p. 42", body)
        self.assertNotIn("done already", body)
        self.assertNotIn("far away", body)
        self.assertIn("fractions", body)
        self.assertNotIn("past", body)

    def test_digest_is_due_once_a_day_from_the_set_time(self):
        cfg = serverconf.validate({"mail": {**self.cfg["mail"], "digest": True, "digest_time": "17:00"}})
        day = datetime.datetime(2026, 11, 23, 16, 59)
        self.assertFalse(mailer.digest_due(cfg, day))
        self.assertTrue(mailer.digest_due(cfg, day.replace(hour=17, minute=0)))
        serverconf.save(cfg)
        orig = mailer.send
        mailer.send = lambda *a, **k: None
        try:
            mailer.send_digest(cfg, day.replace(hour=17, minute=1))
        finally:
            mailer.send = orig
        self.assertFalse(mailer.digest_due(cfg, day.replace(hour=20)))                     # done for today
        self.assertTrue(mailer.digest_due(cfg, day.replace(day=24, hour=17)))
        self.assertFalse(mailer.digest_due(serverconf.validate({}), day.replace(hour=20)))

    def test_alert_mail(self):
        sent = []
        orig = mailer.send
        mailer.send = lambda cfg, subject, body, **k: sent.append((subject, body))
        try:
            mailer.send_alerts(self.cfg, [{"id": "disk", "level": "crit", "text": "Disk full"}])
            mailer.send_alerts(self.cfg, [])
            mailer.send_alerts(serverconf.validate({"mail": {**self.cfg["mail"], "alerts": False}}), [{"id": "x", "level": "warn", "text": "y"}])
        finally:
            mailer.send = orig
        self.assertEqual(len(sent), 1)
        self.assertEqual(sent[0][0], "Server needs attention")


# ------------------------------------------------------------------ automatic updates
class AutoUpdateTests(unittest.TestCase):
    def setUp(self):
        isolate()
        self.cfg = serverconf.validate({"autoupdate": {"enabled": True, "time": "04:00"}})
        self.at = lambda h, m=0, d=23: datetime.datetime(2026, 11, d, h, m)

    def test_due(self):
        self.assertFalse(autoupdate.due(self.cfg, self.at(3, 59)))
        self.assertTrue(autoupdate.due(self.cfg, self.at(4, 0)))
        self.assertFalse(autoupdate.due(serverconf.validate({}), self.at(5)))

    def test_installs_once_and_then_waits_for_tomorrow(self):
        reqs = []
        info = {"update_available": True, "latest": "1.2.0"}
        self.assertEqual(autoupdate.run(self.cfg, lambda: info, reqs.append, self.at(4)), "installing 1.2.0")
        self.assertEqual(reqs, [["update"]])
        self.assertFalse(autoupdate.due(self.cfg, self.at(23)))                           # one attempt per day
        self.assertTrue(autoupdate.due(self.cfg, self.at(4, 0, 24)))

    def test_a_version_that_failed_is_not_retried_every_night(self):
        reqs = []
        info = {"update_available": True, "latest": "1.2.0"}
        autoupdate.run(self.cfg, lambda: info, reqs.append, self.at(4))
        self.assertEqual(autoupdate.run(self.cfg, lambda: info, reqs.append, self.at(4, 0, 24)), "not retried")
        self.assertEqual(len(reqs), 1)
        self.assertEqual(autoupdate.run(self.cfg, lambda: dict(info, latest="1.3.0"), reqs.append, self.at(4, 0, 25)), "installing 1.3.0")   # a newer one is tried

    def test_nothing_new_and_no_internet(self):
        reqs = []
        self.assertEqual(autoupdate.run(self.cfg, lambda: {"update_available": False}, reqs.append, self.at(4)), "up to date")

        def boom():
            raise OSError("offline")
        self.assertEqual(autoupdate.run(self.cfg, boom, reqs.append, self.at(4, 0, 24)), "update check failed")
        self.assertEqual(reqs, [])
        self.assertFalse(autoupdate.due(self.cfg, self.at(9, 0, 24)))                      # no hammering GitHub when it is down


# ------------------------------------------------------------------ the web service
class Client:
    def __init__(self, port):
        self.port, self.cookie, self.extra = port, None, ""

    def req(self, method, path, body=None):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        h = {"Content-Type": "application/json"}
        h["Cookie"] = "; ".join(x for x in (self.cookie, self.extra) if x)
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
        cls.serverapp = serverapp
        con = auth.db()
        auth.create("boss", "twelve-chars!", "admin", con)
        auth.create("anna", "longenoughpw", "user", con)
        auth.create("gast", "guestpass", "guest", con)
        core.profile_add("kid2", "Second child")
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), serverapp.make_handler())
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.port = cls.srv.server_address[1]

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def setUp(self):
        auth.reset_throttle()
        auth.SESSIONS.clear()
        core.set_profile(None)
        auth.set_limits("anna", expires="", sections=[], profiles=[], all_sections=serverconf.SECTIONS, all_profiles=core.profile_ids())
        self.a, self.u, self.g = Client(self.port), Client(self.port), Client(self.port)
        self.a.login("boss", "twelve-chars!")
        self.u.login("anna", "longenoughpw")
        self.g.login("gast", "guestpass")
        self.reqf = serverconf.state_dir() / "apply" / "request.json"
        self.reqf.unlink(missing_ok=True)
        for pid, hw in (("main", "Main homework"), ("kid2", "Second homework")):
            core.set_profile(pid)
            con = core.db()
            con.execute("insert or replace into store values('homework', ?, ?)", (time.time(), json.dumps({"homeworks": [{"id": 1, "lessonId": 1, "dueDate": 20991231, "text": hw}], "lessons": [{"id": 1, "subject": "M"}]})))
            con.commit()
            con.close()
        core.set_profile(None)

    def data(self, who):
        s, b = who.req("GET", "/data.json")
        self.assertEqual(s, 200)
        return json.loads(b)

    def test_new_admin_routes_are_admin_only(self):
        for method, path, body in (("GET", "/api/admin/health", None), ("GET", "/api/admin/mail", None), ("POST", "/api/admin/waketest", {}),
                                   ("POST", "/api/admin/wol", {"name": "x"}), ("POST", "/api/admin/mail/password", {"password": "x"}),
                                   ("POST", "/api/admin/mail/test", {}), ("POST", "/api/admin/accounts/limits", {"name": "anna"}),
                                   ("POST", "/api/admin/profiles", {"action": "add", "id": "x", "label": "x"}),
                                   ("POST", "/api/admin/power", {"action": "poweroff_wake", "wake": "06:00"})):
            for who in (Client(self.port), self.u, self.g):
                self.assertEqual(who.req(method, path, body)[0], 403, (method, path))
        self.assertFalse(self.reqf.exists())

    def test_health_and_mail_status(self):
        s, b = self.a.req("GET", "/api/admin/health")
        self.assertEqual(s, 200)
        self.assertIn("alerts", json.loads(b))
        s, b = self.a.req("GET", "/api/admin/mail")
        self.assertEqual((s, json.loads(b)["password_set"]), (200, False))
        self.assertNotIn("password", json.dumps(json.loads(b)["mail"]).lower().replace("password_set", ""))

    def test_system_reports_wake_and_tls_state(self):
        d = json.loads(self.a.req("GET", "/api/admin/system")[1])
        for k in ("tls", "rtc", "can_suspend", "wol", "waketest"):
            self.assertIn(k, d)
        self.assertFalse(d["tls"]["serving"])

    def test_wake_test_is_refused_without_rtc(self):
        s, b = self.a.req("POST", "/api/admin/waketest", {})
        if s == 400:                                                                       # the CI machine has no RTC/suspend: refused, nothing requested
            self.assertFalse(self.reqf.exists())
        else:
            self.assertEqual(json.loads(self.reqf.read_text())["parts"], ["waketest"])

    def test_power_off_and_wake_validates_the_time(self):
        for bad in ("", "tomorrow", "25:00", "6", "0", "; reboot"):
            self.assertEqual(self.a.req("POST", "/api/admin/power", {"action": "poweroff_wake", "wake": bad})[0], 400, bad)
        self.assertFalse(self.reqf.exists())

    def test_wol_only_wakes_saved_devices(self):
        self.assertEqual(self.a.req("POST", "/api/admin/wol", {"name": "pc"})[0], 400)
        self.assertEqual(self.a.req("POST", "/api/admin/wol", {"mac": "aa:bb:cc:dd:ee:ff"})[0], 400)          # an arbitrary address is not accepted
        serverconf.save(serverconf.merge(serverconf.load(), {"wol": {"devices": [{"name": "pc", "mac": "aa:bb:cc:dd:ee:ff"}]}}))
        import wake as w
        sent = []
        old = w.send
        w.send = lambda mac, targets=(), **k: sent.append(mac) or 1
        try:
            s, b = self.a.req("POST", "/api/admin/wol", {"name": "pc"})
        finally:
            w.send = old
        self.assertEqual((s, sent), (200, ["aa:bb:cc:dd:ee:ff"]))

    def test_mail_password_and_test(self):
        self.assertEqual(self.a.req("POST", "/api/admin/mail/test", {})[0], 400)
        self.assertEqual(self.a.req("POST", "/api/admin/mail/password", {"password": "app-secret"})[0], 200)
        self.assertTrue(json.loads(self.a.req("GET", "/api/admin/mail")[1])["password_set"])
        audit = self.a.req("GET", "/api/admin/audit")[1]
        self.assertNotIn("app-secret", audit)
        self.assertIn("mail-password-set", audit)
        self.a.req("POST", "/api/admin/mail/password", {"password": ""})

    def test_limits_over_http(self):
        s, _ = self.a.req("POST", "/api/admin/accounts/limits", {"name": "anna", "sections": ["today"], "profiles": ["kid2"], "expires": "2099-01-01"})
        self.assertEqual(s, 200)
        self.assertEqual(self.a.req("POST", "/api/admin/accounts/limits", {"name": "boss", "expires": "2099-01-01"})[0], 400)
        self.assertEqual(self.a.req("POST", "/api/admin/accounts/limits", {"name": "anna", "sections": ["nope"]})[0], 400)

    def test_profiles_over_http(self):
        self.assertEqual(self.a.req("POST", "/api/admin/profiles", {"action": "add", "id": "third", "label": "Third"})[0], 200)
        self.assertEqual(self.a.req("POST", "/api/admin/profiles", {"action": "add", "id": "../x", "label": "x"})[0], 400)
        self.assertEqual(self.a.req("POST", "/api/admin/profiles", {"action": "remove", "id": "main"})[0], 400)
        self.assertEqual(self.a.req("POST", "/api/admin/profiles", {"action": "remove", "id": "third"})[0], 200)
        status = json.loads(self.a.req("GET", "/api/admin/status")[1])
        self.assertEqual([p["id"] for p in status["profiles"]], ["main", "kid2"])

    # ---- who sees which child
    def test_the_chosen_child_decides_the_data(self):
        self.assertEqual(self.data(self.a)["homework"][0]["text"], "Main homework")
        self.a.extra = "ssc_profile=kid2"
        d = self.data(self.a)
        self.assertEqual((d["profile"], d["homework"][0]["text"]), ("kid2", "Second homework"))
        self.assertEqual([p["id"] for p in d["profiles"]], ["main", "kid2"])

    def test_an_account_limited_to_one_child_cannot_pick_another(self):
        self.a.req("POST", "/api/admin/accounts/limits", {"name": "anna", "profiles": ["kid2"]})
        self.u.extra = "ssc_profile=main"                                                  # asks for the child she may not see
        d = self.data(self.u)
        self.assertEqual((d["profile"], d["homework"][0]["text"]), ("kid2", "Second homework"))
        self.assertEqual([p["id"] for p in d["profiles"]], ["kid2"])
        for evil in ("ssc_profile=../../etc", "ssc_profile=nobody", "ssc_profile="):
            self.u.extra = evil
            self.assertEqual(self.data(self.u)["profile"], "kid2")

    def test_the_passwordless_guest_only_sees_the_first_child(self):
        s = self.serverapp
        self.assertEqual(s.allowed_profiles({"name": "guest", "role": "guest", "anon": True}), ["main"])
        self.assertEqual(s.allowed_profiles(None), ["main"])
        self.assertEqual(s.allowed_profiles({"name": "boss", "role": "admin"}), ["main", "kid2"])

    def test_own_section_list_only_narrows(self):
        full = set(self.data(self.u)["sections"])
        self.a.req("POST", "/api/admin/accounts/limits", {"name": "anna", "sections": ["today", "homework", "grades"]})
        narrowed = self.data(self.u)["sections"]
        self.assertTrue(set(narrowed) <= full)
        self.assertLessEqual(len(narrowed), 3)
        self.assertNotIn("timetable", narrowed)

    def test_expiry_ends_the_session_over_http(self):
        self.assertEqual(self.u.req("GET", "/data.json")[0], 200)
        self.a.req("POST", "/api/admin/accounts/limits", {"name": "anna", "expires": "2000-01-01"})
        self.assertEqual(self.u.req("GET", "/data.json")[0], 401)
        self.assertEqual(Client(self.port).login("anna", "longenoughpw"), 401)

    def test_switcher_appears_only_when_there_is_a_choice(self):
        s, page = self.a.req("GET", "/")
        self.assertIn("ssc_profile=", page)
        self.assertIn("Second child", page)
        self.a.req("POST", "/api/admin/accounts/limits", {"name": "anna", "profiles": ["kid2"]})
        s, page = self.u.req("GET", "/")
        self.assertNotIn("ssc_profile=", page)                                             # she may see one child only: nothing to switch

    # ---- link expiry
    def test_parent_link_expiry(self):
        core.save_config({**core.config(), "links_expire_days": 0})
        self.assertEqual(core.parent_link_expiry(), (False, None))
        core.parent_token(rotate=True)
        core.save_config({**core.config(), "links_expire_days": 30})
        expired, ends = core.parent_link_expiry()
        self.assertFalse(expired)
        self.assertAlmostEqual(ends, time.time() + 30 * 86400, delta=60)
        core.save_config({**core.config(), "links_created": time.time() - 31 * 86400})
        self.assertTrue(core.parent_link_expiry()[0])
        core.parent_token(rotate=True)                                                     # a new link starts a new period
        self.assertFalse(core.parent_link_expiry()[0])
        core.save_config({**core.config(), "links_expire_days": 0})

    def test_expired_link_is_refused_by_the_wall_display_server(self):
        import ssc
        core.save_config({**core.config(), "links_expire_days": 1})
        core.parent_token(rotate=True)
        tok = core.parent_token()
        srv = ThreadingHTTPServer(("127.0.0.1", 0), ssc.make_parent_handler())
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            def get():
                c = http.client.HTTPConnection("127.0.0.1", srv.server_address[1], timeout=5)
                c.request("GET", f"/p/{tok}/data.json")
                return c.getresponse().status
            core._TOK["t"] = 0
            self.assertEqual(get(), 200)
            core.save_config({**core.config(), "links_created": time.time() - 2 * 86400})
            self.assertEqual(get(), 410)
        finally:
            srv.shutdown()
            core.save_config({**core.config(), "links_expire_days": 0})

    # ---- the background pass
    def test_tick_does_its_jobs_independently(self):
        s = self.serverapp
        calls = []
        cfg = serverconf.validate({"mail": {"enabled": True, "host": "smtp.example.org", "sender": "a@b.org", "to": ["c@d.org"], "digest": True, "digest_time": "00:00"},
                                   "autoupdate": {"enabled": True, "time": "00:00"}})
        serverconf.save(cfg)
        orig = (mailer.send_digest, mailer.send_alerts, s.collect_alerts, autoupdate.run, s.ssc.run_sync_bg, s._stale_profiles)
        try:
            mailer.send_digest = lambda c, w=None: calls.append("digest") or 1
            mailer.send_alerts = lambda c, fresh: calls.append("alerts:" + ",".join(a["id"] for a in fresh))
            s.collect_alerts = lambda c: [{"id": "disk", "level": "warn", "text": "x"}]
            autoupdate.run = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("github down"))        # one job failing...
            s.ssc.run_sync_bg = lambda *a: calls.append("sync")
            s._stale_profiles = lambda c, n: ["main"]
            state = {}
            s.tick(state, now=1e6, wall=datetime.datetime(2026, 11, 23, 12, 0))
            self.assertEqual(calls, ["sync", "digest", "alerts:disk"])                                   # ...does not stop the others
            calls.clear()
            s.tick(state, now=1e6 + 30, wall=datetime.datetime(2026, 11, 23, 12, 0))
            self.assertNotIn("digest", calls)                                                           # no retry storm
            self.assertNotIn("alerts:disk", calls)
        finally:
            mailer.send_digest, mailer.send_alerts, s.collect_alerts, autoupdate.run, s.ssc.run_sync_bg, s._stale_profiles = orig

    def test_https_without_a_certificate_falls_back_to_http(self):
        s = self.serverapp
        old = os.environ.get("SSC_ROOT")
        os.environ["SSC_ROOT"] = tempfile.mkdtemp()
        try:
            cfg = serverconf.validate({"web": {"https": True, "bind": "127.0.0.1"}})
            cfg["web"]["port"] = 0                                                         # any free port
            srv = s.build(cfg)
            self.assertFalse(s.TLS["on"])
            srv.server_close()
        finally:
            os.environ.pop("SSC_ROOT", None)
            if old:
                os.environ["SSC_ROOT"] = old


class AdminPageTests(unittest.TestCase):
    def test_page_script_is_valid_and_covers_the_new_features(self):
        import shutil as sh
        import subprocess
        html = (ROOT / "server/admin.html").read_text(encoding="utf-8")
        for needle in ("Notifications", "Children", "wakeTest", "powerWake", "wolSend", "saveMail", "saveLimits", "kidAdd", "saveAutoUpd", "alertsCard"):
            self.assertIn(needle, html)
        node = sh.which("node")
        if not node:
            self.skipTest("node not installed")
        js = html[html.index("<script>") + 8:html.index("</script>")]
        f = Path(tempfile.mkdtemp()) / "admin.js"
        f.write_text(js, encoding="utf-8")
        r = subprocess.run([node, "--check", str(f)], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)


class ConsoleTests(unittest.TestCase):
    def setUp(self):
        isolate()
        import ssconfig
        self.sc = ssconfig

    def test_limits_command(self):
        auth.create("anna", "longenoughpw", "user", auth.db())
        core.profile_add("kid2", "Second")
        self.assertEqual(self.sc.main(["account", "limits", "anna", "--expires", "2099-01-01", "--sections", "today,homework", "--children", "kid2"]), 0)
        a = next(x for x in auth.accounts() if x["name"] == "anna")
        self.assertEqual((a["sections"], a["profiles"]), (["today", "homework"], ["kid2"]))
        self.assertTrue(a["expires"] > time.time())
        self.assertEqual(self.sc.main(["account", "limits", "anna", "--expires", "none", "--sections", "all", "--children", "all"]), 0)
        a = next(x for x in auth.accounts() if x["name"] == "anna")
        self.assertEqual((a["expires"], a["sections"], a["profiles"]), (None, [], []))
        self.assertEqual(self.sc.main(["account", "limits", "anna", "--sections", "bogus"]), 1)

    def test_profile_and_wol_and_autoupdate_commands(self):
        self.assertEqual(self.sc.main(["profile", "add", "anna", "Anna", "S."]), 0)
        self.assertEqual(core.profile_list()[1]["label"], "Anna S.")
        self.assertEqual(self.sc.main(["profile", "add", "anna"]), 1)
        self.assertEqual(self.sc.main(["wol", "add", "pc", "AA-BB-CC-DD-EE-FF"]), 0)
        self.assertEqual(serverconf.load()["wol"]["devices"], [{"name": "pc", "mac": "aa:bb:cc:dd:ee:ff"}])
        self.assertEqual(self.sc.main(["wol", "add", "pc2", "bad"]), 1)
        self.assertEqual(self.sc.main(["wol", "remove", "pc"]), 0)
        self.assertEqual(self.sc.main(["autoupdate", "on", "03:30"]), 0)
        self.assertEqual(serverconf.load()["autoupdate"], {"enabled": True, "time": "03:30"})
        self.assertEqual(self.sc.main(["autoupdate", "off"]), 0)
        self.assertEqual(self.sc.main(["profile", "remove", "anna"]), 0)

    def test_expiry_days_reach_the_shared_config(self):
        self.assertEqual(self.sc.main(["set", "links.expire_days", "14"]), 0)
        self.assertEqual(core.config()["links_expire_days"], 14)


if __name__ == "__main__":
    unittest.main()
