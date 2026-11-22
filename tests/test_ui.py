"""ui.js (languages, themes, accent colours), page injection and the per-user preferences."""
import os as _os, tempfile as _tf
_os.environ.setdefault("SSC_DATA_DIR", _tf.mkdtemp(prefix="ssc-test-"))      # before core is imported: the tests must never touch the real ~/.ssc
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import core  # noqa: E402

NODE = shutil.which("node")


def node(code):
    """Run a snippet in Node. The code goes through a UTF-8 file (not the command line: Windows limits its length) and the
    output is read as UTF-8 (not the console code page)."""
    f = Path(tempfile.mkdtemp()) / "snippet.js"
    code = code.replace('require("./ui.js")', "require(%s)" % json.dumps(str(ROOT / "ui.js")))
    f.write_text(code, encoding="utf-8")
    r = subprocess.run([NODE, str(f)], capture_output=True, encoding="utf-8", timeout=60, cwd=ROOT)
    if r.returncode != 0:
        raise AssertionError(r.stderr[-800:])
    return r.stdout


@unittest.skipUnless(NODE, "Node.js is not installed")
class RuntimeTests(unittest.TestCase):
    def test_python_and_js_agree_on_the_allowed_values(self):
        out = json.loads(node('const u=require("./ui.js");console.log(JSON.stringify({l:Object.keys(u.LANGS),m:u.MODES,p:Object.keys(u.PALETTES),a:Object.keys(u.ACCENTS),i:u.UIS}))'))
        self.assertEqual(tuple(out["l"]), core.PREF_LANGS)
        self.assertEqual(tuple(out["m"]), core.PREF_MODES)
        self.assertEqual(tuple(out["p"]), core.PREF_PALETTES)
        self.assertEqual(tuple(out["a"]), core.PREF_ACCENTS)
        self.assertEqual(tuple(out["i"]), core.PREF_UIS)

    def test_dictionary_is_complete_and_consistent(self):
        src = (ROOT / "ui.js").read_text(encoding="utf-8")
        body = re.search(r"var D = \[([\s\S]*?)\n\];", src).group(1)
        out = node("const rows=eval('['+%s.replace(/\\/\\/.*$/gm,'')+']');console.log(JSON.stringify(rows))" % json.dumps(body))
        rows = json.loads(out)
        self.assertGreater(len(rows), 150)
        keys = [r[0] for r in rows]
        self.assertEqual(len(keys), len(set(keys)), "duplicate English keys")
        for r in rows:
            self.assertEqual(len(r), 8, r[0])                                   # English + 7 languages
            self.assertTrue(all(c.strip() for c in r), r[0])
            ph = sorted(re.findall(r"\{\w+\}", r[0]))
            for t in r[1:]:
                self.assertEqual(sorted(re.findall(r"\{\w+\}", t)), ph, (r[0], t))   # {n} / {d} survive in every language

    def test_translation(self):
        out = node('''const u=require("./ui.js");const r={};
          u.set({lang:"de"},{silent:true});
          r.a=u.tr("Homework");r.b=u.tr("  Today ");r.c=u.tr("due in 3 days (11-24)");r.d=u.tr("2 days overdue (11-20)");r.e=u.tr("1 day overdue (11-20)");
          r.f=u.tr("Homework due Friday: p.12");r.g=u.t("in {n} days",{n:5});r.h=u.tr("3 missed · 2 due soon");r.i=u.tr("Week of 2026-11-23");r.j=u.tr("due 2026-11-30");
          u.set({lang:"fr"},{silent:true});r.k=u.tr("Exams");u.set({lang:"pl"},{silent:true});r.l=u.tr("Sign in");
          u.set({lang:"en"},{silent:true});r.m=u.tr("Homework");console.log(JSON.stringify(r))''')
        r = json.loads(out)
        self.assertEqual((r["a"], r["b"], r["c"], r["d"], r["e"]), ("Hausaufgaben", "  Heute ", "fällig in 3 Tagen (11-24)", "2 Tage überfällig (11-20)", "1 Tag überfällig (11-20)"))
        self.assertEqual(r["f"], "Homework due Friday: p.12")                  # user text is never touched
        self.assertEqual((r["g"], r["h"], r["i"], r["j"]), ("in 5 Tagen", "3 verpasst · 2 bald fällig", "Woche ab 2026-11-23", "due 2026-11-30"))
        self.assertEqual((r["k"], r["l"], r["m"]), ("Examens", "Zaloguj", "Homework"))

    def test_sentences_and_user_text_are_not_mistaken_for_patterns(self):
        out = node('''const u=require("./ui.js");u.set({lang:"de"},{silent:true});const r=[];
          ["Dismiss homework automatically once it is done","due to the exam, bring a pen","due 2026-11-30","2 done","3 missed · 2 due soon","Learn 3 days of vocabulary",
           "Week of 2026-11-23","in 5 days","Show earlier (7)","Show earlier (many)"].forEach(x=>r.push(u.tr(x)));console.log(JSON.stringify(r))''')
        r = json.loads(out)
        self.assertEqual(r[0], "Dismiss homework automatically once it is done")        # a sentence ending in "done" is not "{n} done"
        self.assertEqual((r[1], r[2]), ("due to the exam, bring a pen", "due 2026-11-30"))
        self.assertEqual((r[3], r[4], r[5]), ("2 erledigt", "3 verpasst · 2 bald fällig", "Learn 3 days of vocabulary"))
        self.assertEqual((r[6], r[7], r[8], r[9]), ("Woche ab 2026-11-23", "in 5 Tagen", "Frühere anzeigen (7)", "Show earlier (many)"))   # counts are digits only

    def test_user_written_text_is_marked_untranslatable(self):
        s = (ROOT / "index.html").read_text(encoding="utf-8")
        for needle in ('class="hw-text" translate="no"', 'class="topic" translate="no"'):
            self.assertIn(needle, s)

    def test_invalid_preferences_are_dropped(self):
        out = node('const u=require("./ui.js");u.set({lang:"xx",theme:"sideways",accent:"nope",palette:"nope"},{silent:true});console.log(JSON.stringify(u.prefs()))')
        self.assertEqual(json.loads(out).get("accent"), None)
        self.assertEqual(json.loads(out).get("theme"), None)

    def test_every_page_script_is_valid_javascript(self):
        for name in ("index.html", "parent.html", "classic/index.html", "classic/parent.html", "server/login.html", "server/admin.html", "ui.js"):
            text = (ROOT / name).read_text(encoding="utf-8")
            scripts = [text] if name.endswith(".js") else re.findall(r"<script>(.*?)</script>", text, re.S)
            for i, js in enumerate(scripts):
                f = Path(tempfile.mkdtemp()) / "c.js"
                f.write_text(js, encoding="utf-8")
                r = subprocess.run([NODE, "--check", str(f)], capture_output=True, encoding="utf-8")
                self.assertEqual(r.returncode, 0, f"{name} script {i}: {r.stderr[:300]}")


