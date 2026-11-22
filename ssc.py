#!/usr/bin/env python3
"""Stundl Student Client (SSC; English: "SSC Student Client") - download WebUntis data (timetable, homework, exams, grades, absences) for offline use.

  ssc.py login --server YOURSCHOOL.webuntis.com --school your-school [--browser firefox]
  ssc.py sync [--full] [--notify] [--interactive]
  ssc.py gui | tui | serve [--no-sync]    (sync on launch)
  ssc.py offline                  open the self-contained offline copy
  ssc.py changes | search TEXT | stats
  ssc.py homework [list|dismiss ID|restore ID] [--all]   status + automatic dismissal (config: hw_soon_days, hw_dismiss_done, hw_dismiss_missed_days)
  ssc.py done [ID ...]            list homework / toggle done
  ssc.py export ics|homework|exams|grades|absences [-o FILE]
  ssc.py schedule install [--time 06:45] [--remind-time 17:30] | remove | status
  ssc.py parent serve|install|remove|status|rotate|sections LIST   read-only Parent website for a wall display
  ssc.py plan [list|add TITLE|done ID|undone ID|edit ID|rm ID]   your own study plan (shown in Parent mode)
  ssc.py remind | grade-calc SUBJECT [--add 2,3] [--target 2.0]
  ssc.py menubar [install|remove|status]   menu bar (macOS) / tray icon (Windows, Linux); install = starts at login
  ssc.py config set icloud true   copy offline pages to iCloud Drive (iPhone)
  ssc.py config set absence_warn 0.15 | absence_critical 0.25
  ssc.py widget                   refresh the WidgetKit widget data (see widget/)
"""
import argparse, io, ipaddress, json, os, re, secrets, socket, subprocess, sys, threading, time, webbrowser
from datetime import date, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, urlparse
from urllib.request import Request, urlopen

import core
from ssc_version import VERSION as SSC_VERSION
import jobs
import vault

HERE = Path(__file__).resolve().parent
# The packaged app must look for browsers in the shared cache (where `ensure_browser` downloads them),
# not inside its own bundle, where Playwright would otherwise search.
if os.environ.get("PLAYWRIGHT_BROWSERS_PATH") in (None, "", "0"):
    os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(core.playwright_cache())
JWT = re.compile(r"^[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]*$")


def paths():
    h = core.HOME
    return h / "browser-profile", h / "config.json", h / "cookies.json"


# ------------------------------------------------------------------ browsers
# Any Chromium-based browser you already have is used as-is (no download). Firefox and WebKit are
# Playwright's own builds, downloaded once on first use. (Real Safari can't be automated; WebKit is the closest.)
APPS = {  # id -> (label, .app path, playwright channel or executable inside the app)
    "chrome": ("Google Chrome", "/Applications/Google Chrome.app", ("channel", "chrome")),
    "msedge": ("Microsoft Edge", "/Applications/Microsoft Edge.app", ("channel", "msedge")),
    "brave": ("Brave", "/Applications/Brave Browser.app", ("exe", "Contents/MacOS/Brave Browser")),
    "vivaldi": ("Vivaldi", "/Applications/Vivaldi.app", ("exe", "Contents/MacOS/Vivaldi")),
    "arc": ("Arc", "/Applications/Arc.app", ("exe", "Contents/MacOS/Arc")),
    "opera": ("Opera", "/Applications/Opera.app", ("exe", "Contents/MacOS/Opera")),
}
BUNDLED = {"chromium": "Chromium (downloaded once)", "firefox": "Firefox (downloaded once)", "webkit": "WebKit / Safari engine (downloaded once)"}


def _exists_any(paths):
    return any(Path(os.path.expandvars(p)).exists() for p in paths)


