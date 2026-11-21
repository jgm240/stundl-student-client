"""Cross-platform tests: run on macOS, Linux and Windows (CI) with `python -m unittest discover -s tests -v`.
They use no network, no browser and no real secret store."""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ["SSC_DATA_DIR"] = tempfile.mkdtemp(prefix="ssc-test-")

import core  # noqa: E402
import jobs  # noqa: E402
import menu_model  # noqa: E402
import vault  # noqa: E402


class FakeKeyring:
    """A secret store with the Windows size limit (about 1280 characters per entry)."""
    priority = 5
    LIMIT = 1280

    def __init__(self):
        self.d = {}

    def set_password(self, s, n, v):
        if len(v) > self.LIMIT:
            raise ValueError("entry too large")
        self.d[(s, n)] = v

    def get_password(self, s, n):
        return self.d.get((s, n))

    def delete_password(self, s, n):
        self.d.pop((s, n), None)


class DataDirTests(unittest.TestCase):
    def test_per_os(self):
        env = {k: v for k, v in os.environ.items() if k != "SSC_DATA_DIR"}
        with mock.patch.dict(os.environ, env, clear=True):
            with mock.patch.object(sys, "platform", "darwin"):
                self.assertEqual(core.data_dir().name, ".ssc")
            with mock.patch.object(sys, "platform", "win32"), mock.patch.dict(os.environ, {"LOCALAPPDATA": r"C:\Users\x\AppData\Local"}):
                self.assertEqual(core.data_dir().name, "SSC")
            with mock.patch.object(sys, "platform", "linux"), mock.patch.dict(os.environ, {"XDG_DATA_HOME": "/home/x/.local/share"}):
                self.assertEqual(core.data_dir(), Path("/home/x/.local/share/ssc"))

    def test_override(self):
        self.assertEqual(core.data_dir(), Path(os.environ["SSC_DATA_DIR"]))


class VaultTests(unittest.TestCase):
    def setUp(self):
        self.kr = FakeKeyring()
        self.p = mock.patch.object(vault, "_keyring", return_value=self.kr)
        self.p.start()
        self.pl = mock.patch.object(sys, "platform", "win32")   # the chunking path used where entries are size-limited
        self.pl.start()

    def tearDown(self):
        self.pl.stop()
        self.p.stop()

    def test_large_secret_is_chunked_and_restored(self):
        cookies = json.dumps([{"name": "JSESSIONID", "value": "x" * 3000}])   # the real session cookies are ~3 KB
        vault.set("webuntis-cookies", cookies)
        self.assertEqual(vault.get("webuntis-cookies"), cookies)
        self.assertTrue(all(len(v) <= 600 for v in self.kr.d.values()))        # nothing larger than a chunk was stored
        vault.set("webuntis-cookies", "short")                                  # shrinking leaves no stale chunks
        self.assertEqual(vault.get("webuntis-cookies"), "short")
        self.assertEqual(len(self.kr.d), 1)
        vault.delete("webuntis-cookies")
        self.assertIsNone(vault.get("webuntis-cookies"))
        self.assertEqual(self.kr.d, {})

    def test_no_secure_backend_is_refused(self):
        class Dead(FakeKeyring):
            priority = 0
        with mock.patch.object(vault, "_keyring", side_effect=vault.VaultError("no secure keyring")):
            with self.assertRaises(vault.VaultError):
                vault.set("x", "y")
            self.assertIsNone(vault.get("x"))


class JobTests(unittest.TestCase):
    def test_systemd_daily(self):
        u = jobs.systemd_units("sync", ("sync", "--notify"), daily_at=(6, 45))
        self.assertEqual(sorted(u), ["ssc-sync.service", "ssc-sync.timer"])
        self.assertIn("OnCalendar=*-*-* 06:45:00", u["ssc-sync.timer"])
        self.assertIn("Type=oneshot", u["ssc-sync.service"])
        self.assertIn('"sync" "--notify"', u["ssc-sync.service"])

    def test_systemd_service(self):
        u = jobs.systemd_units("parent", ("parent", "serve", "--port", "8766"), service=True)
        self.assertEqual(list(u), ["ssc-parent.service"])
        self.assertIn("Restart=on-failure", u["ssc-parent.service"])
        self.assertIn("WantedBy=default.target", u["ssc-parent.service"])

    def test_schtasks(self):
        d = jobs.schtasks_create("sync", ("sync", "--notify"), daily_at=(6, 5))
        self.assertEqual(d[:2], ["schtasks", "/Create"])
        self.assertIn("SSC\\sync", d)
        self.assertEqual(d[d.index("/SC") + 1], "DAILY")
        self.assertEqual(d[d.index("/ST") + 1], "06:05")
        s = jobs.schtasks_create("parent", ("parent", "serve"), service=True)
        self.assertEqual(s[s.index("/SC") + 1], "ONLOGON")

    def test_launchd_and_autostart(self):
        p = jobs.launchd_plist("parent", ("parent", "serve"), service=True)
        self.assertTrue(p["RunAtLoad"])
        self.assertEqual(p["Label"], "com.stundl.ssc.parent")
        self.assertIn("[Desktop Entry]", jobs.autostart_desktop(("menubar", "run")))


