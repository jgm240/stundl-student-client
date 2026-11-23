"""Stundl Student Client (SSC) core: storage, normalisation, change detection, search, stats, export, scheduling.

Shared by the CLI, the TUI and the GUI. Everything here works on the local database only.
"""
import csv, hashlib, html, io, json, os, secrets, shutil, urllib.parse, plistlib, re, sqlite3, subprocess, sys, time
from datetime import date, datetime, timedelta
from pathlib import Path

def data_dir():
    """macOS ~/.ssc | Windows %LOCALAPPDATA%\\SSC | Linux $XDG_DATA_HOME/ssc (default ~/.local/share/ssc).
    SSC_DATA_DIR overrides it (tests); the real home must stay untouched because the Keychain lives there."""
    if os.environ.get("SSC_DATA_DIR"):
        return Path(os.environ["SSC_DATA_DIR"])
    if sys.platform == "darwin":
        return Path.home() / ".ssc"
    if sys.platform.startswith("win"):
        return Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local") / "SSC"
    return Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share") / "ssc"


def playwright_cache():
    """Where Playwright keeps downloaded browsers (the packaged app must look here, not inside its bundle)."""
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Caches" / "ms-playwright"
    if sys.platform.startswith("win"):
        return Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local") / "ms-playwright"
    return Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache") / "ms-playwright"


HOME = data_dir()
DB = HOME / "data.db"
APP_NAME = "Stundl Student Client"          # official name
APP_SHORT = "SSC"
APP_NAME_EN = "SSC Student Client"           # what English speakers can call it
FROZEN = bool(getattr(sys, "frozen", False))                      # running inside the packaged .app
APPDIR = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))  # where index.html lives


def self_cmd(*args):
    """Command line that runs this app again (works from source and from the packaged .app)."""
    if FROZEN:
        return [sys.executable, *args]
    return [sys.executable, str(Path(__file__).resolve().parent / "ssc.py"), *args]


# ------------------------------------------------------------------ storage
# ------------------------------------------------------------------ profiles (several children / schools on one server)
import re as _re, threading as _threading
_PROFILE = _threading.local()
PROFILE_RE = _re.compile(r"^[a-z0-9][a-z0-9-]{0,19}$")
MAIN = "main"                                   # the profile every installation has (its data lives where it always did)
HOOKS = []                                      # functions(changes: [(kind, text)], profile_id) run after changes were recorded


def profile_id():
    """The profile this thread works on (a web request sets it; a sync subprocess gets it from SSC_PROFILE)."""
    return getattr(_PROFILE, "id", None) or os.environ.get("SSC_PROFILE") or MAIN


def set_profile(pid):
    _PROFILE.id = pid if pid and pid != MAIN and PROFILE_RE.match(str(pid)) else None


def vkey(name):
    """Name of a secret in the vault for the current profile (the main profile keeps the old names)."""
    pid = profile_id()
    return name if pid == MAIN else f"{name}@{pid}"


def profile_list():
    extra = config().get("profiles") or []
    out = [{"id": MAIN, "label": str(config().get("main_label") or "Main")}]
    out += [{"id": str(p["id"]), "label": str(p.get("label") or p["id"])} for p in extra if isinstance(p, dict) and PROFILE_RE.match(str(p.get("id", "")))]
    return out


def profile_ids():
    return [p["id"] for p in profile_list()]


def profile_add(pid, label):
    pid = str(pid or "").strip().lower()
    if not PROFILE_RE.match(pid) or pid == MAIN:
        raise ValueError("profile id: letters, digits and '-' (max 20), not 'main'")
    label = re.sub(r"[\x00-\x1f<>]", "", str(label or "")).strip()[:40] or pid
    cfg = config()
    if pid in profile_ids():
        raise ValueError("that profile exists already")
    if len(cfg.get("profiles") or []) >= 9:
        raise ValueError("at most 10 profiles")
    cfg["profiles"] = list(cfg.get("profiles") or []) + [{"id": pid, "label": label}]
    save_config(cfg)
    return pid


def profile_rename(pid, label):
    label = re.sub(r"[\x00-\x1f<>]", "", str(label or "")).strip()[:40]
    if not label:
        raise ValueError("a name is needed")
    cfg = config()
    if pid == MAIN:
        cfg["main_label"] = label
    elif pid in profile_ids():
        cfg["profiles"] = [dict(p, label=label) if p["id"] == pid else p for p in cfg.get("profiles") or []]
    else:
        raise ValueError("no such profile")
    save_config(cfg)


def profile_remove(pid):
    if pid == MAIN or pid not in profile_ids():
        raise ValueError("that profile can't be removed")
    cfg = config()
    cfg["profiles"] = [p for p in cfg.get("profiles") or [] if p["id"] != pid]
    save_config(cfg)
    shutil.rmtree(HOME / "profiles" / pid, ignore_errors=True)


def db_path():
    pid = profile_id()
    return DB if pid == MAIN else HOME / "profiles" / pid / "data.db"


def db():
    path = db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(path)
    c.executescript("""
        create table if not exists store(key text primary key, fetched_at real, body text);
        create table if not exists changes(id integer primary key autoincrement, at real, kind text,
                                           text text, seen integer default 0);
        create table if not exists done(hw_id text primary key, done integer, at real);
        create table if not exists notified(key text primary key, at real);
        create table if not exists plan_items(id integer primary key autoincrement, date text, start text, minutes integer,
                                              subject text, title text, notes text, done integer default 0);
        create table if not exists hw_dismiss(hw_id text primary key, state text, at real);
    """)
    scrub_teams(c)
    migrate_secrets()
    harden_permissions()
    return c


def harden_permissions():
    """Only you can read the data folder and its files."""
    try:
        HOME.chmod(0o700)
        files = [DB] + [HOME / n for n in ("config.json", "snapshot.json", "offline.html", "mobile.html", "sync.log", "parent.log", "menubar.log")]
        for f in files:
            if f.exists():
                f.chmod(0o600)
    except OSError:
        pass


def shred(path):
    """Overwrite, then delete (best effort - on SSDs/APFS an overwrite can't be guaranteed)."""
    try:
        n = path.stat().st_size
        with open(path, "r+b") as f:
            f.write(b"\0" * n)
            f.flush()
            os.fsync(f.fileno())
    except OSError:
        pass
    path.unlink(missing_ok=True)


