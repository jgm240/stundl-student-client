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


class PhoneLayoutTests(unittest.TestCase):
    """The Beta UI on a phone (checked in a real browser at 375 px: no page wider than the screen). These keep the rules from being lost."""

    @classmethod
    def setUpClass(cls):
        cls.page = (ROOT / "index.html").read_text(encoding="utf-8")

    def test_the_phone_rules_are_there(self):
        css = self.page[self.page.index("/* ---------- phones"):self.page.index("</style></head>")]
        for rule in ("#top{flex-wrap:wrap", "font-size:16px", "overflow-x:hidden", "table{display:block", ".item.hw{flex-wrap:wrap", "env(safe-area-inset-bottom)", "min-height:40px"):
            self.assertIn(rule, css, rule)
        self.assertIn("@media(max-width:900px)", css)

    def test_the_page_can_use_the_whole_screen(self):
        self.assertIn("viewport-fit=cover", self.page)                       # safe-area insets (notch, home bar) only work with this
        self.assertIn("width=device-width", self.page)

    def test_the_account_bar_of_the_server_does_not_cover_the_tab_bar(self):
        src = (ROOT / "server/serverapp.py").read_text(encoding="utf-8")
        self.assertIn("#sscbar{bottom:calc(78px + env(safe-area-inset-bottom))", src)


FAKE_BROWSER = r"""
// a tiny stand-in for a browser: cookies, local/session storage, fetch, reload - enough to run ui.js's choice of interface
function makeWindow(opts) {
  const mem = () => { const m = {}; return { getItem: k => (k in m ? m[k] : null), setItem: (k, v) => { m[k] = String(v); }, removeItem: k => { delete m[k]; }, _m: m }; };
  const w = { localStorage: opts.local || mem(), sessionStorage: opts.session || mem(), SSC_PREFS_API: true, SSC_CLASSIC: !!opts.classic,
              location: { protocol: "http:", reload() { w.reloads++; } }, reloads: 0, posts: [], gets: 0, cookies: [],
              navigator: { language: "en" }, document: { readyState: "complete", title: "t", body: null, documentElement: { style: { setProperty() {}, removeProperty() {} }, setAttribute() {} } } };
  Object.defineProperty(w.document, "cookie", { set(v) { w.cookies.push(v); }, get() { return ""; } });
  w.fetch = (url, o) => {
    if (o && o.method === "POST") {
      w.posts.push({ body: JSON.parse(o.body), keepalive: !!o.keepalive });
      return opts.postHangs ? new Promise(() => {}) : Promise.resolve({ ok: opts.postOk !== false });
    }
    w.gets++;
    return Promise.resolve({ ok: true, json: () => Promise.resolve(opts.remote || {}) });
  };
  return w;
}
const UI_PATH = @UI@;
function load(w) { delete require.cache[require.resolve(UI_PATH)]; global.window = w; return require(UI_PATH); }
const tick = (ms) => new Promise(r => setTimeout(r, ms || 20));
"""


