"""The updater: only GitHub, only on request, checksums enforced, safe unpacking, staged install with roll-back."""
import os as _os, tempfile as _tf
_os.environ.setdefault("SSC_DATA_DIR", _tf.mkdtemp(prefix="ssc-test-"))      # before core is imported: the tests must never touch the real ~/.ssc
import hashlib
import http.server
import io
import json
import os
import shutil
import sys
import tarfile
import tempfile
import threading
import unittest
from pathlib import Path

if sys.platform == "win32":
    raise unittest.SkipTest("the server install path is POSIX only")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "server"))
import ssc_version  # noqa: E402
import updater  # noqa: E402

NEW = "1.0.0-beta.7"


def make_release_tar(version=NEW, extra=None, broken=False, drop=None):
    """A source archive of THIS repository (whitelisted files only) claiming to be `version` - what GitHub would serve."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        def add(name, data):
            ti = tarfile.TarInfo("stundl-student-client-abc123/" + name)
            ti.size = len(data)
            tar.addfile(ti, io.BytesIO(data))
        for pat in updater.ALLOWED:
            for f in ROOT.glob(pat):
                rel = f.relative_to(ROOT).as_posix()
                if f.is_file() and rel != drop:
                    data = f.read_bytes()
                    if rel == "ssc_version.py":
                        data = data.replace(ssc_version.VERSION.encode(), version.encode())
                    if broken and rel == "server/auth.py":
                        data = b"def (:\n"
                    add(rel, data)
        add("README.md", b"not whitelisted: must be ignored")
        add("tests/evil.py", b"print('not whitelisted')")
        for name, data in (extra or {}).items():
            add(name, data)
    return buf.getvalue()


class FakeGitHub(http.server.BaseHTTPRequestHandler):
    tarball = b""
    app = b"app-bytes"
    sums_ok = True
    with_sums = True

    def log_message(self, *a):
        pass

    head = "a" * 40                 # the newest commit of the fake main branch
    head_version = NEW
    runs = [{"status": "completed", "conclusion": "success"}]
    hits = []

    def do_GET(self):
        cls = type(self)
        base = f"http://127.0.0.1:{self.server.server_address[1]}"
        cls.hits.append(self.path)
        if self.path.startswith("/repos/") and self.path.endswith("/commits/main"):
            body = json.dumps({"sha": cls.head, "html_url": base + "/commit", "commit": {"message": "Fix the thing\n\nlong text", "committer": {"date": "2026-11-28T08:00:00Z"}}}).encode()
            ctype = "application/json"
        elif self.path.startswith("/repos/") and "/contents/ssc_version.py" in self.path:
            import base64
            body = json.dumps({"content": base64.encodebytes(f'VERSION = "{cls.head_version}"\n'.encode()).decode()}).encode()
            ctype = "application/json"
        elif self.path.startswith("/repos/") and "/check-runs" in self.path:
            body = json.dumps({"check_runs": cls.runs}).encode()
            ctype = "application/json"
        elif self.path.startswith("/repos/") and "/releases" in self.path:
            sums = hashlib.sha256(cls.app).hexdigest() if cls.sums_ok else "0" * 64
            assets = [{"name": f"SSC-{NEW}-linux.tar.gz", "size": len(cls.app), "browser_download_url": base + "/dl/linux"},
                      {"name": f"SSC-{NEW}-windows.zip", "size": 1, "browser_download_url": base + "/dl/win"}]
            if cls.with_sums:
                assets.append({"name": "SHA256SUMS", "size": 1, "browser_download_url": base + "/dl/sums"})
            body = json.dumps([
                {"tag_name": "v1.0.0-beta.9", "draft": True, "prerelease": True, "html_url": "x", "assets": []},                  # drafts are ignored
                {"tag_name": "v1.0.0-beta.7.untested", "draft": False, "prerelease": True, "html_url": "x", "assets": []},         # odd tags too
                {"tag_name": "v" + NEW, "draft": False, "prerelease": True, "html_url": base + "/rel", "body": "notes", "assets": assets},
                {"tag_name": "v1.0.0-beta.4", "draft": False, "prerelease": True, "html_url": "x", "assets": []}]).encode()
            ctype = "application/json"
        elif self.path == "/dl/sums":
            sums = hashlib.sha256(cls.app).hexdigest() if cls.sums_ok else "0" * 64
            body = (f"{sums}  SSC-{NEW}-linux.tar.gz\n{'1' * 64}  SSC-{NEW}-windows.zip\n").encode()
            ctype = "text/plain"
        elif self.path == "/dl/linux":
            body, ctype = cls.app, "application/octet-stream"
        elif self.path.startswith("/tarball/"):
            body, ctype = cls.tarball, "application/gzip"
        elif self.path == "/redirect":
            self.send_response(302)
            self.send_header("Location", "http://evil.example/steal")
            self.end_headers()
            return
        else:
            self.send_response(404)
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), FakeGitHub)
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.base = f"http://127.0.0.1:{cls.srv.server_address[1]}"

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def setUp(self):
        self._old = (updater.API, updater._TEST_ALLOW_LOCAL, ssc_version.VERSION)
        updater.API, updater._TEST_ALLOW_LOCAL = self.base, True
        FakeGitHub.tarball, FakeGitHub.sums_ok, FakeGitHub.with_sums = make_release_tar(), True, True
        FakeGitHub.head, FakeGitHub.head_version, FakeGitHub.runs, FakeGitHub.hits = "a" * 40, NEW, [{"status": "completed", "conclusion": "success"}], []
        self.tmp = Path(tempfile.mkdtemp(prefix="ssc-upd-"))

    def tearDown(self):
        updater.API, updater._TEST_ALLOW_LOCAL, ssc_version.VERSION = self._old
        shutil.rmtree(self.tmp, ignore_errors=True)


class VersionTests(unittest.TestCase):
    def test_ordering(self):
        p = ssc_version.parse
        self.assertLess(p("1.0.0-beta.9"), p("1.0.0-rc.1"))
        self.assertLess(p("1.0.0-rc.3"), p("1.0.0"))
        self.assertLess(p("1.0.0"), p("1.0.1-beta.1"))
        self.assertLess(p("v1.0.0-beta.5"), p("v1.0.0-beta.10"))                  # numeric, not alphabetical
        self.assertIsNone(p("1.0.0-beta.5.untested"))
        self.assertIsNone(p("latest"))
        self.assertTrue(ssc_version.is_newer("v1.0.0", "1.0.0-beta.5"))
        self.assertFalse(ssc_version.is_newer("1.0.0-beta.5", "1.0.0-beta.5"))

    def test_version_files_agree(self):
        import re
        self.assertEqual(re.search(r'default: "([^"]+)"', (ROOT / ".github/workflows/server-iso.yml").read_text(encoding="utf-8")).group(1), ssc_version.VERSION)


class CheckTests(Base):
    def test_releases_are_filtered_and_sorted(self):
        rels = updater.releases()
        self.assertEqual([r["tag"] for r in rels], ["v" + NEW, "v1.0.0-beta.4"])         # draft and .untested ignored
        info = updater.check()
        self.assertTrue(info["update_available"])
        self.assertEqual((info["latest"], info["notes"]), (NEW, "notes"))
        self.assertFalse(updater.check(current=NEW)["update_available"])

    def test_only_github_is_ever_contacted(self):
        updater._TEST_ALLOW_LOCAL = False
        for url in ("http://api.github.com/x", "https://evil.example/x", "https://github.com.evil.example/x", "file:///etc/passwd", "ftp://github.com/x"):
            with self.assertRaises(updater.UpdateError, msg=url):
                updater._open(url)
        self.assertTrue(updater._trusted("https://api.github.com/repos/x"))
        self.assertTrue(updater._trusted("https://objects.githubusercontent.com/a"))

    def test_redirects_may_not_leave_github(self):
        with self.assertRaises(updater.UpdateError) as e:
            updater._open(self.base + "/redirect")
        self.assertIn("redirect", str(e.exception))


class AppDownloadTests(Base):
    def test_download_is_verified(self):
        f, v = updater.download_app(self.tmp, platform="linux")
        self.assertEqual((f.name, v, f.read_bytes()), (f"SSC-{NEW}-linux.tar.gz", NEW, b"app-bytes"))

    def test_checksum_mismatch_saves_nothing(self):
        FakeGitHub.sums_ok = False
        with self.assertRaises(updater.UpdateError) as e:
            updater.download_app(self.tmp, platform="linux")
        self.assertIn("checksum", str(e.exception))
        self.assertEqual(list(self.tmp.iterdir()), [])                                # not even a partial file

    def test_no_checksums_no_download(self):
        FakeGitHub.with_sums = False
        with self.assertRaises(updater.UpdateError) as e:
            updater.download_app(self.tmp, platform="linux")
        self.assertIn("SHA256SUMS", str(e.exception))
        self.assertEqual(list(self.tmp.iterdir()), [])

    def test_mac_has_no_prebuilt_app(self):
        with self.assertRaises(updater.UpdateError) as e:
            updater.download_app(self.tmp, platform="darwin")
        self.assertIn("build_app.sh", str(e.exception))

    def test_parse_sums(self):
        self.assertEqual(updater.parse_sums(("a" * 64) + "  file.zip\n" + ("b" * 64) + " *other.tar.gz\njunk line\n"), {"file.zip": "a" * 64, "other.tar.gz": "b" * 64})


def tar_with(members):
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for name, kind, data in members:
            ti = tarfile.TarInfo(name)
            if kind == "file":
                ti.size = len(data)
                tar.addfile(ti, io.BytesIO(data))
            elif kind == "symlink":
                ti.type, ti.linkname = tarfile.SYMTYPE, "/etc/passwd"
                tar.addfile(ti)
            elif kind == "hardlink":
                ti.type, ti.linkname = tarfile.LNKTYPE, "core.py"
                tar.addfile(ti)
    return buf.getvalue()


class ExtractTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="ssc-ext-"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def extract(self, members):
        f = self.tmp / "a.tar.gz"
        f.write_bytes(tar_with(members))
        return updater.safe_extract(f, self.tmp / "stage")

    def test_whitelisted_files_only(self):
        n = self.extract([("r/core.py", "file", b"x=1"), ("r/server/auth.py", "file", b"y=2"), ("r/README.md", "file", b"no"), ("r/tests/t.py", "file", b"no"),
                          ("r/.github/workflows/x.yml", "file", b"no"), ("r/server/iso/build.sh", "file", b"no")])
        self.assertEqual(n, 2)
        self.assertEqual(sorted(p.relative_to(self.tmp / "stage").as_posix() for p in (self.tmp / "stage").rglob("*") if p.is_file()), ["core.py", "server/auth.py"])

    def test_unsafe_archives_are_refused(self):
        for members in ([("r/core.py", "symlink", b"")], [("r/core.py", "hardlink", b"")], [("r/../../evil.py", "file", b"x")],
                        [("../escape.py", "file", b"x"), ("r/core.py", "file", b"x")]):
            with self.assertRaises(updater.UpdateError, msg=str(members)):
                self.extract(members)
        self.assertFalse((self.tmp / "evil.py").exists())

    def test_oversized_file_refused(self):
        with self.assertRaises(updater.UpdateError):
            self.extract([("r/core.py", "file", b"x" * (9 * 1024 * 1024))])


class ServerInstallTests(Base):
    def make_root(self):
        root = self.tmp / "opt" / "ssc"
        root.mkdir(parents=True)
        (root / "marker.txt").write_text("old version")
        (root / "VERSION").write_text("1.0.0-beta.5\n")
        return root

    def run_fn(self):
        self.calls = []

        class R:
            returncode, stdout, stderr = 0, "", ""
        return lambda c, **k: self.calls.append(c) or R()

    def test_install_swaps_in_the_new_version_and_keeps_the_old(self):
        root = self.make_root()
        v = updater.install_server(root=str(root), run=self.run_fn(), log=lambda m: None)
        self.assertEqual(v, NEW)
        self.assertEqual((root / "VERSION").read_text().strip(), NEW)
        self.assertIn(NEW, (root / "ssc_version.py").read_text())
        self.assertTrue((root / "server" / "serverapp.py").exists() and (root / "ui.js").exists())
        self.assertFalse((root / "README.md").exists() or (root / "tests").exists())              # only whitelisted files
        prev = self.tmp / "opt" / "ssc.prev"
        self.assertEqual((prev / "marker.txt").read_text(), "old version")                          # roll-back copy
        self.assertIn(["systemctl", "restart", "ssc-server"], self.calls)
        self.assertEqual(oct((root / "core.py").stat().st_mode & 0o777), "0o644")
        self.assertEqual([p.name for p in (self.tmp / "opt").iterdir() if p.name.startswith("ssc-update-")], [])   # work folder cleaned up

    def test_rollback(self):
        root = self.make_root()
        updater.install_server(root=str(root), run=self.run_fn(), log=lambda m: None)
        updater.rollback(root=str(root), run=self.run_fn())
        self.assertEqual((root / "marker.txt").read_text(), "old version")
        self.assertFalse((root / "ssc_version.py").exists())
        with self.assertRaises(updater.UpdateError):
            updater.rollback(root=str(self.tmp / "nowhere" / "ssc"), run=self.run_fn())

    def test_a_broken_release_changes_nothing(self):
        for label, tar in (("syntax error", make_release_tar(broken=True)), ("missing file", make_release_tar(drop="server/ssconfig.py")),
                           ("wrong version inside", make_release_tar(version="1.0.0-beta.8"))):
            FakeGitHub.tarball = tar
            root = self.make_root() if not (self.tmp / "opt" / "ssc").exists() else self.tmp / "opt" / "ssc"
            before = (root / "marker.txt").read_text()
            with self.assertRaises(updater.UpdateError, msg=label):
                updater.install_server(root=str(root), run=self.run_fn(), log=lambda m: None)
            self.assertEqual((root / "marker.txt").read_text(), before, label)
            self.assertFalse((self.tmp / "opt" / "ssc.prev").exists(), label)
            self.assertNotIn(["systemctl", "restart", "ssc-server"], self.calls, label)

    def test_already_up_to_date(self):
        ssc_version.VERSION = NEW
        with self.assertRaises(updater.UpdateError) as e:
            updater.install_server(root=str(self.make_root()), run=self.run_fn(), log=lambda m: None)
        self.assertIn("up to date", str(e.exception))

    def test_flavor(self):
        self.assertEqual(updater.flavor(), "source")
        os.environ["SSC_SERVER_ROOT"] = str(ROOT)
        try:
            self.assertEqual(updater.flavor(), "server")
        finally:
            del os.environ["SSC_SERVER_ROOT"]


class CommitUpdateTests(Base):
    """A new commit on the main branch is an update - no release needed."""
    SHA2 = "b" * 40

    def make_root(self, commit=""):
        root = self.tmp / "opt" / "ssc"
        root.mkdir(parents=True)
        (root / "marker.txt").write_text("old version")
        (root / "VERSION").write_text("1.0.0-beta.5\n")
        if commit:
            (root / "COMMIT").write_text(commit + "\n")
        return root

    def run_fn(self):
        self.calls = []

        class R:
            returncode, stdout, stderr = 0, "", ""
        return lambda c, **k: self.calls.append(c) or R()

    def test_a_new_commit_is_an_update(self):
        root = self.make_root("c" * 40)
        info = updater.check(channel="commits")
        self.assertEqual(info["channel"], "commits")
        self.assertEqual((info["sha"], info["tag"]), ("a" * 40, "a" * 40))
        self.assertTrue(info["update_available"])
        self.assertEqual(info["notes"], "Fix the thing")                                       # the first line of the commit message
        self.assertIn(NEW, info["latest"])
        self.assertIn("aaaaaaa", info["latest"])
        self.assertEqual(info["ci"], "passed")
        self.assertEqual(updater.installed_commit(root), "c" * 40)
        os.environ["SSC_SERVER_ROOT"] = str(root)
        try:
            (root / "COMMIT").write_text("a" * 40 + "\n")                                     # already on the newest commit
            self.assertFalse(updater.check(channel="commits")["update_available"])
            FakeGitHub.head = self.SHA2
            self.assertTrue(updater.check(channel="commits")["update_available"])             # and the next commit is an update again
        finally:
            del os.environ["SSC_SERVER_ROOT"]

    def test_unknown_installed_commit_is_offered_once(self):
        os.environ["SSC_SERVER_ROOT"] = str(self.make_root())                                  # installed from a release: no COMMIT file
        try:
            self.assertTrue(updater.check(channel="commits")["update_available"])
            self.assertEqual(updater.installed_commit(), "")
        finally:
            del os.environ["SSC_SERVER_ROOT"]
        root = self.tmp / "opt" / "ssc"
        updater.install_server(root=str(root), run=self.run_fn(), log=lambda m: None, channel="commits")
        os.environ["SSC_SERVER_ROOT"] = str(root)
        try:
            self.assertFalse(updater.check(channel="commits")["update_available"])            # now it knows where it is
        finally:
            del os.environ["SSC_SERVER_ROOT"]

    def test_install_a_commit(self):
        root = self.make_root("c" * 40)
        v = updater.install_server(root=str(root), run=self.run_fn(), log=lambda m: None, channel="commits")
        self.assertEqual(v, NEW)
        self.assertEqual((root / "COMMIT").read_text().strip(), "a" * 40)
        self.assertEqual((root / "VERSION").read_text().strip(), NEW)
        self.assertEqual((self.tmp / "opt" / "ssc.prev" / "COMMIT").read_text().strip(), "c" * 40)       # roll-back copy
        self.assertIn(["systemctl", "restart", "ssc-server"], self.calls)
        self.assertTrue(any(h.startswith("/tarball/" + "a" * 40) for h in FakeGitHub.hits))
        with self.assertRaises(updater.UpdateError) as e:
            updater.install_server(root=str(root), run=self.run_fn(), log=lambda m: None, channel="commits")
        self.assertIn("up to date", str(e.exception))

    def test_a_commit_whose_tests_failed_is_not_installed(self):
        root = self.make_root("c" * 40)
        FakeGitHub.runs = [{"status": "completed", "conclusion": "success"}, {"status": "completed", "conclusion": "failure"}]
        self.assertEqual(updater.check(channel="commits")["ci"], "failed")
        with self.assertRaises(updater.UpdateError) as e:
            updater.install_server(root=str(root), run=self.run_fn(), log=lambda m: None, channel="commits")
        self.assertIn("failed", str(e.exception))
        self.assertEqual((root / "marker.txt").read_text(), "old version")
        self.assertNotIn(["systemctl", "restart", "ssc-server"], self.calls)
        updater.install_server(root=str(root), run=self.run_fn(), log=lambda m: None, channel="commits", force=True)       # an administrator can still insist
        self.assertEqual((root / "COMMIT").read_text().strip(), "a" * 40)

    def test_ci_states(self):
        ok, bad, wait = {"status": "completed", "conclusion": "success"}, {"status": "completed", "conclusion": "failure"}, {"status": "in_progress", "conclusion": None}
        skipped, cancelled = {"status": "completed", "conclusion": "skipped"}, {"status": "completed", "conclusion": "cancelled"}
        for runs, want in (([ok], "passed"), ([ok, skipped], "passed"), ([ok, wait], "pending"), ([ok, bad], "failed"), ([wait, bad], "failed"),
                           ([], "unknown"), ([cancelled], "unknown"), ([skipped], "unknown"), ([ok, cancelled], "passed")):
            FakeGitHub.runs = runs
            self.assertEqual(updater.ci_state("a" * 40), want, runs)

    def test_ci_unknown_when_github_does_not_answer(self):
        updater.API = self.base + "/nothing-here"
        self.assertEqual(updater.ci_state("a" * 40), "unknown")

    def test_a_broken_commit_changes_nothing(self):
        for label, tar in (("syntax error", make_release_tar(broken=True)), ("missing file", make_release_tar(drop="server/ssconfig.py"))):
            FakeGitHub.tarball = tar
            root = self.tmp / "opt" / "ssc" if (self.tmp / "opt" / "ssc").exists() else self.make_root("c" * 40)
            with self.assertRaises(updater.UpdateError, msg=label):
                updater.install_server(root=str(root), run=self.run_fn(), log=lambda m: None, channel="commits")
            self.assertEqual((root / "marker.txt").read_text(), "old version", label)
            self.assertEqual((root / "COMMIT").read_text().strip(), "c" * 40, label)
            self.assertNotIn(["systemctl", "restart", "ssc-server"], self.calls, label)

    def test_never_goes_back_to_an_older_version(self):
        FakeGitHub.tarball = make_release_tar(version="0.9.0")
        FakeGitHub.head_version = "0.9.0"
        root = self.make_root("c" * 40)
        with self.assertRaises(updater.UpdateError) as e:
            updater.install_server(root=str(root), run=self.run_fn(), log=lambda m: None, channel="commits")
        self.assertIn("older", str(e.exception))
        self.assertEqual((root / "marker.txt").read_text(), "old version")

    def test_bad_answers_from_github_are_refused(self):
        FakeGitHub.head = "not-a-sha"
        with self.assertRaises(updater.UpdateError):
            updater.check(channel="commits")
        with self.assertRaises(updater.UpdateError):
            updater.install_server(root=str(self.make_root()), run=self.run_fn(), log=lambda m: None, channel="commits", sha="../../etc")

    def test_the_channel_setting_picks_the_way(self):
        real_flavor = updater.flavor
        updater.flavor = lambda: "server"
        try:
            import serverconf
            old = serverconf.load
            serverconf.load = lambda: serverconf.validate({"update": {"channel": "releases"}})
            self.assertEqual(updater.default_channel(), "releases")
            self.assertEqual(updater.check()["channel"], "releases")
            serverconf.load = lambda: serverconf.validate({})
            self.assertEqual(updater.default_channel(), "commits")
            self.assertEqual(updater.check()["channel"], "commits")
            serverconf.load = old
        finally:
            updater.flavor = real_flavor
        self.assertEqual(updater.default_channel(), "releases")                                # a desktop app / source run never follows commits
        with self.assertRaises(ValueError):
            __import__("serverconf").validate({"update": {"channel": "nightly"}})


if __name__ == "__main__":
    unittest.main()