def migrate_secrets():
    """One time: move secrets older versions kept in plain files into the Keychain, then destroy the files."""
    marker = HOME / ".secrets-migrated"
    if marker.exists():
        return
    try:
        import vault
        cfg = config()
        if cfg.get("parent_token"):
            vault.set("parent-token", cfg.pop("parent_token"))
            save_config(cfg)
        ck = HOME / "cookies.json"
        if ck.exists():
            vault.set("webuntis-cookies", ck.read_text())
            shred(ck)
        for log in ("parent.log", "menubar.log", "sync.log"):  # old logs may contain the Parent link
            (HOME / log).unlink(missing_ok=True)
        marker.write_text("1")
    except Exception:
        pass  # keep the old files until the Keychain works; tried again on the next start


def wipe_browser_profiles():
    """Don't leave a signed-in browser profile (SSO cookies) on disk - the WebUntis session itself is in the Keychain.
    Opt out with `config set keep_sso_session true` (convenience for unattended re-login)."""
    if str(config().get("keep_sso_session", "")).lower() in ("1", "true", "yes", "on"):
        return
    shutil.rmtree(HOME / "browser-profile", ignore_errors=True)


def migrate_legacy_home():
    """One time, macOS installs from before the rename: move ~/.offlineuntis to ~/.ssc (the Keychain items are tied to the
    folder path, so they are moved too) and remove the old background jobs (re-create them with `parent install`,
    `menubar install`, `schedule install`)."""
    if sys.platform != "darwin" or os.environ.get("SSC_DATA_DIR"):
        return
    old = Path.home() / ".offlineuntis"
    if old.exists() and not HOME.exists():
        shutil.move(str(old), str(HOME))
        try:
            import vault
            vault.migrate_from(old)
        except Exception:
            pass  # secrets stay readable under the old name; the app will ask you to sign in again if needed
    import jobs
    jobs.remove_legacy()


def scrub_teams(con):
    """Microsoft Teams support was removed. Once, delete everything it left on this computer."""
    marker = HOME / ".teams-scrubbed"
    if marker.exists():
        return
    for t in ("contacts", "groups", "group_members", "outbox"):
        con.execute(f"drop table if exists {t}")
    con.execute("delete from store where key like 'teams:%'")
    con.execute("delete from changes where text like 'Teams%'")
    con.commit()
    for f in ("ms_token.json", "teams_web.json"):
        (HOME / f).unlink(missing_ok=True)
    for d in (HOME / "browser-profile").glob("*-teams"):
        shutil.rmtree(d, ignore_errors=True)
    shutil.rmtree(HOME / "teams-debug", ignore_errors=True)
    cfg = config()
    stale = [k for k in cfg if k.startswith(("ms_", "teams_"))]
    if stale:
        for k in stale:
            del cfg[k]
        save_config(cfg)
    marker.write_text("1")


def config():
    try:
        return json.loads((HOME / "config.json").read_text())
    except Exception:
        return {}


def save_config(cfg):
    HOME.mkdir(parents=True, exist_ok=True)
    f = HOME / "config.json"
    f.write_text(json.dumps(cfg, indent=1))
    f.chmod(0o600)  # holds the Parent mode link secret


def load(con=None):
    """-> {key: (fetched_at, parsed_json)}"""
    con = con or db()
    return {k: (t, json.loads(b)) for k, t, b in con.execute("select key, fetched_at, body from store")}


def store_save(con, key, data, changes):
    """Store a fetched document; if an older version exists, add the human-readable differences to `changes`."""
    old = con.execute("select body from store where key=?", (key,)).fetchone()
    if old:
        changes.extend(diff(key, json.loads(old[0]), data))
    con.execute("insert or replace into store values(?,?,?)", (key, time.time(), json.dumps(data)))
    con.commit()


def record_changes(con, changes):
    now = time.time()
    con.executemany("insert into changes(at, kind, text) values(?,?,?)", [(now, k, t) for k, t in changes])
    con.commit()
    if changes:
        for h in list(HOOKS):
            try:
                h(list(changes), profile_id())
            except Exception:
                pass                                                   # a mail problem never breaks a sync


def set_done(con, hw_id, value):
    con.execute("insert or replace into done values(?,?,?)", (str(hw_id), int(bool(value)), time.time()))
    con.commit()


def done_ids(con):
    return {r[0] for r in con.execute("select hw_id from done where done=1")}


# ------------------------------------------------------------------ normalisation
def _unwrap(d):
    return (d.get("data", d) if isinstance(d, dict) else d) or {}


# ---- per-user interface preferences (language, mode, theme palette, accent) - must match ui.js
PREF_LANGS = ("en", "de", "fr", "es", "it", "pt", "nl", "pl")
PREF_MODES = ("system", "light", "dark")
PREF_PALETTES = ("default", "paper", "ocean", "forest", "midnight", "rose", "graphite")
PREF_ACCENTS = ("indigo", "blue", "teal", "green", "orange", "pink", "purple", "slate")
PREF_UIS = ("beta", "classic")            # the redesigned interface, or the one from before the redesign


def clean_prefs(d):
    """Only known keys with known values survive: preferences are stored per user and echoed back into pages."""
    d = d if isinstance(d, dict) else {}
    out = {}
    for key, allowed in (("lang", PREF_LANGS), ("theme", PREF_MODES), ("palette", PREF_PALETTES), ("accent", PREF_ACCENTS), ("ui", PREF_UIS)):
        if d.get(key) in allowed:
            out[key] = d[key]
    return out


UI_MARK = "<!--SSC-UI-->"
SWITCH_BACK = ("<button id='ssc-ui-switch' type='button' onclick=\"SSCUI.set({ui:'beta'})\" style=\"position:fixed;left:12px;bottom:12px;z-index:99;font:13px system-ui,"
               "sans-serif;padding:7px 13px;border-radius:99px;border:1px solid rgba(128,128,128,.45);background:rgba(255,255,255,.9);color:#111;cursor:pointer\">"
               "New interface (beta)</button>")


def ui_choice(cookie_header):
    """'classic' if the browser asked for the old interface (cookie ssc_ui=classic), else 'beta'."""
    m = re.search(r"(?:^|;\s*)ssc_ui=(classic|beta)\b", str(cookie_header or ""))
    return m.group(1) if m else "beta"


def _runtime_js(path):
    for d in (path.parent, path.parent.parent, APPDIR):
        f = d / "ui.js"
        if f.exists():
            return f.read_text(encoding="utf-8")
    return None