_OTHER = {  # Windows / Linux: Chrome and Edge are driven through Playwright "channels"
    "chrome": ("Google Chrome", {"win": [r"%ProgramFiles%\Google\Chrome\Application\chrome.exe", r"%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe",
                                         r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"],
                                 "linux": ["/usr/bin/google-chrome", "/usr/bin/google-chrome-stable", "/opt/google/chrome/chrome"]}),
    "msedge": ("Microsoft Edge", {"win": [r"%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe", r"%ProgramFiles%\Microsoft\Edge\Application\msedge.exe"],
                                  "linux": ["/usr/bin/microsoft-edge", "/opt/microsoft/msedge/msedge"]}),
}


def _apps():
    """id -> (label, installed?, playwright launch kwargs) for the browsers you may already have."""
    if sys.platform == "darwin":
        return {k: (label, Path(app).exists(), {"channel": val} if kind == "channel" else {"executable_path": str(Path(app) / val)})
                for k, (label, app, (kind, val)) in APPS.items()}
    osk = "win" if sys.platform.startswith("win") else "linux"
    return {k: (label, _exists_any(paths[osk]), {"channel": k}) for k, (label, paths) in _OTHER.items()}


def available_browsers():
    out = [dict(id="auto", label="Automatic", installed=True)]
    out += [dict(id=k, label=v[0], installed=v[1]) for k, v in _apps().items()]
    out += [dict(id=k, label=v, installed=True) for k, v in BUNDLED.items()]
    return out


def browser_spec(name):
    """-> (id, playwright engine, launch kwargs)"""
    apps = _apps()
    if name in (None, "", "auto"):
        name = next((k for k, v in apps.items() if v[1]), "chromium")
    if name in apps:
        label, ok, kw = apps[name]
        if ok:
            return name, "chromium", kw
        print(f"{label} not found - using bundled Chromium instead.")
        return "chromium", "chromium", {}
    if name in BUNDLED:
        return name, name, {}
    sys.exit(f"Unknown browser '{name}'. Options: auto, {', '.join(list(apps) + list(BUNDLED))}")


def ensure_browser(engine):
    root = Path(os.environ["PLAYWRIGHT_BROWSERS_PATH"])
    if root.exists() and any(root.glob(engine + "-*")):
        return
    print(f"Downloading {engine} browser (first run only, ~150 MB)...", flush=True)
    from playwright._impl._driver import compute_driver_executable, get_driver_env
    d = compute_driver_executable()
    cmd = [str(x) for x in d] if isinstance(d, (tuple, list)) else [str(d)]
    subprocess.run([*cmd, "install", engine], env=get_driver_env(), check=True)


def conf():
    _, cf, _ = paths()
    if not cf.exists():
        sys.exit("Not configured. Run: ssc.py login --server X.webuntis.com --school NAME")
    return json.loads(cf.read_text())


def token_of(r):
    t = r.text().strip().strip('"').strip()
    return t if r.ok and JWT.match(t) else None


def open_ctx(p, headless, sub=None):
    name, engine, kw = browser_spec(core.config().get("browser", "auto"))
    for attempt in (0, 1):
        prof = paths()[0] / (name if not sub else f"{name}-{sub}")  # one profile per browser (and purpose)
        prof.mkdir(parents=True, exist_ok=True)
        if not kw:
            ensure_browser(engine)
        try:
            return getattr(p, engine).launch_persistent_context(str(prof), headless=headless, **kw)
        except Exception as e:
            if attempt or not kw:
                raise
            print(f"Could not start {name} ({str(e).splitlines()[0][:80]}) - falling back to bundled Chromium.")
            name, engine, kw = "chromium", "chromium", {}


def wait_token(ctx, base, seconds):
    """Poll until the browser session yields a WebUntis API token; saves cookies on success."""
    _, _, cookies = paths()
    end = time.time() + seconds
    while True:
        try:
            jwt = token_of(ctx.request.get(f"{base}/api/token/new"))
            if jwt:
                try:
                    vault.set("webuntis-cookies", json.dumps(ctx.cookies()))   # Keychain only - never a file
                except vault.VaultError as e:
                    print("Warning: couldn't store the session securely, so you'll have to sign in again next time:", e, flush=True)
                return jwt
        except Exception:
            pass
        if time.time() >= end:
            return None
        time.sleep(2)


def auto_login(ctx, base, school):
    """Headless re-login: open WebUntis, press the Tirol/Bildungs-ID button, and hope the
    identity provider still has a valid SSO session in the saved browser profile."""
    page = ctx.new_page()
    try:
        page.goto(f"{base}/?school={school}", wait_until="load", timeout=30000)
        btn = page.get_by_text(re.compile("tirol|bildungs", re.I)).first
        if btn.count():
            btn.click(timeout=5000)
    except Exception:
        pass
    jwt = wait_token(ctx, base, 30)
    page.close()
    return jwt


# ------------------------------------------------------------------ login
def cmd_login(a):
    core.HOME.mkdir(parents=True, exist_ok=True)
    cfg = {**core.config(), "server": a.server, "school": a.school}
    if a.browser:
        cfg["browser"] = a.browser
    core.save_config(cfg)
    interactive_login(a.server, a.school)


def interactive_login(server, school):
    from playwright.sync_api import sync_playwright
    base = f"https://{server}/WebUntis"
    with sync_playwright() as p:
        ctx = open_ctx(p, headless=False)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto(f"{base}/?school={school}")
        print("Log in in the browser window (choose the Tirol / login.tirol.gv.at option).", flush=True)
        if wait_token(ctx, base, 600):
            print("Login successful, session saved.")
            ctx.close()
            core.wipe_browser_profiles()
            return True
        ctx.close()
    sys.exit("Timed out waiting for login.")


# ------------------------------------------------------------------ sync
class _NeedLogin(Exception):
    """Raised inside the sync once its browser session is closed, so the login window can open safely.
    (Playwright's sync API can't be started while another sync_playwright() block is still active.)"""


def _sync_school(a, _retry=True):
    try:
        return _sync_school_body(a)
    except _NeedLogin:
        c = conf()
        if (a.interactive or sys.stdin.isatty()) and _retry:
            print("Session expired and automatic re-login failed - opening a browser window.", flush=True)
            interactive_login(c["server"], c["school"])
            return _sync_school(a, _retry=False)
        if a.notify:
            core.notify(core.APP_SHORT, "Login expired - run: ssc.py login")
        sys.exit("No valid token - session expired. Run `ssc.py login` again.")
    finally:
        core.wipe_browser_profiles()


def _sync_school_body(a):
    from playwright.sync_api import sync_playwright
    c = conf()
    base = f"https://{c['server']}/WebUntis"
    _, _, cookies = paths()
    con = core.db()
    changes = []
    with sync_playwright() as p:
        ctx = open_ctx(p, headless=True)
        saved = vault.get("webuntis-cookies")
        if saved:
            ctx.add_cookies(json.loads(saved))
        try:
            first = ctx.request.get(f"{base}/api/token/new")
        except Exception as e:  # no network / WebUntis unreachable
            ctx.close()
            print(f"Offline or WebUntis unreachable ({str(e).splitlines()[0][:80]}) - keeping stored data.")
            sys.exit(3)
        keep = str(core.config().get("keep_sso_session", "")).lower() in ("1", "true", "yes", "on")
        jwt = token_of(first) or (auto_login(ctx, base, c["school"]) if keep else None)  # no saved SSO session otherwise
        if not jwt:
            ctx.close()
            raise _NeedLogin()  # leaves the with-block first; the login window opens in _sync_school
        auth = {"Authorization": "Bearer " + jwt, "Accept": "application/json"}
        req = ctx.request

        def get(path, **params):
            try:
                r = req.get(f"{base}{path}", params={k: v for k, v in params.items() if v is not None}, headers=auth)
            except Exception as e:
                print(f"  ! {path} -> {str(e).splitlines()[0][:80]}")
                return None
            if not r.ok:
                print(f"  ! {path} -> HTTP {r.status}")
                return None
            try:
                return r.json()
            except Exception:
                return None

        def save(key, data):
            if data is None:
                return
            old = con.execute("select body from store where key=?", (key,)).fetchone()
            if old:
                changes.extend(core.diff(key, json.loads(old[0]), data))
            con.execute("insert or replace into store values(?,?,?)", (key, time.time(), json.dumps(data)))
            con.commit()
            print("  saved", key)

        app = get("/api/rest/view/v1/app/data") or {}
        save("app", app)
        sid = ((app.get("user") or {}).get("person") or {}).get("id")
        if not sid:
            print("  ! could not determine student id; timetable/exams/absences may fail")

        today = date.today()
        monday = today - timedelta(days=today.weekday())
        ys = date(today.year if today.month >= 8 else today.year - 1, 9, 1)
        first = (ys - timedelta(days=ys.weekday())) if a.full else monday - timedelta(weeks=2)
        w = first
        while w <= monday + timedelta(weeks=8):
            save(f"timetable:{w.isoformat()}", get(
                "/api/rest/view/v1/timetable/entries", start=w.isoformat(), end=(w + timedelta(days=6)).isoformat(),
                format=2, resourceType="STUDENT", resources=sid, timetableType="MY_TIMETABLE"))
            w += timedelta(weeks=1)

        f, t = today - timedelta(days=30), today + timedelta(days=90)
        ymd = lambda d: d.strftime("%Y%m%d")
        save("homework", get("/api/homeworks/lessons", startDate=ymd(f), endDate=ymd(t)))
        save("exams", get("/api/exams", studentId=sid, klasseId=0, startDate=ymd(f), endDate=ymd(t)))
        save("absences", get("/api/classreg/absences/students", startDate=ymd(ys), endDate=ymd(today),
                             studentId=sid, excuseStatusId=-1))
        save("grades", get("/api/classreg/grade/gradeList", studentId=sid,
                           schoolyearId=(app.get("currentSchoolYear") or {}).get("id")))
        save("messages", get("/api/rest/view/v1/messages"))
        ctx.close()

    core.record_changes(con, changes)
    print("Offline copy:", core.write_snapshot(core.APPDIR / "index.html"))
    print(f"Sync done. {len(changes)} change(s).")
    for k, t in changes:
        print("  *", t)
    if a.notify:
        cmd_remind(a)
    if a.notify and changes:
        core.notify(core.APP_SHORT, f"{len(changes)} change(s): " + "; ".join(t for _, t in changes[:3]))


def _sync_direct(a):
    import untis_direct
    try:
        changes, notes = untis_direct.sync()
    except untis_direct.AuthError as e:
        sys.exit(f"WebUntis refused the Untis Mobile login: {e}. Was the QR code regenerated, or is the clock wrong?")
    except untis_direct.UntisError as e:
        sys.exit(str(e))
    except OSError as e:
        sys.exit(f"Can't reach WebUntis ({e}) - keeping the stored data.")
    print(f"Sync done. {len(changes)} change(s).")
    for k, t in changes:
        print("  *", t)
    for n in notes:
        print("  ! " + n)
    core.write_snapshot(core.APPDIR / "index.html")


def cmd_server(a):
    sys.path.insert(0, str(Path(__file__).resolve().parent / "server"))
    import serverapp
    serverapp.run()


def watch_status():
    try:
        import bleak  # noqa: F401
        ble = True
    except ImportError:
        ble = False
    return dict(address=core.config().get("pinetime_address"), bluetooth=ble)


def cmd_update(a):
    import updater
    try:
        info = updater.check()
    except updater.UpdateError as e:
        sys.exit(f"Update: {e}")
    print(f"Installed: {info['current']}   Latest on GitHub: {info['latest'] or 'unknown'}")
    if not info["update_available"]:
        return print("You're up to date.")
    print("A newer version is available:", info["url"])
    if info["flavor"] == "server":
        return print("This is an SSC Server: run  ssconfig update --install")
    if not a.download:
        return print("Download it with:  ssc update --download")
    try:
        f, v = updater.download_app(Path.home() / "Downloads", progress=None)
    except updater.UpdateError as e:
        sys.exit(f"Update: {e}")
    print(f"Saved {f} (checksum verified). Quit SSC, then unpack it over the old one." if info["flavor"] == "app" else
          f"Saved {f} (checksum verified). From a source folder you can also run:  git pull")


def cmd_pinetime(a):
    import asyncio
    import pinetime
    if a.action == "scan":
        for addr, name in asyncio.run(pinetime.scan()):
            print(addr, name)
    elif a.action == "pair":
        if not a.address:
            print("usage: ssc pinetime pair <address>")
            return
        pinetime.set_address(a.address)
        print("Saved. Try: ssc pinetime test")
    elif a.action == "forget":
        pinetime.set_address(None)
    else:
        print(pinetime.sync(test=a.action == "test"))


def _untis_credentials(a):
    """From the link given as the argument, or from --server/--school/--user (+ --key, asked for without echo)."""
    import untis_direct, getpass
    if a.value:
        cfg = untis_direct.parse_qr(a.value)
        if not cfg:
            sys.exit("That link doesn't work: " + untis_direct.explain_qr(a.value))
        return cfg
    if a.server or a.school or a.user or a.key:
        key = a.key or getpass.getpass("Untis Mobile key (not shown): ")
        try:
            return untis_direct.check_credentials(a.server, a.school, a.user, key)
        except ValueError as e:
            sys.exit("Can't use these values - " + str(e))
    sys.exit("Give the untis:// link, or --server, --school and --user (the key is asked for).")


def cmd_untis(a):
    import untis_direct
    if a.action == "set":
        cfg = _untis_credentials(a)
        untis_direct.save_credentials(cfg)
        print("Saved:", untis_direct.describe(), " - check it with:  ssc untis test")
    elif a.action == "clear":
        untis_direct.clear_credentials(); print("Removed.")
    elif a.action == "test":
        cfg = untis_direct.load_credentials() or sys.exit("No key set. Use:  ssc untis set 'untis://setschool?...'")
        try:
            untis_direct.call(cfg, "getUserData2017")
            print("Login OK.")
        except untis_direct.UntisError as e:
            sys.exit(f"Login failed: {e}\n{untis_direct.login_hint(e)}".rstrip())
        except OSError as e:
            sys.exit(f"Can't reach {cfg['server']}: {e}")
    elif a.action == "raw":
        cfg = untis_direct.load_credentials() or sys.exit("No key set.")
        try:
            print(json.dumps(untis_direct.raw_call(cfg, a.value or "getHomeWork2017"), indent=1, ensure_ascii=False)[:20000])
        except (ValueError, untis_direct.UntisError, OSError) as e:
            sys.exit(f"{e}")
    else:
        print(untis_direct.describe() or "No Untis Mobile key set.")


def cmd_sync(a):
    if getattr(a, "direct", False) or str(core.config().get("sync_mode", "")) == "direct":
        r = _sync_direct(a)
    else:
        r = _sync_school(a)
    if core.config().get("pinetime_address"):      # a watch is set up: tell it what's new (never blocks or fails the sync)
        try:
            import pinetime
            print(pinetime.sync())
        except Exception as e:
            print("Watch skipped:", e)
    return r


# ------------------------------------------------------------------ small commands
def cmd_changes(a):
    con = core.db()
    rows = con.execute("select at, kind, text, seen from changes order by id desc limit ?", (a.n,)).fetchall()
    for at, k, t, seen in rows:
        print(time.strftime("%d.%m. %H:%M", time.localtime(at)), f"[{k}]", t)
    con.execute("update changes set seen=1"); con.commit()
    if not rows:
        print("No changes recorded yet.")


def cmd_search(a):
    for r in core.search(" ".join(a.text)):
        print(f"{r['date'][:16]:16}  {r['kind']:9} {r['title']}  {r['text']}")


def cmd_stats(a):
    s = core.stats(core.load())
    print("Grades:")
    for g in s["grades"]:
        print(f"  {g['subject']:20} avg {g['average']}   {g['marks']}")
    ab = s["absences"]
    print(f"Absences: {ab['total']} ({ab['excused']} excused, {ab['open']} open), {ab['days']} days")
    for sub, n in ab["missed_lessons_by_subject"].items():
        print(f"  missed {n:3} x {sub}")
    for l in ab["limits"]:
        flag = {"ok": "  ", "warn": "! ", "critical": "!!"}[l["level"]]
        print(f"  {flag} {l['subject']:12} missed {l['missed']}/{l['total']} ({l['pct']:.0%}) {l['level']}")
    print(" ", ab["note"])


def cmd_done(a):
    con = core.db()
    if a.ids:
        cur = core.done_ids(con)
        for i in a.ids:
            core.set_done(con, i, i not in cur)
    done = core.done_ids(con)
    for x in core.state(con)["homework"]:
        print(("[x]" if x["id"] in done or x["completed"] else "[ ]"), x["id"], x["due"], x["subject"], "-", x["text"])


def cmd_homework(a):
    con = core.db()
    if a.action in ("dismiss", "restore"):
        for i in a.ids:
            core.hw_set_dismiss(con, i, "dismissed" if a.action == "dismiss" else "restored")
    st = core.state(con)
    icons = {"missed": "!!", "soon": "! ", "pending": "  ", "done": "ok"}
    order = {"missed": 0, "soon": 1, "pending": 2, "done": 3}
    for h in sorted(st["homework"], key=lambda h: (order[h["status"]], h["due"])):
        if h["dismissed"] and not a.all:
            continue
        tag = f"  [dismissed: {h['dismiss_reason']}]" if h["dismissed"] else ""
        print(f"{h['id']:>8} {icons[h['status']]} {h['status']:8} due {h['due']}  {h['subject']}: {h['text']}{tag}")
    sm = st["homework_summary"]
    print(f"-- missed {sm['missed']} · due soon {sm['soon']} · pending {sm['pending']} · done {sm['done']} · dismissed {sm['dismissed']}")


def cmd_export(a):
    data = core.export_ics() if a.kind == "ics" else core.export_csv(a.kind)
    out = Path(a.output or f"ssc-{a.kind}.{'ics' if a.kind == 'ics' else 'csv'}")
    out.write_text(data, encoding="utf-8")
    print("Wrote", out.resolve())


def cmd_config(a):
    cfg = core.config()
    secret = lambda k: any(w in k.lower() for w in ("token", "secret", "password", "cookie"))
    if a.action == "set":
        if secret(a.key or ""):
            sys.exit("Secrets are kept in the Keychain and can't be set here.")
        cfg[a.key] = a.value
        core.save_config(cfg)
    print(json.dumps({k: ("***" if secret(k) else v) for k, v in cfg.items()}, indent=1))


def cmd_plan(a):
    con = core.db()
    try:
        if a.action == "add":
            if not a.arg:
                sys.exit('Usage: plan add "Learn vocabulary" [--date tomorrow] [--time 16:30] [--minutes 45] [--subject E]')
            i = core.plan_add(con, title=" ".join(a.arg), date=core.parse_plan_date(a.date), start=a.time or "", minutes=a.minutes or 0,
                              subject=a.subject or "", notes=a.notes or "")
            print("Added #%d" % i)
        elif a.action in ("done", "undone", "rm", "edit"):
            if not a.arg or not a.arg[0].isdigit():
                sys.exit(f"Usage: plan {a.action} ID")
            i = int(a.arg[0])
            if a.action == "rm":
                core.plan_delete(con, i)
            elif a.action == "edit":
                f = {k: v for k, v in dict(title=" ".join(a.arg[1:]) or None, date=core.parse_plan_date(a.date) if a.date else None,
                                           start=a.time, minutes=a.minutes, subject=a.subject, notes=a.notes).items() if v is not None}
                core.plan_update(con, i, **f)
            else:
                core.plan_update(con, i, done=a.action == "done")
            print("OK")
    except ValueError as e:
        sys.exit(f"Error: {e}")
    items = core.plan_items(con)
    for p in items:
        print(f"#{p['id']:<3} {p['date']} {p['start'] or '     '} {'[x]' if p['done'] else '[ ]'} {p['subject']:6} {p['title']}"
              + (f"  ({p['minutes']} min)" if p["minutes"] else ""))
    if not items:
        print("The study plan is empty. Add something: plan add \"Learn vocabulary\" --date tomorrow --minutes 30")


def cmd_remind(a):
    con = core.db()
    sent = {r[0] for r in con.execute("select key from notified")}
    todo = core.reminders(core.state(con), sent=sent)
    for key, title, msg in todo:
        core.notify(title, msg)
        con.execute("insert or replace into notified values(?,?)", (key, time.time()))
    con.commit()
    print(f"{len(todo)} reminder(s) sent.")


def cmd_gradecalc(a):
    add = [float(x) for x in re.split(r"[ ,;]+", a.add) if x] if a.add else []
    print(json.dumps(core.grade_calc(core.state()["stats"]["grades"], a.subject, add, a.target), indent=1))


# ------------------------------------------------------------------ Parent mode
PARENT_PORT = 8766
_PFAILS, _PCACHE = {}, {"t": 0.0, "v": ""}


def parent_port():
    try:
        return int(core.config().get("parent_port") or PARENT_PORT)
    except ValueError:
        return PARENT_PORT


def lan_hosts():
    hosts = []
    try:
        sk = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sk.connect(("10.255.255.255", 1))  # no packet is sent; picks the interface with the default route
        hosts.append(sk.getsockname()[0])
        sk.close()
    except Exception:
        pass
    if sys.platform == "darwin":
        r = subprocess.run(["scutil", "--get", "LocalHostName"], capture_output=True, text=True)
        if r.returncode == 0 and r.stdout.strip():
            hosts.append(r.stdout.strip() + ".local")
    else:
        name = socket.gethostname().split(".")[0]
        if name:
            hosts.append(name if sys.platform.startswith("win") else name + ".local")
    return [h for h in dict.fromkeys(hosts) if h and not h.startswith("127.")]


def parent_urls(port=None):
    tok, port = core.parent_token(), port or parent_port()
    return [dict(host=h, browser=f"http://{h}:{port}/p/{tok}/", wall=f"http://{h}:{port}/p/{tok}/wall") for h in lan_hosts()]


def parent_running():
    try:
        socket.create_connection(("127.0.0.1", parent_port()), timeout=0.4).close()
        return True
    except OSError:
        return False


def parent_status():
    return dict(installed=jobs.installed("parent"), running=parent_running(), port=parent_port(),
                urls=parent_urls(), sections=core.parent_sections(), all_sections=list(core.PARENT_SECTIONS))


def parent_data():
    if time.time() - _PCACHE["t"] > 15:
        _PCACHE.update(t=time.time(), v=json.dumps(core.parent_state()))
    return _PCACHE["v"]


def make_parent_handler():
    """Read-only server for the Parent website. The ONLY things it can ever return are the Parent page and the
    allow-listed data from core.parent_state(); there are no other routes and no writes."""
    class P(BaseHTTPRequestHandler):
        server_version, sys_version = "SSC", ""

        def log_message(self, *a): pass

        def reply(self, code, body, ctype="text/plain; charset=utf-8"):
            b = body.encode() if isinstance(body, str) else body
            self.send_response(code)
            for k, v in {"Content-Type": ctype, "Content-Length": str(len(b)), "Cache-Control": "no-store",
                         "X-Content-Type-Options": "nosniff", "Referrer-Policy": "no-referrer", "X-Robots-Tag": "noindex, nofollow",
                         "Content-Security-Policy": "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; "
                                                    "connect-src 'self'; img-src 'self' data:; base-uri 'none'; form-action 'none'"}.items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(b)

        def do_GET(self):
            ip = self.client_address[0].split("%")[0]
            try:
                a = ipaddress.ip_address(ip)
                if not (a.is_private or a.is_loopback or a.is_link_local):
                    return self.reply(404, "not found")  # never answer the public internet (e.g. an accidental port forward)
            except ValueError:
                return self.reply(404, "not found")
            now = time.time()
            _PFAILS[ip] = [t for t in _PFAILS.get(ip, []) if now - t < 60]
            if len(_PFAILS[ip]) >= 15:
                return self.reply(429, "slow down")
            m = re.fullmatch(r"/p/([A-Za-z0-9_-]{20,64})(/|/wall/?|/data\.json)", urlparse(self.path).path)
            tok = core.parent_token_peek().encode()
            need_long = str(core.config().get("links_long", "")).lower() in ("1", "true", "yes", "on")
            if not m or not tok or (need_long and len(tok) < 64) or not secrets.compare_digest(m.group(1).encode(), tok):
                _PFAILS[ip].append(now)
                return self.reply(404, "not found")
            if m.group(2) == "/data.json":
                return self.reply(200, parent_data(), "application/json")
            return self.reply(200, core.load_page(core.APPDIR / "parent.html"), "text/html; charset=utf-8")

        def do_other(self):
            self.reply(405, "read-only")
        do_POST = do_PUT = do_DELETE = do_PATCH = do_HEAD = do_OPTIONS = do_other
    return P


def parent_install():
    core.parent_token()
    return jobs.install_service("parent", ("parent", "serve", "--port", str(parent_port())))


def parent_remove():
    jobs.remove("parent")


def cmd_parent(a):
    if a.action == "serve":
        core.parent_token()
        try:
            srv = ThreadingHTTPServer((a.host, a.port or parent_port()), make_parent_handler())
        except OSError as e:
            sys.exit(f"Can't listen on port {a.port or parent_port()}: {e}")
        if sys.stdout.isatty():  # never write the secret link into a log file (background jobs have no terminal)
            print("Parent mode is running. Open one of these on your devices (anyone with the link can see the Parent view):")
            for u in parent_urls(a.port or parent_port()):
                print("  browser:", u["browser"]); print("  wall:   ", u["wall"])
        else:
            print("Parent mode is running (link not logged - see Settings or `parent status`).")
        sys.stdout.flush()
        srv.serve_forever()
    elif a.action == "rotate":
        core.parent_token(rotate=True)
        _PCACHE["t"] = 0
        print("New link created - the old one no longer works.")
        for u in parent_urls():
            print("  browser:", u["browser"]); print("  wall:   ", u["wall"])
    elif a.action == "install":
        ok, err = parent_install()
        print("Parent mode installed: it starts at login and keeps running." if ok else "Could not install the background job: " + err)
        for u in parent_urls():
            print("  browser:", u["browser"]); print("  wall:   ", u["wall"])
        print("Your firewall may ask once whether to allow incoming connections - choose Allow (private network).")
        print("Tip: the page shows what this Mac last synced, so also run `schedule install` to keep it fresh.")
    elif a.action == "remove":
        parent_remove(); print("Parent mode removed.")
    elif a.action == "sections":
        chosen = [x.strip() for x in (a.value or "").split(",") if x.strip()]
        bad = [x for x in chosen if x not in core.PARENT_SECTIONS]
        if bad or not chosen:
            sys.exit(f"Choose from: {', '.join(core.PARENT_SECTIONS)}")
        core.save_config({**core.config(), "parent_sections": ",".join(chosen)}); print("Parent view now shows:", ", ".join(chosen))
    else:
        st = parent_status()
        print(f"installed: {st['installed']}   running: {st['running']}   port: {st['port']}\nshows: {', '.join(st['sections'])}")
        for u in st["urls"]:
            print("  browser:", u["browser"]); print("  wall:   ", u["wall"])


def cmd_widget(a):
    if sys.platform != "darwin":
        sys.exit("The WidgetKit widget is macOS-only.")
    f = core.write_widget(core.state())
    print("Wrote", f) if f else print("Widget not installed yet: build and run the host app in widget/, add the widget "
                                       "from the desktop gallery, then run this again.")


def cmd_menubar(a):
    """macOS menu bar app, or the tray icon on Windows / Linux."""
    if a.action == "run":
        if sys.platform == "darwin":
            return __import__("menubar").run()
        return __import__("tray").run()
    if a.action == "install":
        ok, err = jobs.install_service("menubar", ("menubar", "run"))
        print("Menu bar / tray icon installed: it starts at login." if ok else "Could not install: " + err)
    elif a.action == "remove":
        jobs.remove("menubar")
        print("Menu bar / tray icon removed.")
    else:
        print("installed" if jobs.installed("menubar") else "not installed")


def cmd_schedule(a):
    plan = [("sync", a.time, ("sync", "--notify")), ("remind", a.remind_time, ("remind",))]
    for name, at, args in plan:
        if a.action == "install":
            h, m = (int(x) for x in at.split(":"))
            ok, err = jobs.install_daily(name, args, h, m)
            print(f"{args[0]} scheduled daily at {at}." if ok else f"Could not schedule {args[0]}: {err}")
        elif a.action == "remove":
            jobs.remove(name)
            print(f"{args[0]} schedule removed.")
        else:
            print(args[0], "installed" if jobs.installed(name) else "not installed")
    if a.action == "install":
        print("Log folder:", core.HOME)


# ------------------------------------------------------------------ GUI / web server
SYNC = {"running": False, "log": "", "ok": None}


LOGIN = {"running": False, "log": "", "ok": None}


def run_bg(state, cmd):
    if state["running"]:
        return
    state.update(running=True, log="", ok=None)

    def work():
        r = subprocess.run(cmd, capture_output=True, text=True, stdin=subprocess.DEVNULL)
        state.update(running=False, log=(r.stdout + r.stderr)[-3000:], ok=r.returncode == 0)
    threading.Thread(target=work, daemon=True).start()


def run_sync_bg():
    if str(core.config().get("sync_mode", "")) == "direct":
        run_bg(SYNC, core.self_cmd("sync", "--direct"))
    else:
        run_bg(SYNC, core.self_cmd("sync", "--notify", "--interactive"))


def school_search(q):
    req = Request("https://mobile.webuntis.com/ms/schoolquery2", method="POST",
                  data=json.dumps({"id": "1", "method": "searchSchool", "params": [{"search": q}], "jsonrpc": "2.0"}).encode(),
                  headers={"Content-Type": "application/json"})
    r = json.loads(urlopen(req, timeout=10).read())
    if r.get("error"):
        raise ValueError(r["error"].get("message", "search failed") + " - type more of the name")
    return [dict(name=x["displayName"], address=x.get("address", ""), server=x["server"], school=x["loginName"])
            for x in (r.get("result") or {}).get("schools", [])]


def make_handler(port=None):

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a): pass

        def send(self, code, body, ctype="application/json", extra=None):
            b = body.encode() if isinstance(body, str) else body
            self.send_response(code); self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(b)))
            for k, v in (("Cache-Control", "no-store"), ("X-Content-Type-Options", "nosniff"),
                         ("Referrer-Policy", "no-referrer"), ("X-Frame-Options", "DENY")):
                self.send_header(k, v)
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers(); self.wfile.write(b)

        def guard(self):  # blocks DNS-rebinding / cross-site requests to the local server
            port = self.server.server_address[1]
            if self.headers.get("Host") not in {f"localhost:{port}", f"127.0.0.1:{port}"}:
                self.send(403, "bad host", "text/plain"); return False
            return True

        def authed(self):
            """Only a window the app itself opened holds the session cookie - other programs on this Mac don't."""
            m = re.search(r"(?:^|;\s*)ssc_s=([A-Za-z0-9_-]+)", self.headers.get("Cookie", ""))
            return bool(m) and m.group(1) in self.server.sessions

        def do_GET(self):
            if not self.guard(): return
            u = urlparse(self.path)
            if u.path == "/":
                page = core.load_page(core.APPDIR / "index.html", prefs_api=True)
                k = parse_qs(u.query).get("k", [""])[0]
                if k and secrets.compare_digest(k.encode(), self.server.boot.encode()):
                    sid = secrets.token_urlsafe(24)
                    self.server.sessions.add(sid)
                    return self.send(200, page, "text/html; charset=utf-8", {"Set-Cookie": f"ssc_s={sid}; HttpOnly; SameSite=Strict; Path=/"})
                if self.authed():
                    return self.send(200, page, "text/html; charset=utf-8")
                return self.send(403, "Open Stundl Student Client from the app (or use the link it printed).", "text/plain")
            if not self.authed():
                return self.send(403, json.dumps({"error": "not signed in"}))
            if u.path == "/api/state":
                return self.send(200, json.dumps(core.state()))
            if u.path == "/api/search":
                return self.send(200, json.dumps(core.search(parse_qs(u.query).get("q", [""])[0])))
            if u.path == "/api/sync":
                return self.send(200, json.dumps(SYNC))
            if u.path == "/api/config":
                c = core.config()
                return self.send(200, json.dumps(dict(configured=bool(c.get("server") and c.get("school")) or c.get("sync_mode") == "direct", server=c.get("server"),
                                                      school=c.get("school"), browser=c.get("browser", "auto"), login=LOGIN,
                                                      version=SSC_VERSION)))
            if u.path == "/api/prefs":
                return self.send(200, json.dumps(core.clean_prefs(core.config().get("prefs"))))
            if u.path == "/api/update/check":
                import updater
                try:
                    return self.send(200, json.dumps(updater.check()))
                except updater.UpdateError as e:
                    return self.send(502, json.dumps({"error": str(e), "current": updater.ssc_version.VERSION, "flavor": updater.flavor()}))
            if u.path == "/api/watch":
                return self.send(200, json.dumps(watch_status()))
            if u.path == "/api/parent":
                try:
                    return self.send(200, json.dumps(parent_status()))
                except vault.VaultError as e:
                    return self.send(500, json.dumps({"error": str(e)}))
            if u.path == "/api/parent/qr":
                q = parse_qs(u.query)
                urls = parent_urls()
                try:
                    link = urls[int(q.get("i", ["0"])[0])][("wall" if q.get("kind", ["browser"])[0] == "wall" else "browser")]
                except (IndexError, ValueError):
                    return self.send(404, "no such link", "text/plain")
                import segno
                buf = io.BytesIO()
                segno.make(link, error="m").save(buf, kind="svg", scale=5, border=2, dark="#000000", light="#ffffff")
                return self.send(200, buf.getvalue(), "image/svg+xml")
            if u.path == "/api/browsers":
                return self.send(200, json.dumps(available_browsers()))
            if u.path == "/api/schools":
                try:
                    return self.send(200, json.dumps(school_search(parse_qs(u.query).get("q", [""])[0])))
                except Exception as e:
                    return self.send(502, json.dumps({"error": str(e)[:120]}))
            m = re.fullmatch(r"/export/(ics|homework|exams|grades|absences)", u.path)
            if m:
                k = m.group(1)
                data, ct, fn = ((core.export_ics(), "text/calendar", "ssc.ics") if k == "ics"
                                else (core.export_csv(k), "text/csv", f"{k}.csv"))
                return self.send(200, data, ct + "; charset=utf-8", {"Content-Disposition": f'attachment; filename="{fn}"'})
            self.send(404, "not found", "text/plain")

        def do_POST(self):
            if not self.guard(): return
            if self.headers.get("Content-Type", "").split(";")[0] != "application/json":
                return self.send(415, "json only", "text/plain")
            if not self.authed():
                return self.send(403, json.dumps({"error": "not signed in"}))
            n = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(n) or b"{}")
            path = urlparse(self.path).path
            con = core.db()
            if path == "/api/done":
                core.set_done(con, body["id"], body["done"])
            elif path == "/api/seen":
                con.execute("update changes set seen=1"); con.commit()
            elif path == "/api/sync":
                run_sync_bg()
            elif path in ("/api/homework/dismiss", "/api/homework/restore"):
                core.hw_set_dismiss(con, str(body.get("id", "")), "dismissed" if path.endswith("dismiss") else "restored")
            elif path == "/api/homework/settings":
                try:
                    soon, miss = int(body["soon_days"]), int(body["dismiss_missed_days"])
                    if not (0 <= soon <= 14 and -1 <= miss <= 365):
                        raise ValueError
                except (KeyError, TypeError, ValueError):
                    return self.send(400, json.dumps({"error": "'due soon' must be 0-14 days and the missed-dismiss delay -1 to 365"}))
                core.save_config({**core.config(), "hw_soon_days": soon, "hw_dismiss_missed_days": miss,
                                  "hw_dismiss_done": bool(body.get("dismiss_done"))})
            elif path == "/api/plan":
                try:
                    f = {k: body[k] for k in ("date", "start", "minutes", "subject", "title", "notes", "done") if k in body}
                    if body.get("id"):
                        core.plan_update(con, body["id"], **f)
                    else:
                        core.plan_add(con, **f)
                except (ValueError, TypeError) as e:
                    return self.send(400, json.dumps({"error": str(e)}))
            elif path == "/api/plan/delete":
                core.plan_delete(con, body.get("id", 0))
            elif path == "/api/prefs":
                core.save_config({**core.config(), "prefs": core.clean_prefs(body)})
            elif path == "/api/update/download":
                import updater
                if updater.flavor() == "server":
                    return self.send(400, json.dumps({"error": "this is an SSC Server: use Install update"}))
                try:
                    f, v = updater.download_app(Path.home() / "Downloads")
                    return self.send(200, json.dumps({"path": str(f), "version": v}))
                except updater.UpdateError as e:
                    return self.send(502, json.dumps({"error": str(e)}))
            elif path == "/api/update/install":
                return self.send(400, json.dumps({"error": "installing updates is only available on an SSC Server"}))
            elif path == "/api/watch/scan":
                try:
                    import asyncio, pinetime
                    return self.send(200, json.dumps({"found": [{"address": a, "name": n} for a, n in asyncio.run(pinetime.scan())]}))
                except Exception as e:
                    return self.send(500, json.dumps({"error": str(e)[:200]}))
            elif path == "/api/watch/pair":
                addr = str(body.get("address", "")).strip()
                if not re.fullmatch(r"([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}|[0-9A-Fa-f]{8}-([0-9A-Fa-f]{4}-){3}[0-9A-Fa-f]{12}", addr):
                    return self.send(400, json.dumps({"error": "not a Bluetooth address"}))
                import pinetime
                pinetime.set_address(addr)
            elif path == "/api/watch/forget":
                import pinetime
                pinetime.set_address(None)
            elif path in ("/api/watch/sync", "/api/watch/test"):
                import pinetime
                msg = pinetime.sync(test=path.endswith("test"))
                return self.send(200, json.dumps(dict(watch_status(), message=msg)))
            elif path == "/api/parent/install":
                ok, err = parent_install()
                return self.send(200 if ok else 500, json.dumps(parent_status() if ok else {"error": err[:200]}))
            elif path == "/api/parent/remove":
                parent_remove()
            elif path == "/api/parent/rotate":
                core.parent_token(rotate=True); _PCACHE["t"] = 0
            elif path == "/api/parent/sections":
                chosen = [x for x in core.PARENT_SECTIONS if x in [str(v) for v in body.get("sections", [])]]
                if not chosen:
                    return self.send(400, json.dumps({"error": "pick at least one section"}))
                core.save_config({**core.config(), "parent_sections": ",".join(chosen)}); _PCACHE["t"] = 0
            elif path == "/api/browser":
                if str(body.get("browser")) not in {b["id"] for b in available_browsers()}:
                    return self.send(400, json.dumps({"error": "unknown browser"}))
                core.save_config({**core.config(), "browser": body["browser"]})
            elif path == "/api/login":
                server, school, br = str(body.get("server", "")), str(body.get("school", "")), str(body.get("browser", "auto"))
                if not (re.fullmatch(r"[a-z0-9-]+(\.[a-z0-9-]+)*\.webuntis\.com", server) and re.fullmatch(r"[A-Za-z0-9_.-]+", school)
                        and br in {b["id"] for b in available_browsers()}):
                    return self.send(400, json.dumps({"error": "invalid server, school or browser"}))
                run_bg(LOGIN, core.self_cmd("login", "--server", server, "--school", school, "--browser", br))
            else:
                return self.send(404, "not found", "text/plain")
            self.send(200, "{}")
    return H