class PageTests(unittest.TestCase):
    def test_load_page_inlines_the_runtime(self):
        html = core.load_page(ROOT / "parent.html")
        self.assertNotIn("<!--SSC-UI-->", html)
        self.assertIn("window.SSCUI", html)
        self.assertNotIn("SSC_PREFS_API=true", html)                                 # the read-only Parent link server never saves preferences
        self.assertIn("window.SSC_PREFS_API=true", core.load_page(ROOT / "index.html", prefs_api=True))
        if NODE:                                                                 # the inlined code must not break the page's scripts
            for i, js in enumerate(re.findall(r"<script>(.*?)</script>", html, re.S)):
                f = Path(tempfile.mkdtemp()) / "c.js"
                f.write_text(js, encoding="utf-8")
                self.assertEqual(subprocess.run([NODE, "--check", str(f)], capture_output=True, encoding="utf-8").returncode, 0, f"script {i}")

    def test_every_page_with_a_marker_gets_it(self):
        for name in ("index.html", "parent.html", "server/login.html"):
            self.assertIn("<!--SSC-UI-->", (ROOT / name).read_text(encoding="utf-8"), name)

    def test_homework_and_exam_views_show_subjects_and_dates(self):
        s = (ROOT / "index.html").read_text(encoding="utf-8")
        for needle in ("function subjChip", "class=\"hw-top\"", "function examItem", "datebox", "Date unknown", "By subject", "By due date"):
            self.assertIn(needle, s, needle)
        p = (ROOT / "parent.html").read_text(encoding="utf-8")
        for needle in ("subjChip(h.subject)", "dWeek(e.date)", "No subject"):
            self.assertIn(needle, p, needle)

    def test_interface_choice(self):
        self.assertEqual(core.ui_choice("ssc_sess=abc; ssc_ui=classic"), "classic")
        self.assertEqual(core.ui_choice("ssc_ui=classic"), "classic")
        for c in ("", None, "ssc_ui=beta", "ssc_ui=other", "xssc_ui=classic", "foo=ssc_ui=classic"):
            self.assertEqual(core.ui_choice(c), "beta", c)
        self.assertEqual(core.clean_prefs({"ui": "classic"}), {"ui": "classic"})
        self.assertEqual(core.clean_prefs({"ui": "../../etc"}), {})

    def test_classic_pages(self):
        for name in ("index.html", "parent.html"):
            beta = core.load_page(ROOT / name, prefs_api=True)
            old = core.load_page(ROOT / name, prefs_api=True, classic=True)
            self.assertNotIn("window.SSC_CLASSIC=true", beta)
            self.assertIn("window.SSC_CLASSIC=true", old)
            self.assertIn("ssc-ui-switch", old)
            self.assertIn("SSCUI.set({ui:'beta'})", old)
            self.assertIn("root.SSCUI = API", old)
            self.assertNotEqual(beta, old)
            self.assertNotIn("<!--SSC-UI-->", old)
            self.assertEqual(old.count("</body>"), 1)
        self.assertIn("glass", core.load_page(ROOT / "index.html", classic=True))
        self.assertNotIn("SSC_CLASSIC=true", core.load_page(ROOT / "server/login.html", classic=True))

    def test_classic_runtime_keeps_its_own_design(self):
        self.assertIn("root.SSC_CLASSIC) return;", (ROOT / "ui.js").read_text(encoding="utf-8"))

    def test_clean_prefs(self):
        self.assertEqual(core.clean_prefs({"lang": "de", "theme": "dark", "palette": "ocean", "accent": "teal", "evil": "<script>"}),
                         {"lang": "de", "theme": "dark", "palette": "ocean", "accent": "teal"})
        self.assertEqual(core.clean_prefs({"lang": "klingon", "theme": 5, "accent": "#ff0000"}), {})
        self.assertEqual(core.clean_prefs(None), {})
        self.assertEqual(core.clean_prefs("lang=de"), {})


if __name__ == "__main__":
    unittest.main()