def load_page(path, prefs_api=False, classic=False):
    """An HTML page with the shared front-end runtime (ui.js: languages, themes, accent colours) inlined at <!--SSC-UI-->.
    prefs_api=True lets the page save the user's preferences through /api/prefs (only pages served by an app server, never
    the read-only Parent link server). classic=True serves the old interface (classic/<name>) when it exists: it gets the
    language runtime only (no themes - its own design) and a button to go back to the new one."""
    path = Path(path)
    old = None
    if classic:
        for d in (path.parent, APPDIR):
            if (d / "classic" / path.name).exists():
                old = d / "classic" / path.name
                break
    html = (old or path).read_text(encoding="utf-8")
    js = _runtime_js(path)
    esc = lambda t: t.replace("</script", "<\\/script")
    block = ("<script>window.SSC_PREFS_API=true;</script>" if prefs_api else "") + (("<script>" + esc(js) + "</script>") if js else "")
    if old:
        block = "<script>window.SSC_CLASSIC=true;</script>" + block
        html = html.replace("<head>", "<head>" + block, 1).replace("</body>", SWITCH_BACK + "</body>", 1)
    elif UI_MARK in html:
        html = html.replace(UI_MARK, block, 1)
    return html


def ymd(v):
    s = str(v or "")
    if s.isdigit() and len(s) == 8:
        return f"{s[:4]}-{s[4:6]}-{s[6:]}"
    m = re.match(r"^(\d{4}-\d{2}-\d{2})[T ]", s)        # "2026-11-23T08:00:00" -> the date part
    return m.group(1) if m else s


def hhmm(v):
    s = str(v or "")
    if s.isdigit():
        s = s.zfill(4)
        return f"{s[:2]}:{s[2:]}"
    return s


def _plain(t):
    """Text from a field that may contain HTML."""
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", str(t or "")))).strip()


def _names(e):
    """Short names by type (SUBJECT/TEACHER/ROOM/CLASS...), the long names, and what a substitution changed."""
    short, long_, changes = {}, {}, []
    for i in range(1, 6):
        for p in e.get("position%d" % i) or []:
            cur, old = p.get("current") or {}, p.get("removed") or {}
            c = cur or old
            kind = (c.get("type") or "OTHER").upper()
            n = c.get("shortName") or c.get("displayName") or c.get("longName")
            if n:
                short.setdefault(kind, []).append(n)
                ln = c.get("longName") or c.get("displayName") or n
                long_.setdefault(kind, []).append(ln)
            if cur and old:
                a_, b_ = old.get("shortName") or old.get("displayName"), cur.get("shortName") or cur.get("displayName")
                if a_ and b_ and a_ != b_:
                    changes.append(f"{kind.title()}: {a_} \u2192 {b_}")
            elif old and not cur:
                changes.append(f"{kind.title()} {old.get('shortName') or old.get('displayName') or ''} removed".strip())
    return short, long_, changes


def tt_entries(data):
    res = []
    for d in (data or {}).get("days", []):
        for e in d.get("gridEntries", []):
            n, ln, changes = _names(e)
            if not any(n.get(t) for t in ("SUBJECT", "TEACHER", "ROOM")):  # untyped: guess by position
                g = lambda i: ", ".join(filter(None, ((p.get("current") or p.get("removed") or {}).get("shortName")
                                                      for p in e.get("position%d" % i) or [])))
                n = {"SUBJECT": [g(1)], "TEACHER": [g(2)], "ROOM": [g(3)]}
                ln = dict(n)
            st = e.get("status") or ""
            j = lambda m, k: ", ".join(m.get(k, []))
            topic, note, subst = _plain(e.get("lessonText")), _plain(e.get("notesAll")), _plain(e.get("substitutionText"))
            res.append(dict(
                date=d.get("date"), start=e["duration"]["start"], end=e["duration"]["end"],
                subject=j(n, "SUBJECT"), teacher=j(n, "TEACHER"), room=j(n, "ROOM"), status=st, cancelled="CANCEL" in st.upper(),
                info=_plain(e.get("lessonInfo")) or subst or note,
                # details for the lesson view
                subject_long=j(ln, "SUBJECT"), teacher_long=j(ln, "TEACHER"), room_long=j(ln, "ROOM"),
                group=_plain(e.get("studentGroup")) or j(n, "CLASS"), topic=topic, note=note, substitution=subst,
                changes=changes, kind=str(e.get("type") or ""), color=str(e.get("color") or "")))
    res.sort(key=lambda x: x["start"])
    return res


def _source_status(x):
    """A status WebUntis itself reports for a homework entry (if the payload has one) -> done|missed|soon|pending|None."""
    for k in ("status", "homeworkStatus", "state", "homeworkState"):
        v = x.get(k)
        if isinstance(v, str):
            v = v.lower()
            if any(w in v for w in ("complet", "done", "erledigt")):
                return "done"
            if any(w in v for w in ("miss", "overdue", "vers\u00e4umt", "\u00fcberf\u00e4llig")):
                return "missed"
            if "soon" in v or "upcoming" in v or "bald" in v:
                return "soon"
            if "pend" in v or "open" in v or "offen" in v:
                return "pending"
    return None


def homework_items(data):
    h = _unwrap(data)
    subj = {l.get("id"): l.get("subject") for l in h.get("lessons", [])}
    recs = {r.get("homeworkId"): r for r in h.get("records", [])}
    base = h.get("homeworks") or h.get("records") or []
    out = []
    for x in base:
        x = {**recs.get(x.get("id"), {}), **x}
        text = x.get("text", "")
        hid = x.get("id") or x.get("homeworkId") or hashlib.md5(f"{x.get('lessonId')}{x.get('dueDate')}{text}".encode()).hexdigest()[:10]
        out.append(dict(id=str(hid), subject=subj.get(x.get("lessonId"), "") or "", due=ymd(x.get("dueDate")),
                        date=ymd(x.get("date")), text=text, remark=x.get("remark") or "",
                        completed=bool(x.get("completed")), source_status=_source_status(x)))
    out.sort(key=lambda x: x["due"], reverse=True)
    return out


def exam_items(data):
    out = []
    for e in _unwrap(data).get("exams", []):
        out.append(dict(id=str(e.get("id") or f"{e.get('examDate')}{e.get('subject')}"), date=ymd(e.get("examDate")),
                        start=hhmm(e.get("startTime")), end=hhmm(e.get("endTime")),
                        subject=str(e.get("subject") or e.get("name") or ""), text=e.get("text") or e.get("name") or ""))
    out.sort(key=lambda x: x["date"])
    return out


def absence_items(data):
    out = []
    for a in _unwrap(data).get("absences", []):
        out.append(dict(start=ymd(a.get("startDate")), end=ymd(a.get("endDate")), start_time=hhmm(a.get("startTime")),
                        end_time=hhmm(a.get("endTime")), excused=bool(a.get("isExcused")),
                        reason=a.get("reason") or (a.get("excuse") or {}).get("text") or ""))
    out.sort(key=lambda x: x["start"], reverse=True)
    return out