class NotifyTests(unittest.TestCase):
    def test_linux_uses_notify_send(self):
        with mock.patch.object(sys, "platform", "linux"), mock.patch("shutil.which", return_value="/usr/bin/notify-send"), \
                mock.patch("subprocess.run") as run:
            core.notify("T", "hello")
        self.assertEqual(run.call_args[0][0][0], "notify-send")

    def test_windows_passes_text_via_environment(self):
        with mock.patch.object(sys, "platform", "win32"), mock.patch("subprocess.Popen") as popen:
            core.notify("T", 'he said "hi"; $x')
        env = popen.call_args[1]["env"]
        self.assertEqual(env["SSC_M"], 'he said "hi"; $x')      # never interpolated into the script
        self.assertNotIn("he said", " ".join(popen.call_args[0][0]))

    def test_linux_without_notify_send_prints(self):
        with mock.patch.object(sys, "platform", "linux"), mock.patch("shutil.which", return_value=None), mock.patch("builtins.print") as pr:
            core.notify("T", "m")
        pr.assert_called()


class CoreTests(unittest.TestCase):
    def test_state_and_menu_model_work_on_an_empty_install(self):
        con = core.db()
        st = core.state(con)
        m = menu_model.build(st)
        self.assertEqual(m["title"], "SSC")
        self.assertEqual(m["homework"], [])

    def test_lesson_details_are_kept(self):
        e = {"duration": {"start": "2026-11-23T10:00", "end": "2026-11-23T10:50"}, "status": "CHANGED", "type": "NORMAL_TEACHING_PERIOD",
             "lessonText": "Chapter 4: <b>fractions</b>", "notesAll": "Bring a calculator", "studentGroup": "5A/2", "color": "#ff8800",
             "substitutionText": "Cover lesson",
             "position1": [{"current": {"type": "CLASS", "shortName": "5A"}}],
             "position2": [{"current": {"type": "TEACHER", "shortName": "CD", "longName": "Carla Dorn"}, "removed": {"type": "TEACHER", "shortName": "AB"}}],
             "position3": [{"current": {"type": "SUBJECT", "shortName": "M", "longName": "Mathematics"}}],
             "position4": [{"current": {"type": "ROOM", "shortName": "R2", "longName": "Room 2"}, "removed": {"type": "ROOM", "shortName": "R1"}}]}
        x = core.tt_entries({"days": [{"date": "2026-11-23", "gridEntries": [e]}]})[0]
        self.assertEqual((x["subject"], x["subject_long"], x["teacher"], x["teacher_long"], x["room"]), ("M", "Mathematics", "CD", "Carla Dorn", "R2"))
        self.assertEqual(x["topic"], "Chapter 4: fractions")          # HTML removed
        self.assertEqual((x["note"], x["group"], x["substitution"], x["color"]), ("Bring a calculator", "5A/2", "Cover lesson", "#ff8800"))
        self.assertEqual(x["changes"], ["Teacher: AB \u2192 CD", "Room: R1 \u2192 R2"])
        before = {"days": [{"date": "2026-11-23", "gridEntries": [{**e, "lessonText": ""}]}]}
        after = {"days": [{"date": "2026-11-23", "gridEntries": [e]}]}
        changes = [t for _, t in core.diff("timetable:2099-01-01", before, after)]
        self.assertTrue(any("Lesson topic" in t and "fractions" in t for t in changes), changes)   # a newly entered topic is reported

    def test_plan_roundtrip(self):
        con = core.db()
        i = core.plan_add(con, title="Learn", minutes=30)
        core.plan_update(con, i, done=True)
        self.assertTrue(core.plan_items(con)[0]["done"])

    def test_parent_state_never_contains_secrets(self):
        con = core.db()
        blob = json.dumps(core.parent_state(con))
        for needle in ("token", "cookie", "password"):
            self.assertNotIn(needle, blob.lower())

    def test_macos_only_features_are_off_elsewhere(self):
        with mock.patch.object(sys, "platform", "linux"):
            self.assertIsNone(core.icloud_dir())
            self.assertIsNone(core.write_widget({}, {}))


class SscCliTests(unittest.TestCase):
    def test_help_and_browsers(self):
        import ssc
        names = [b["id"] for b in ssc.available_browsers()]
        for k in ("auto", "chromium", "firefox", "webkit"):
            self.assertIn(k, names)
        with mock.patch.object(sys, "platform", "win32"):
            self.assertIn("chrome", [b["id"] for b in ssc.available_browsers()])
            self.assertNotIn("brave", [b["id"] for b in ssc.available_browsers()])

    def test_no_display_linux_defaults_to_tui(self):
        import ssc
        with mock.patch.object(sys, "platform", "linux"), mock.patch.dict(os.environ, {}, clear=True), \
                mock.patch.object(sys, "argv", ["ssc"]), mock.patch.object(ssc.core, "migrate_legacy_home"), \
                mock.patch("ssc.argparse.ArgumentParser.parse_args", side_effect=SystemExit):
            with self.assertRaises(SystemExit):
                ssc.main()
            self.assertEqual(sys.argv[1], "tui")


if __name__ == "__main__":
    unittest.main()