def start_server(port):
    try:
        srv = ThreadingHTTPServer(("127.0.0.1", port), make_handler())
    except OSError:  # preferred port busy (e.g. another window): use any free one
        srv = ThreadingHTTPServer(("127.0.0.1", 0), make_handler())
    srv.boot, srv.sessions = secrets.token_urlsafe(18), set()   # per-run secret: only the window we open knows it
    return srv


def gui_url(srv, extra=""):
    return f"http://localhost:{srv.server_address[1]}/?k={srv.boot}{extra}"


def launch_prep(a):
    """On launch: refresh the offline file from stored data, then sync in the background."""
    try:
        core.write_snapshot(core.APPDIR / "index.html")
    except Exception as e:
        print("snapshot failed:", e)
    if not a.no_sync and core.config().get("server"):
        run_sync_bg()


def cmd_offline(a):
    f = core.write_snapshot(core.APPDIR / "index.html")
    print("Offline viewer:", f)
    webbrowser.open(f.as_uri())


def cmd_serve(a):
    srv = start_server(a.port)
    launch_prep(a)
    print(f"{core.APP_NAME}: open {gui_url(srv)}")
    srv.serve_forever()


def cmd_gui(a):
    srv = start_server(a.port)
    launch_prep(a)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = gui_url(srv)
    try:
        import webview
        try:  # macOS: transparent window with system vibrancy behind the glass-styled page
            if sys.platform != "darwin":
                raise TypeError
            webview.create_window(core.APP_NAME, url + "&native", width=1100, height=760, transparent=True, vibrancy=True)
        except TypeError:
            webview.create_window(core.APP_NAME, url, width=1100, height=760)
        webview.start()
    except ImportError:
        print("pywebview not installed (pip install pywebview) - opening in your browser instead. Ctrl+C to quit.")
        webbrowser.open(url)
        try:
            while True:
                time.sleep(3600)
        except KeyboardInterrupt:
            pass