def message_items(data):
    d = data
    l = (d or {}).get("incomingMessages") or (d or {}).get("messages") if isinstance(d, dict) else d
    out = []
    for m in l or []:
        out.append(dict(id=str(m.get("id") or m.get("subject")), subject=m.get("subject") or "",
                        sender=(m.get("sender") or {}).get("displayName", ""),
                        sent=str(m.get("sentDateTime") or "")[:16], preview=m.get("contentPreview") or ""))
    return out


def grade_items(data):
    """Defensive walk: finds {lesson:{subject}, grades:[...]} blocks wherever they are."""
    out = []

    def walk(o):
        if isinstance(o, dict):
            if "grades" in o and isinstance(o["grades"], list):
                les = o.get("lesson") or {}
                subj = les.get("subject") or les.get("name") or o.get("subject") or ""
                for g in o["grades"]:
                    m = g.get("mark") if isinstance(g.get("mark"), dict) else g
                    val = m.get("markDisplayValue", m.get("markValue", m.get("value")))
                    try:
                        num = float(val)
                    except (TypeError, ValueError):
                        num = None
                    out.append(dict(subject=str(subj), value=val, num=num, date=ymd(g.get("date")),
                                    text=g.get("text") or g.get("examType") or ""))
                return
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)

    walk(data)
    return out


# ------------------------------------------------------------------ change detection
def _monday_iso():
    t = date.today()
    return (t - timedelta(days=t.weekday())).isoformat()


def _diff_tt(old, new):
    k = lambda e: (e["date"], e["start"], e["subject"])
    om, nm = {k(e): e for e in old}, {k(e): e for e in new}
    out = []
    for key, e in nm.items():
        o = om.get(key)
        label = f"{e['subject'] or 'Lesson'} {e['date']} {e['start'][11:16]}"
        if not o:
            out.append(("timetable", f"New lesson: {label}"))
            continue
        if e["cancelled"] and not o["cancelled"]:
            out.append(("timetable", f"Cancelled: {label}"))
        elif o["cancelled"] and not e["cancelled"]:
            out.append(("timetable", f"Reinstated: {label}"))
        if e["room"] != o["room"]:
            out.append(("timetable", f"Room change: {label}: {o['room'] or '-'} -> {e['room'] or '-'}"))
        if e["teacher"] != o["teacher"]:
            out.append(("timetable", f"Substitution: {label}: {o['teacher'] or '-'} -> {e['teacher'] or '-'}"))
        if e.get("topic") and e["topic"] != o.get("topic"):
            out.append(("timetable", f"Lesson topic: {label}: {e['topic'][:80]}"))
        if e["info"] and e["info"] != o["info"]:
            out.append(("timetable", f"Note: {label}: {e['info']}"))
    for key, o in om.items():
        if key not in nm and not o["cancelled"]:
            out.append(("timetable", f"Lesson removed: {o['subject'] or 'Lesson'} {o['date']} {o['start'][11:16]}"))
    return out


def diff(key, old, new):
    """Human-readable changes between two stored versions of `key`. -> [(kind, text)]"""
    if key.startswith("timetable:"):
        return [] if key[10:] < _monday_iso() else _diff_tt(tt_entries(old), tt_entries(new))
    if key == "homework":
        seen = {x["id"] for x in homework_items(old)}
        return [("homework", f"New homework: {x['subject']} - {x['text']} (due {x['due']})")
                for x in homework_items(new) if x["id"] not in seen]
    if key == "exams":
        seen = {x["id"] for x in exam_items(old)}
        return [("exam", f"New exam: {x['subject']} on {x['date']} {x['text']}") for x in exam_items(new) if x["id"] not in seen]
    if key == "messages":
        seen = {x["id"] for x in message_items(old)}
        return [("message", f"New message: {x['subject']} ({x['sender']})") for x in message_items(new) if x["id"] not in seen]
    if key == "grades":
        sig = lambda g: (g["subject"], g["date"], str(g["value"]))
        seen = {sig(g) for g in grade_items(old)}
        return [("grade", f"New grade: {g['subject']} {g['value']} {g['text']}") for g in grade_items(new) if sig(g) not in seen]
    return []


# ------------------------------------------------------------------ state / search / stats
def timetable_weeks(store):
    return {k[10:]: tt_entries(v[1]) for k, v in sorted(store.items()) if k.startswith("timetable:")}


def stats(store):
    g = lambda k: store[k][1] if k in store else None
    marks = {}
    for x in grade_items(g("grades")):
        marks.setdefault(x["subject"], []).append(x)
    grades = [dict(subject=s, average=(round(sum(m["num"] for m in v if m["num"] is not None) /
                                             max(1, len([m for m in v if m["num"] is not None])), 2)
                                       if any(m["num"] is not None for m in v) else None),
                   marks=[m["value"] for m in v]) for s, v in sorted(marks.items())]
    ab = absence_items(g("absences"))
    now_iso = datetime.now().strftime("%Y-%m-%dT%H:%M")
    entries = [e for es in timetable_weeks(store).values() for e in es if not e["cancelled"] and e["start"] <= now_iso]
    total, missed = {}, {}
    for t in entries:
        total[t["subject"] or "?"] = total.get(t["subject"] or "?", 0) + 1
    for a in ab:
        s_ = f"{a['start']}T{a['start_time'] or '00:00'}"
        e_ = f"{a['end']}T{a['end_time'] or '23:59'}"
        for t in entries:
            if s_ <= t["start"] <= e_:
                missed[t["subject"] or "?"] = missed.get(t["subject"] or "?", 0) + 1
    cfg = config()
    warn, crit = float(cfg.get("absence_warn", 0.15)), float(cfg.get("absence_critical", 0.25))
    limits = sorted([dict(subject=s, missed=n, total=total[s], pct=round(n / total[s], 3),
                          level="critical" if n / total[s] >= crit else "warn" if n / total[s] >= warn else "ok")
                     for s, n in missed.items() if total.get(s)], key=lambda x: -x["pct"])
    days = 0
    for a in ab:
        try:
            days += (date.fromisoformat(a["end"]) - date.fromisoformat(a["start"])).days + 1
        except ValueError:
            pass
    return dict(grades=grades, absences=dict(
        total=len(ab), excused=sum(a["excused"] for a in ab), open=sum(not a["excused"] for a in ab), days=days,
        missed_lessons_by_subject=dict(sorted(missed.items(), key=lambda kv: -kv[1])), limits=limits,
        thresholds=dict(warn=warn, critical=crit),
        note="Based on timetable weeks stored locally (use `sync --full` for the whole year). "
             "Thresholds are placeholders - set yours with `config set absence_warn 0.2` / `absence_critical 0.3`."))


# ------------------------------------------------------------------ Parent mode (read-only view for other devices)
PARENT_SECTIONS = ("today", "timetable", "homework", "exams", "plan", "changes", "grades", "absences")