@unittest.skipUnless(NODE, "Node.js is not installed")
class InterfaceChoiceTests(unittest.TestCase):
    """Switching between Beta and Classic - including the way iOS Safari broke it: it cancels a request that is still on its way when the page
    reloads, so the server never heard of the choice and the next page load took the old one back."""

    def run_js(self, body):
        code = FAKE_BROWSER.replace("@UI@", json.dumps(str(ROOT / "ui.js"))) + "(async () => {" + body + "})().catch(e => { console.error(e); process.exit(1); });"
        return json.loads(node(code))

    def test_the_server_hears_of_the_choice_before_the_page_reloads(self):
        out = self.run_js("""
          const w = makeWindow({ classic: true }); const u = load(w); await tick();
          u.set({ ui: "beta" });
          const early = { posts: w.posts.length, reloads: w.reloads, pending: w.localStorage.getItem("ssc-ui-pending") };   // right away: sent, not reloaded yet
          await tick(60);
          console.log(JSON.stringify({ early, posts: w.posts, reloads: w.reloads, pending: w.localStorage.getItem("ssc-ui-pending"), cookie: w.cookies.filter(c => c.startsWith("ssc_ui=")).pop() }));""")
        self.assertEqual(out["early"], {"posts": 1, "reloads": 0, "pending": "beta"})        # no 300 ms delay, no reload before the request is done
        self.assertEqual(out["posts"][0]["body"]["ui"], "beta")
        self.assertTrue(out["posts"][0]["keepalive"])
        self.assertEqual(out["reloads"], 1)
        self.assertIsNone(out["pending"])                                                      # confirmed by the server: no longer pending
        self.assertIn("ssc_ui=beta", out["cookie"])

    def test_a_stale_server_value_does_not_undo_a_choice_the_server_missed(self):
        # iPhone: the POST was cut off by the reload. The page that now loads is Beta (cookie), the server still says classic.
        local = """const local = { m: { "ssc-ui-pending": "beta", "ssc-prefs": JSON.stringify({ ui: "beta" }) }, getItem(k) { return k in this.m ? this.m[k] : null; }, setItem(k, v) { this.m[k] = String(v); }, removeItem(k) { delete this.m[k]; } };"""
        out = self.run_js(local + """
          const w = makeWindow({ classic: false, local, remote: { ui: "classic", lang: "de" } }); const u = load(w); await tick(500);                       // the re-send is debounced (300 ms)
          console.log(JSON.stringify({ ui: u.prefs().ui, lang: u.prefs().lang, reloads: w.reloads, posts: w.posts.map(p => p.body.ui), pending: local.getItem("ssc-ui-pending") }));""")
        self.assertEqual(out["ui"], "beta")                                                    # our choice wins
        self.assertEqual(out["lang"], "de")                                                    # everything else still comes from the server
        self.assertEqual(out["reloads"], 0)                                                    # no switching back
        self.assertEqual(out["posts"], ["beta"])                                               # and the server is told again
        self.assertIsNone(out["pending"])

    def test_without_a_pending_choice_the_server_wins_once(self):
        out = self.run_js("""
          const session = (() => { const m = {}; return { getItem: k => (k in m ? m[k] : null), setItem: (k, v) => { m[k] = String(v); }, removeItem: k => { delete m[k]; } }; })();
          const w = makeWindow({ classic: false, session, remote: { ui: "classic" } }); load(w); await tick(60);
          const first = w.reloads;
          const w2 = makeWindow({ classic: false, session, remote: { ui: "classic" } }); load(w2); await tick(60);          // the same tab after the reload, server still says classic
          console.log(JSON.stringify({ first, second: w2.reloads }));""")
        self.assertEqual(out, {"first": 1, "second": 0})                                       # one reload at most per tab: never a loop

    def test_no_session_storage_means_no_reload(self):
        out = self.run_js("""
          const bad = { getItem() { throw new Error("blocked"); }, setItem() { throw new Error("blocked"); }, removeItem() {} };
          const w = makeWindow({ classic: false, session: bad, remote: { ui: "classic" } }); load(w); await tick(60);
          console.log(JSON.stringify({ reloads: w.reloads }));""")
        self.assertEqual(out["reloads"], 0)

    def test_when_the_server_does_not_answer_the_page_reloads_anyway(self):
        out = self.run_js("""
          const w = makeWindow({ classic: true, postHangs: true }); const u = load(w); await tick();
          u.set({ ui: "beta" }); await tick(100);
          const before = w.reloads;
          await tick(2600);
          console.log(JSON.stringify({ before, after: w.reloads, pending: w.localStorage.getItem("ssc-ui-pending") }));""")
        self.assertEqual((out["before"], out["after"]), (0, 1))                                # waits a little, then reloads
        self.assertEqual(out["pending"], "beta")                                               # still pending: the next page load tells the server again

    def test_pages_without_a_server_use_the_cookie_only(self):
        out = self.run_js("""
          const w = makeWindow({ classic: true }); w.SSC_PREFS_API = false; const u = load(w); await tick();
          u.set({ ui: "beta" }); await tick(60);
          console.log(JSON.stringify({ posts: w.posts.length, gets: w.gets, reloads: w.reloads, cookie: w.cookies.filter(c => c.startsWith("ssc_ui=")).pop() }));""")
        self.assertEqual((out["posts"], out["gets"], out["reloads"]), (0, 0, 1))               # the Parent link server is never asked (that would count as a wrong guess)
        self.assertIn("ssc_ui=beta", out["cookie"])


if __name__ == "__main__":
    unittest.main()