def main():
    core.migrate_legacy_home()   # one time: move the data folder + Keychain items from the old name
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    sp = ap.add_subparsers(required=True)
    x = sp.add_parser("login"); x.add_argument("--server", required=True); x.add_argument("--school", required=True)
    x.add_argument("--browser", help="auto, chrome, msedge, brave, vivaldi, arc, opera, chromium, firefox, webkit")
    x.set_defaults(f=cmd_login)
    x = sp.add_parser("sync")
    x.add_argument("--full", action="store_true", help="download the timetable for the whole school year")
    x.add_argument("--notify", action="store_true", help="macOS notification on changes / expired login")
    x.add_argument("--interactive", action="store_true", help="open a browser window if re-login fails")
    x.add_argument("--direct", action="store_true", help="headless: log in with the Untis Mobile key (no browser) - used by SSC Server")
    x.set_defaults(f=cmd_sync)
    sp.add_parser("tui").set_defaults(f=lambda a: __import__("tui").run())
    x = sp.add_parser("pinetime", help="sync with a PineTime watch (clock + notifications)")
    x.add_argument("action", choices=["scan", "pair", "sync", "test", "forget"], nargs="?", default="sync")
    x.add_argument("address", nargs="?")
    x.set_defaults(f=cmd_pinetime)
    x = sp.add_parser("update", help="check GitHub for a newer SSC (and download it)")
    x.add_argument("--download", action="store_true", help="download the release file for this system (checksum verified)"); x.set_defaults(f=cmd_update)
    x = sp.add_parser("imgen", help="make a printable access card (logo, address, user, password, notes)")
    x.add_argument("rest", nargs=argparse.REMAINDER)
    x.set_defaults(f=lambda a: sys.exit(__import__("ssc_imgen").main(a.rest)))
    sp.add_parser("server", help="run SSC Server (web login, roles, Parent view)").set_defaults(f=cmd_server)
    for name, fn in (("serve", cmd_serve), ("gui", cmd_gui)):
        x = sp.add_parser(name); x.add_argument("--port", type=int, default=8765)
        x.add_argument("--no-sync", action="store_true", help="don't sync on launch"); x.set_defaults(f=fn)
    x = sp.add_parser("changes"); x.add_argument("-n", type=int, default=30); x.set_defaults(f=cmd_changes)
    x = sp.add_parser("search"); x.add_argument("text", nargs="+"); x.set_defaults(f=cmd_search)
    sp.add_parser("offline").set_defaults(f=cmd_offline)
    sp.add_parser("stats").set_defaults(f=cmd_stats)
    x = sp.add_parser("done"); x.add_argument("ids", nargs="*"); x.set_defaults(f=cmd_done)
    x = sp.add_parser("homework", help="homework by status (done / due soon / pending / missed) with automatic dismissal")
    x.add_argument("action", nargs="?", default="list", choices=["list", "dismiss", "restore"]); x.add_argument("ids", nargs="*")
    x.add_argument("--all", action="store_true", help="include dismissed homework"); x.set_defaults(f=cmd_homework)
    x = sp.add_parser("export"); x.add_argument("kind", choices=["ics", "homework", "exams", "grades", "absences"])
    x.add_argument("-o", "--output"); x.set_defaults(f=cmd_export)
    x = sp.add_parser("schedule"); x.add_argument("action", choices=["install", "remove", "status"])
    x.add_argument("--time", default="06:45", help="daily sync time")
    x.add_argument("--remind-time", default="17:30", help="daily reminder time"); x.set_defaults(f=cmd_schedule)
    x = sp.add_parser("untis", help="the Untis Mobile key for headless syncing (SSC Server)")
    x.add_argument("action", choices=["set", "test", "clear", "status", "raw"]); x.add_argument("value", nargs="?", help="the untis://setschool?... link (or, for raw: getHomeWork2017 / getExams2017 / getTimetable2017)")
    x.add_argument("--server", help="e.g. xyz.webuntis.com"); x.add_argument("--school", help="the school's login name (?school=... in the WebUntis address)")
    x.add_argument("--user"); x.add_argument("--key", help="(better leave it out: it is then asked for without showing it)"); x.set_defaults(f=cmd_untis)
    x = sp.add_parser("plan", help="your study plan (shown in Parent mode)")
    x.add_argument("action", nargs="?", default="list", choices=["list", "add", "done", "undone", "edit", "rm"]); x.add_argument("arg", nargs="*")
    x.add_argument("--date"); x.add_argument("--time"); x.add_argument("--minutes", type=int); x.add_argument("--subject"); x.add_argument("--notes")
    x.set_defaults(f=cmd_plan)
    x = sp.add_parser("parent", help="read-only website for wall displays / other devices (no credentials shared)")
    x.add_argument("action", choices=["serve", "status", "rotate", "install", "remove", "sections"]); x.add_argument("value", nargs="?")
    x.add_argument("--port", type=int); x.add_argument("--host", default="0.0.0.0"); x.set_defaults(f=cmd_parent)
    sp.add_parser("remind").set_defaults(f=cmd_remind)
    x = sp.add_parser("menubar"); x.add_argument("action", nargs="?", default="run",
                                                  choices=["run", "install", "remove", "status"]); x.set_defaults(f=cmd_menubar)
    x = sp.add_parser("config"); x.add_argument("action", choices=["show", "set"]); x.add_argument("key", nargs="?")
    x.add_argument("value", nargs="?"); x.set_defaults(f=cmd_config)
    x = sp.add_parser("grade-calc"); x.add_argument("subject"); x.add_argument("--add", default="")
    x.add_argument("--target", type=float); x.set_defaults(f=cmd_gradecalc)
    sp.add_parser("widget").set_defaults(f=cmd_widget)
    if len(sys.argv) == 1 or sys.argv[1].startswith("-psn"):  # double-clicked app: GUI - or the terminal view on a Linux box without a display
        no_display = sys.platform.startswith("linux") and not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))
        sys.argv = [sys.argv[0], "tui" if no_display else "gui"]
    if len(sys.argv) > 1 and sys.argv[1] == "imgen":          # its own option parser (argparse can't pass leading --options on)
        sys.exit(__import__("ssc_imgen").main(sys.argv[2:]))
    a = ap.parse_args()
    try:
        a.f(a)
    except vault.VaultError as e:
        sys.exit(f"{e}\nSSC keeps its secrets in the macOS Keychain and never in plain files. Unlock or repair your login Keychain "
                 "(Keychain Access app), then try again.")


if __name__ == "__main__":
    main()