def parent_token(rotate=False):
    """The secret in the Parent link (kept in the Keychain, not in a file). Anyone with the link can see the Parent
    view - nothing else."""
    import vault
    long_links = str(config().get("links_long", "")).lower() in ("1", "true", "yes", "on")
    tok = None if rotate else vault.get("parent-token")
    if tok and long_links and len(tok) < 64:   # "require long link": replace a short link by a long one
        tok = None
    if not tok:
        tok = secrets.token_urlsafe(48 if long_links else 24)
        vault.set("parent-token", tok)
        save_config({**config(), "links_created": time.time()})        # for "the link expires after N days"
    _TOK["t"] = 0.0
    return tok


def parent_link_expiry():
    """-> (expired, ends_at epoch or None). Setting 'links_expire_days' (0 = never) counts from when the link was made."""
    try:
        days = int(config().get("links_expire_days") or 0)
    except (TypeError, ValueError):
        days = 0
    if days <= 0:
        return False, None
    created = float(config().get("links_created") or 0)
    if not created:                                  # a link from before this setting existed: counts from now
        created = time.time()
        save_config({**config(), "links_created": created})
    end = created + days * 86400
    return time.time() > end, end


_TOK = {"t": 0.0, "v": ""}


def parent_token_peek():
    """Short-lived cached read for the Parent web server (it checks the token on every request)."""
    if time.time() - _TOK["t"] > 5:
        import vault
        _TOK.update(t=time.time(), v=vault.get("parent-token") or "")
    return _TOK["v"]


def parent_sections():
    raw = config().get("parent_sections")
    chosen = [x.strip() for x in str(raw).split(",")] if raw else list(PARENT_SECTIONS)
    return [x for x in PARENT_SECTIONS if x in chosen]


def parent_state(con=None, sections=None):
    """What the Parent website may show. Built field by field from an allow-list: it never contains WebUntis
    messages, sign-in data, settings or anything that can change data."""
    st = state(con)
    secs = [x for x in PARENT_SECTIONS if x in sections] if sections else parent_sections()
    out = dict(synced_at=st["synced_at"], server_time=time.time(), sections=secs)
    if "timetable" in secs or "today" in secs:
        out["timetable"] = {wk: [dict(date=e["date"], start=e["start"], end=e["end"], subject=e["subject"], teacher=e["teacher"],
                                      room=e["room"], cancelled=e["cancelled"], info=e["info"], subject_long=e["subject_long"],
                                      teacher_long=e["teacher_long"], room_long=e["room_long"], group=e["group"], topic=e["topic"],
                                      note=e["note"], substitution=e["substitution"], changes=e["changes"], color=e["color"])
                                 for e in es]
                            for wk, es in st["timetable"].items()}
    if "homework" in secs or "today" in secs:
        out["homework"] = [dict(subject=h["subject"], due=h["due"], text=h["text"], remark=h["remark"], done=h["done"],
                                status=h["status"], days_left=h["days_left"]) for h in st["homework"] if not h["dismissed"]]
        out["homework_summary"] = {k: v for k, v in st["homework_summary"].items() if k != "dismissed"}
    if "exams" in secs or "today" in secs:
        out["exams"] = [dict(date=e["date"], start=e["start"], end=e["end"], subject=e["subject"], text=e["text"]) for e in st["exams"]]
    if "plan" in secs or "today" in secs:  # your study plan: read-only on the Parent site, edited only on this computer
        since = (date.today() - timedelta(7)).isoformat()
        out["plan"] = [dict(date=p["date"], start=p["start"], minutes=p["minutes"], subject=p["subject"], title=p["title"],
                            notes=p["notes"], done=p["done"]) for p in st["plan"] if p["date"] >= since]
    if "changes" in secs or "today" in secs:  # kind "message" = WebUntis inbox: private, never shown
        out["changes"] = [dict(at=c["at"], kind=c["kind"], text=c["text"]) for c in st["changes"] if c["kind"] != "message"][:30]
    if "grades" in secs:
        out["grades"] = [dict(subject=g["subject"], average=g["average"], marks=g["marks"]) for g in st["stats"]["grades"]]
    if "absences" in secs:
        ab = st["stats"]["absences"]
        out["absences"] = dict(total=ab["total"], excused=ab["excused"], open=ab["open"], days=ab["days"], limits=ab["limits"],
                               list=[dict(start=a["start"], end=a["end"], excused=a["excused"], reason=a["reason"]) for a in st["absences"]])
    return out


# ------------------------------------------------------------------ teachers & rooms
def people(timetable):
    d = {}
    now_iso = datetime.now().strftime("%Y-%m-%dT%H:%M")
    for es in timetable.values():
        for e in es:
            for t in filter(None, [x.strip() for x in e["teacher"].split(",")]):
                r = d.setdefault(t, dict(name=t, subjects=set(), rooms=set(), lessons=0, next=""))
                r["subjects"].add(e["subject"]); r["rooms"].add(e["room"]); r["lessons"] += 1
                if e["start"] >= now_iso and not e["cancelled"] and (not r["next"] or e["start"] < r["next"]):
                    r["next"] = e["start"]
    return sorted([dict(name=r["name"], subjects=sorted(filter(None, r["subjects"])), rooms=sorted(filter(None, r["rooms"])),
                        lessons=r["lessons"], next=r["next"][:16]) for r in d.values()], key=lambda r: r["name"])


# ------------------------------------------------------------------ study plan (written by you, edited on the computer)
def _plan_clean(d):
    """Validates the fields you may set. Keys can only come from this fixed list (used to build SQL safely)."""
    out = {}
    if "date" in d:
        try:
            out["date"] = date.fromisoformat(str(d["date"]).strip()).isoformat()
        except ValueError:
            raise ValueError("date must look like 2026-11-24")
    if "start" in d:
        t = str(d["start"] or "").strip()
        if t and not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", t):
            raise ValueError("time must look like 16:30")
        out["start"] = t
    if "minutes" in d:
        try:
            m = int(d["minutes"] or 0)
        except (TypeError, ValueError):
            raise ValueError("minutes must be a number")
        if not 0 <= m <= 720:
            raise ValueError("minutes must be between 0 and 720")
        out["minutes"] = m
    if "subject" in d:
        out["subject"] = str(d["subject"] or "").strip()[:40]
    if "title" in d:
        t = str(d["title"] or "").strip()
        if not t or len(t) > 120:
            raise ValueError("a title is required (max 120 characters)")
        out["title"] = t
    if "notes" in d:
        out["notes"] = str(d["notes"] or "").strip()[:500]
    if "done" in d:
        out["done"] = int(bool(d["done"]))
    return out


