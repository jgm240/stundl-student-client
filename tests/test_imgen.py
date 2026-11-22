import io
import contextlib
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import ssc_imgen as im  # noqa: E402


def dark_pixels(img, box):
    px = img.crop(box).convert("L")
    return sum(1 for v in px.getdata() if v < 60)


class ImgenTests(unittest.TestCase):
    def card(self, **kw):
        args = dict(address="192.168.1.50:8080", user="anna", password="Blue-Tiger-42")
        args.update(kw)
        return im.make_card(**args)

    def test_size_and_not_blank(self):
        c = self.card()
        self.assertEqual(c.size, (1500, 900))
        self.assertEqual(c.getpixel((700, 100))[2] > 150, True)                 # indigo header band
        self.assertGreater(dark_pixels(c, (60, 280, 960, 700)), 3000)           # text on the card

    def test_qr_is_optional(self):
        self.assertGreater(dark_pixels(self.card(), (1030, 280, 1430, 680)), 4000)
        self.assertLess(dark_pixels(self.card(qr=False), (1030, 280, 1430, 680)), 50)

    def test_notes_optional_and_clipped(self):
        without = self.card()
        with_notes = self.card(notes="Sign in on the TV")
        self.assertGreater(dark_pixels(with_notes, (60, 700, 1000, 840)), dark_pixels(without, (60, 700, 1000, 840)) + 500)
        long = self.card(notes="word " * 400)                                    # never overflows the card
        self.assertEqual(long.size, (1500, 900))

    def test_dark_theme(self):
        self.assertLess(sum(self.card(dark=True).getpixel((700, 800))), 200)
        self.assertGreater(sum(self.card().getpixel((700, 800))), 600)

    def test_long_password_shrinks_to_fit(self):
        c = self.card(password="x7Kq-" * 12, qr=True)
        # nothing is drawn over the QR column to the right of the credentials box
        self.assertEqual(self.card(password="x7Kq-" * 12).size, (1500, 900))
        self.assertLess(dark_pixels(c, (990, 590, 1010, 690)), 30)

    def test_wrap(self):
        Image, ImageDraw, ImageFont = im._pil()
        d = ImageDraw.Draw(Image.new("RGB", (10, 10)))
        f = im.load_font(im.SANS, 32)
        lines = im.wrap(d, "one two three four five six seven eight nine ten " * 3, f, 400, 2)
        self.assertEqual(len(lines), 2)
        self.assertTrue(lines[-1].endswith("..."))
        self.assertTrue(all(d.textlength(x, font=f) <= 400 for x in lines))
        self.assertEqual(im.wrap(d, "a\\nb", f, 400, 3), ["a", "b"])             # \n typed on the command line

    def test_languages(self):
        self.assertEqual(sorted(im.STRINGS), sorted(["en", "de", "fr", "es", "it", "pt", "nl", "pl", "cs", "sv", "da", "tr"]))
        keys = set(im.STRINGS["en"])
        for code, tr in im.STRINGS.items():
            self.assertEqual(set(tr), keys, code)                                     # every language has every text
            self.assertTrue(all(v.strip() for v in tr.values()), code)
        self.assertEqual(im.STRINGS["de"]["password"], "PASSWORT")
        self.assertEqual(im.pick_language("de_AT.UTF-8"), "de")
        self.assertEqual(im.pick_language("fr-CA"), "fr")
        with self.assertRaises(im.CardError):
            im.pick_language("xx")
        old = {k: os.environ.get(k) for k in ("LC_ALL", "LC_MESSAGES", "LANG", "LANGUAGE")}
        try:
            for k in old:
                os.environ.pop(k, None)
            self.assertEqual(im.pick_language("auto"), "en")
            os.environ["LANG"] = "es_ES.UTF-8"
            self.assertEqual(im.pick_language("auto"), "es")
            os.environ["LANG"] = "ja_JP.UTF-8"                                        # not available: English, not an error
            self.assertEqual(im.pick_language("auto"), "en")
        finally:
            for k, v in old.items():
                os.environ.pop(k, None)
                if v is not None:
                    os.environ[k] = v

    def test_every_language_renders_and_differs(self):
        base = self.card(lang="en").tobytes()
        for code in im.STRINGS:
            c = self.card(lang=code, notes="x")
            self.assertEqual(c.size, (1500, 900), code)
            if code != "en":
                self.assertNotEqual(c.tobytes(), base, code)                          # labels really change
        for code in ("pl", "fr", "cs"):                                              # the longest labels still fit their column
            self.assertLess(dark_pixels(self.card(lang=code), (985, 255, 1010, 285)), 20, code)

    def test_cli_lang_option(self):
        d = Path(tempfile.mkdtemp())
        self.assertEqual(self.run_cli("--user", "a", "--address", "1.2.3.4:80", "--password-stdin", "--lang", "de", "-o", str(d / "a.png"), stdin="pw1234\n")[0], 0)
        self.assertEqual(self.run_cli("--user", "a", "--address", "1.2.3.4:80", "--password-stdin", "--lang", "klingon", "-o", str(d / "b.png"), stdin="pw1234\n")[0], 1)

    def amber(self, img, box):
        px = img.crop(box).convert("RGB")
        return sum(1 for r, g, b in px.getdata() if r > 200 and 120 < g < 200 and b < 90)

    def test_cellular_warning(self):
        box = (60, 560, 990, 730)
        self.assertLess(self.amber(self.card(), box), 20)                          # off by default
        warn = self.card(cellular_warn=True)
        self.assertGreater(self.amber(warn, box), 400)                             # the amber triangle and band
        self.assertEqual(warn.size, (1500, 900))
        for code in im.STRINGS:
            self.assertTrue(im.STRINGS[code]["cellular"].strip(), code)
        self.assertEqual(im.STRINGS["en"]["cellular"], "Connect to WiFi instead of mobile data to access SSC")
        self.assertNotEqual(self.card(cellular_warn=True, lang="de").tobytes(), warn.tobytes())

    def test_cellular_warning_fits_with_everything_else(self):
        # all optional parts at once, longest language: nothing may run into the footer line
        for code in ("de", "pl", "fr", "es"):
            c = self.card(cellular_warn=True, notes="Line one\nLine two", lang=code)
            self.assertLess(dark_pixels(c, (60, 836, 1440, 846)), 5, code)         # clear gap above the footer text
            self.assertEqual(c.size, (1500, 900))
        c = self.card(cellular_warn=True, notes="n", qr=False, dark=True)
        self.assertGreater(self.amber(c, (60, 560, 1440, 730)), 400)

    def test_cli_cellularwarn(self):
        d = Path(tempfile.mkdtemp())
        self.assertEqual(self.run_cli("--user", "a", "--address", "1.2.3.4:80", "--password-stdin", "--cellularwarn", "-o", str(d / "w.png"), stdin="pw1234\n")[0], 0)
        self.assertTrue((d / "w.png").exists())

    def test_validation(self):
        for bad in (dict(user=""), dict(password=""), dict(address="not an address"), dict(address="http://1.2.3.4:80")):
            with self.assertRaises(im.CardError, msg=str(bad)):
                self.card(**bad)

    def run_cli(self, *args, stdin=None):
        out, err = io.StringIO(), io.StringIO()
        old = sys.stdin
        if stdin is not None:
            sys.stdin = io.StringIO(stdin)
        try:
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                rc = im.main(list(args))
        finally:
            sys.stdin = old
        return rc, out.getvalue(), err.getvalue()

    def test_cli_writes_files_privately(self):
        d = Path(tempfile.mkdtemp())
        rc, out, _ = self.run_cli("--user", "anna", "--address", "10.0.0.5:8080", "--password-stdin", "-o", str(d / "a.png"), stdin="Secret-1\n")
        self.assertEqual(rc, 0, out)
        self.assertEqual(stat.S_IMODE((d / "a.png").stat().st_mode), 0o600)         # holds a password: owner only
        self.assertNotIn("Secret-1", out)                                           # the password is never echoed
        for ext, magic in ((".pdf", b"%PDF"), (".jpg", b"\xff\xd8")):
            rc, _, _ = self.run_cli("--user", "a", "--address", "10.0.0.5:8080", "--password-stdin", "--notes", "hi", "-o", str(d / ("a" + ext)), stdin="pw1234\n")
            self.assertEqual(rc, 0)
            self.assertEqual((d / ("a" + ext)).read_bytes()[:4][:len(magic)], magic)
        self.assertEqual(self.run_cli("--user", "a", "--address", "bad addr", "--password-stdin", "-o", str(d / "x.png"), stdin="pw\n")[0], 1)
        self.assertEqual(self.run_cli("--user", "a", "--address", "1.2.3.4:80", "-o", str(d / "x.png"), stdin="")[0], 1)   # no password source

    def test_detect_address(self):
        old = im.lan_ips
        im.lan_ips = lambda: ["192.168.7.9"]
        try:
            addr, scheme = im.detect_address()
        finally:
            im.lan_ips = old
        self.assertRegex(addr, r"^192\.168\.7\.9:\d+$")
        self.assertIn(scheme, ("http", "https"))
        im.lan_ips = lambda: []
        try:
            with self.assertRaises(im.CardError):
                im.detect_address()
        finally:
            im.lan_ips = old


if __name__ == "__main__":
    unittest.main()