def parse_plan_date(s):
    """today / tomorrow / mon..sun / 2026-11-24 / 24.11. / 24.11.2026 -> ISO date"""
    s = (s or "today").strip().lower()
    t = date.today()
    if s == "today":
        return t.isoformat()
    if s == "tomorrow":
        return (t + timedelta(1)).isoformat()
    days = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
    if s[:3] in days and len(s) <= 9:
        return (t + timedelta((days.index(s[:3]) - t.weekday()) % 7 or 7)).isoformat()
    m = re.fullmatch(r"(\d{1,2})\.(\d{1,2})\.?(\d{4})?", s)
    if m:
        try:
            return date(int(m.group(3) or t.year), int(m.group(2)), int(m.group(1))).isoformat()
        except ValueError:
            pass
    return date.fromisoformat(s).isoformat()


def plan_items(con):
    rows = con.execute("select id, date, start, minutes, subject, title, notes, done from plan_items "
                       "order by date, case when start = '' or start is null then '99:99' else start end, id").fetchall()
    return [dict(id=r[0], date=r[1], start=r[2] or "", minutes=r[3] or 0, subject=r[4] or "", title=r[5],
                 notes=r[6] or "", done=bool(r[7])) for r in rows]


def plan_add(con, **d):
    if "title" not in d:
        raise ValueError("a title is required")
    c = {**dict(date=date.today().isoformat(), start="", minutes=0, subject="", notes="", done=0), **_plan_clean(d)}
    cur = con.execute("insert into plan_items(date, start, minutes, subject, title, notes, done) values(?,?,?,?,?,?,?)",
                      (c["date"], c["start"], c["minutes"], c["subject"], c["title"], c["notes"], c["done"]))
    con.commit()
    return cur.lastrowid


def plan_update(con, item_id, **d):
    c = _plan_clean(d)
    if c:
        con.execute(f"update plan_items set {', '.join(k + '=?' for k in c)} where id=?", (*c.values(), int(item_id)))
        con.commit()


def plan_delete(con, item_id):
    con.execute("delete from plan_items where id=?", (int(item_id),))
    con.commit()


# ------------------------------------------------------------------ grade calculator (Austrian scale: 1 best, 5 worst)
def grade_calc(grades, subject, add=(), target=None):
    nums = [float(m) for g in grades if g["subject"] == subject for m in g["marks"]
            if str(m).replace(".", "", 1).isdigit()]
    n, tot = len(nums), sum(nums)
    out = dict(subject=subject, n=n, current=round(tot / n, 2) if n else None)
    if add:
        out["with_added"] = round((tot + sum(add)) / (n + len(add)), 2)
    if target:
        need = target * (n + 1) - tot
        out["needed_next_mark"] = round(need, 2)
        out["reachable"] = need >= 1
    return out


# ------------------------------------------------------------------ reminders & menu bar summary
def reminders(st, now=None, sent=frozenset()):
    now = now or datetime.now()
    today, tmr = now.date().isoformat(), (now.date() + timedelta(1)).isoformat()
    out = []
    for h in st["homework"]:
        if h["done"] or h["dismissed"]:
            continue
        if h["status"] == "missed" and h["days_left"] is not None and -h["days_left"] <= 3:
            out.append((f"hw-missed:{h['id']}", "Homework missed", f"{h['subject']}: {h['text']} (due {h['due']})"))
        if h["due"] == tmr and now.hour >= 16:
            out.append((f"hw-tmr:{h['id']}:{tmr}", "Homework due tomorrow", f"{h['subject']}: {h['text']}"))
        if h["due"] == today and now.hour >= 6:
            out.append((f"hw-today:{h['id']}:{today}", "Homework due today", f"{h['subject']}: {h['text']}"))
    for e in st["exams"]:
        try:
            n = (date.fromisoformat(e["date"]) - now.date()).days
        except ValueError:
            continue
        if 0 <= n <= 3:
            out.append((f"exam:{e['id']}:{n}", f"Exam in {n} day(s)" if n else "Exam today", f"{e['subject']} {e['text']}"))
    for l in st["stats"]["absences"]["limits"]:
        if l["level"] != "ok":
            out.append((f"abs:{l['subject']}:{l['level']}", "Absence limit " + l["level"],
                        f"{l['subject']}: missed {l['missed']}/{l['total']} lessons ({l['pct']:.0%})"))
    for p in st.get("plan", []):
        if not p["done"] and p["date"] == today and now.hour >= 6:
            out.append((f"plan:{p['id']}:{today}", "Study plan today", p["title"] + (f" ({p['start']})" if p["start"] else "")))
    return [r for r in out if r[0] not in sent]


def menu_summary(st, now=None):
    now = now or datetime.now()
    nowi, today, tmr = now.strftime("%Y-%m-%dT%H:%M"), now.date().isoformat(), (now.date() + timedelta(1)).isoformat()
    es = sorted((e for w in st["timetable"].values() for e in w), key=lambda e: e["start"])
    nxt = next((e for e in es if e["start"] >= nowi and not e["cancelled"]), None)
    line = lambda e: f"{e['start'][11:16]} {e['subject'] or 'Lesson'} {e['room']}".strip() + (" - CANCELLED" if e["cancelled"] else "")
    alerts = [f"Cancelled: {e['subject']} {e['date'][5:]} {e['start'][11:16]}" for e in es
              if e["cancelled"] and e["date"] in (today, tmr) and e["start"] >= nowi]
    alerts += [f"Absence {l['level']}: {l['subject']} {l['pct']:.0%}" for l in st["stats"]["absences"]["limits"] if l["level"] != "ok"]
    missed = st["homework_summary"]["missed"]
    if missed:
        alerts.append(f"{missed} homework missed")
    unseen = sum(not c["seen"] for c in st["changes"])
    if unseen:
        alerts.append(f"{unseen} unseen change(s)")
    return dict(
        title=(f"{nxt['start'][11:16]} {nxt['subject']} {nxt['room']}".strip() if nxt and nxt["date"] == today
               else ("SSC: done today" if es else "SSC")),
        today=[line(e) for e in es if e["date"] == today], tomorrow=[line(e) for e in es if e["date"] == tmr],
        homework=[("! " if h["status"] == "missed" else "") + f"{h['due'][5:]} {h['subject']}: {h['text']}"[:70]
                  for h in hw_active(st)[:5]],
        alerts=alerts)


# ------------------------------------------------------------------ homework: status + automatic dismissal
HW_STATUSES = ("missed", "soon", "pending", "done")


def hw_cfg():
    c = config()
    def num(k, d, lo, hi):
        try:
            return max(lo, min(hi, int(c.get(k, d))))
        except (TypeError, ValueError):
            return d
    return dict(soon_days=num("hw_soon_days", 2, 0, 14),
                dismiss_done=str(c.get("hw_dismiss_done", "true")).lower() not in ("0", "false", "no", "off"),
                dismiss_missed_days=num("hw_dismiss_missed_days", 7, -1, 365))  # -1 = never auto-dismiss missed homework


def hw_set_dismiss(con, hw_id, state):
    """state: 'dismissed' (hide it) or 'restored' (show it, even if an automatic rule would hide it)."""
    if state not in ("dismissed", "restored"):
        raise ValueError("bad state")
    con.execute("insert or replace into hw_dismiss values(?,?,?)", (str(hw_id), state, time.time()))
    con.commit()


def hw_classify(items, local_done, overrides, cfg=None, today=None):
    """Adds status (missed | soon | pending | done), days_left, dismissed and dismiss_reason to each item.
    Status comes from WebUntis when it reports one, otherwise from the due date. Automatic dismissal:
    done homework disappears at once (if enabled); missed homework disappears N days after its due date."""
    cfg, today = cfg or hw_cfg(), today or date.today()
    for x in items:
        x["done"] = x["id"] in local_done or x["completed"] or x.get("source_status") == "done"
        try:
            days = (date.fromisoformat(x["due"]) - today).days
        except ValueError:
            days = None
        x["days_left"] = days
        if x["done"]:
            st = "done"
        elif x.get("source_status") in ("missed", "soon", "pending"):
            st = x["source_status"]
        elif days is None:
            st = "pending"
        elif days < 0:
            st = "missed"
        elif days <= cfg["soon_days"]:
            st = "soon"
        else:
            st = "pending"
        x["status"] = st
        ov, reason = overrides.get(x["id"]), ""
        if ov == "restored":
            gone = False
        elif ov == "dismissed":
            gone, reason = True, "manual"
        elif st == "done" and cfg["dismiss_done"]:
            gone, reason = True, "done"
        elif st == "missed" and cfg["dismiss_missed_days"] >= 0 and days is not None and -days > cfg["dismiss_missed_days"]:
            gone, reason = True, "missed"
        else:
            gone = False
        x["dismissed"], x["dismiss_reason"] = gone, reason
    return items


def hw_summary(items):
    live = [x for x in items if not x["dismissed"]]
    return dict(missed=sum(x["status"] == "missed" for x in live), soon=sum(x["status"] == "soon" for x in live),
                pending=sum(x["status"] == "pending" for x in live), done=sum(x["status"] == "done" for x in live),
                dismissed=sum(x["dismissed"] for x in items))


def hw_active(st):
    """Homework still needing attention (not done, not dismissed), most urgent first."""
    order = {"missed": 0, "soon": 1, "pending": 2}
    return sorted((h for h in st["homework"] if not h["dismissed"] and h["status"] in order),
                  key=lambda h: (order[h["status"]], h["due"]))


def state(con=None):
    con = con or db()
    store = load(con)
    g = lambda k: store[k][1] if k in store else None
    done = done_ids(con)
    cfg = hw_cfg()
    hw = hw_classify(homework_items(g("homework")), done, dict(con.execute("select hw_id, state from hw_dismiss")), cfg)
    ch = con.execute("select id, at, kind, text, seen from changes order by id desc limit 300").fetchall()
    ex = exam_items(g("exams"))
    return dict(
        plan=plan_items(con),
        synced_at=max((t for t, _ in store.values()), default=0),
        timetable=timetable_weeks(store), homework=hw, homework_summary=hw_summary(hw), homework_cfg=cfg, exams=ex,
        absences=absence_items(g("absences")), messages=message_items(g("messages")),
        grades=grade_items(g("grades")), stats=stats(store), people=people(timetable_weeks(store)),
        changes=[dict(id=i, at=a, kind=k, text=t, seen=bool(s)) for i, a, k, t, s in ch])


def search(q, con=None):
    st = state(con)
    idx = []
    for wk, es in st["timetable"].items():
        for e in es:
            idx.append(("lesson", e["start"][:16], f"{e['subject']} {e['teacher']} {e['room']}", e["info"]))
    idx += [("homework", x["due"], f"{x['subject']}", f"{x['text']} {x['remark']}") for x in st["homework"]]
    idx += [("exam", x["date"], x["subject"], x["text"]) for x in st["exams"]]
    idx += [("message", x["sent"], x["subject"], f"{x['sender']} {x['preview']}") for x in st["messages"]]
    idx += [("grade", x["date"], x["subject"], f"{x['value']} {x['text']}") for x in st["grades"]]
    idx += [("plan", x["date"], x["subject"] or "study plan", f"{x['title']} {x['notes']}") for x in st["plan"]]
    idx += [("absence", x["start"], x["reason"], "") for x in st["absences"]]
    words = q.lower().split()
    return [dict(kind=k, date=d, title=t, text=x) for k, d, t, x in idx
            if words and all(w in f"{k} {d} {t} {x}".lower() for w in words)][:300]


# ------------------------------------------------------------------ export
def _esc(s):
    return str(s).replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")


def export_ics(con=None):
    st = state(con)
    now = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    L = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Stundl Student Client//EN", "CALSCALE:GREGORIAN",
         "X-WR-CALNAME:Stundl Student Client", "X-WR-TIMEZONE:Europe/Vienna"]

    def ev(uid, summary, start, end=None, allday=False, loc="", desc="", cancelled=False):
        L.extend(["BEGIN:VEVENT", "UID:%s@ssc" % hashlib.md5(uid.encode()).hexdigest(), "DTSTAMP:" + now])
        if allday:
            d = start.replace("-", "")
            L.append("DTSTART;VALUE=DATE:" + d)
            L.append("DTEND;VALUE=DATE:" + (date.fromisoformat(start) + timedelta(days=1)).strftime("%Y%m%d"))
        else:
            f = lambda s: s.replace("-", "").replace(":", "")[:15]
            L.append("DTSTART:" + f(start)); L.append("DTEND:" + f(end))
        L.append("SUMMARY:" + _esc(summary))
        if loc: L.append("LOCATION:" + _esc(loc))
        if desc: L.append("DESCRIPTION:" + _esc(desc))
        if cancelled: L.append("STATUS:CANCELLED")
        L.append("END:VEVENT")

    for es in st["timetable"].values():
        for e in es:
            ev("tt" + e["start"] + e["subject"], ("CANCELLED: " if e["cancelled"] else "") + (e["subject"] or "Lesson"),
               e["start"], e["end"], loc=e["room"], desc=" ".join(filter(None, [e["teacher"], e["info"]])),
               cancelled=e["cancelled"])
    for x in st["homework"]:
        if x["due"]:
            ev("hw" + x["id"], f"Homework {x['subject']}: {x['text']}"[:120], x["due"], allday=True, desc=x["text"])
    for x in st["exams"]:
        if x["date"]:
            ev("ex" + x["id"], f"Exam {x['subject']}", x["date"], allday=True, desc=x["text"])
    L.append("END:VCALENDAR")
    return "\r\n".join(L) + "\r\n"


def export_csv(kind, con=None):
    st = state(con)
    rows = {"homework": (["due", "subject", "text", "remark", "status", "dismissed"],
                         [[x["due"], x["subject"], x["text"], x["remark"], x["status"], x["dismiss_reason"] or ""] for x in st["homework"]]),
            "exams": (["date", "start", "end", "subject", "text"],
                      [[x["date"], x["start"], x["end"], x["subject"], x["text"]] for x in st["exams"]]),
            "grades": (["subject", "value", "date", "text"],
                       [[x["subject"], x["value"], x["date"], x["text"]] for x in st["grades"]]),
            "absences": (["start", "end", "excused", "reason"],
                         [[x["start"], x["end"], "yes" if x["excused"] else "no", x["reason"]] for x in st["absences"]]),
            }[kind]
    buf = io.StringIO()
    w = csv.writer(buf); w.writerow(rows[0]); w.writerows(rows[1])
    return buf.getvalue()


# ------------------------------------------------------------------ offline snapshot
def write_snapshot(template):
    """Write everything to files: snapshot.json (raw state) and offline.html (self-contained viewer
    that opens by double-click, no server, no network)."""
    st = state()
    HOME.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(st)
    (HOME / "snapshot.json").write_text(payload, encoding="utf-8")
    html = load_page(template)
    html = html.replace("<script>\n", "<script>window.__SNAPSHOT__=" + payload.replace("</", "<\\/") + ";</script>\n<script>\n", 1)
    (HOME / "offline.html").write_text(html, encoding="utf-8")
    (HOME / "mobile.html").write_text(render_static(st), encoding="utf-8")
    cfg = config()
    write_widget(st, cfg)
    if str(cfg.get("icloud", "")).lower() in ("1", "true", "on", "yes"):
        d = icloud_dir()
        if d:
            d.mkdir(exist_ok=True)
            for f in ("offline.html", "mobile.html"):
                shutil.copyfile(HOME / f, d / f)
    return HOME / "offline.html"


def icloud_dir():
    if sys.platform != "darwin":
        return None
    base = Path.home() / "Library" / "Mobile Documents" / "com~apple~CloudDocs"
    return base / "Stundl Student Client" if base.exists() else None


WIDGET_BUNDLE = "com.stundl.ssc.host.widget"


def widget_dir():
    """The widget's own sandbox container - no App Group needed. It exists once the widget has been
    added to the desktop at least once."""
    return Path.home() / "Library" / "Containers" / WIDGET_BUNDLE / "Data" / "Library" / "Application Support"


def write_widget(st, cfg=None):
    if sys.platform != "darwin":  # the WidgetKit widget is macOS-only
        return None
    d = widget_dir()
    if not d.parents[2].exists():  # widget not installed yet
        return None
    nowi = datetime.now().strftime("%Y-%m-%dT%H:%M")
    es = sorted((e for w in st["timetable"].values() for e in w if e["start"] >= nowi), key=lambda e: e["start"])[:8]
    m = menu_summary(st)
    d.mkdir(parents=True, exist_ok=True)
    (d / "widget.json").write_text(json.dumps(dict(
        updated=time.time(), title=m["title"], alerts=m["alerts"], homework=m["homework"],
        lessons=[dict(start=e["start"], end=e["end"], subject=e["subject"], room=e["room"], cancelled=e["cancelled"])
                 for e in es])))
    return d / "widget.json"


def render_static(st):
    """Plain HTML without JavaScript - opens anywhere, e.g. in the iPhone Files app (iCloud Drive)."""
    e = html.escape
    m = menu_summary(st)
    sec = lambda t, rows: f"<h2>{e(t)}</h2>" + ("".join(f"<p>{e(r)}</p>" for r in rows) or "<p class=m>-</p>")
    plan = [f"{p['date'][5:]} {p['start']} {p['title']}".replace("  ", " ") + (" (done)" if p["done"] else "")
            for p in st["plan"] if p["date"] >= date.today().isoformat()][:7]
    changes = [c["text"] for c in st["changes"][:10]]
    upd = datetime.fromtimestamp(st["synced_at"]).strftime("%d.%m. %H:%M") if st["synced_at"] else "never"
    return ("<!doctype html><html><head><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'>"
            "<title>Stundl Student Client</title><style>body{font:16px -apple-system,system-ui;margin:16px;max-width:640px}"
            "h2{font-size:15px;margin:20px 0 6px;color:#555}p{margin:3px 0}.m{color:#888}.a{color:#c00}</style></head><body>"
            f"<h1>Stundl Student Client</h1><p class=m>Synced {e(upd)}</p>"
            + (sec("Alerts", m["alerts"]).replace("<p>", "<p class=a>")) + sec("Today", m["today"]) + sec("Tomorrow", m["tomorrow"])
            + sec("Homework (open)", m["homework"]) + sec("Study plan", plan) + sec("Latest changes", changes)
            + "</body></html>")


# ------------------------------------------------------------------ scheduling / notifications
def notify(title, msg):
    """Desktop notification: macOS notification centre, Windows balloon tip, Linux notify-send (else printed)."""
    title, msg = str(title)[:100], str(msg)[:300]
    try:
        if sys.platform == "darwin":
            esc = lambda t: t.replace("\\", "\\\\").replace('"', '\\"')
            subprocess.run(["osascript", "-e", f'display notification "{esc(msg)}" with title "{esc(title)}"'], check=False)
        elif sys.platform.startswith("win"):
            script = ("Add-Type -AssemblyName System.Windows.Forms; $n = New-Object System.Windows.Forms.NotifyIcon; "
                      "$n.Icon = [System.Drawing.SystemIcons]::Information; $n.Visible = $true; "
                      "$n.ShowBalloonTip(8000, $env:SSC_T, $env:SSC_M, [System.Windows.Forms.ToolTipIcon]::Info); "
                      "Start-Sleep -Seconds 9; $n.Dispose()")   # text goes through environment variables: no quoting problems
            subprocess.Popen(["powershell", "-NoProfile", "-WindowStyle", "Hidden", "-Command", script],
                             env={**os.environ, "SSC_T": title, "SSC_M": msg}, creationflags=0x08000000)
        elif shutil.which("notify-send"):
            subprocess.run(["notify-send", "--app-name=SSC", title, msg], check=False)
        else:
            print(f"[{title}] {msg}")
    except Exception:
        print(f"[{title}] {msg}")
