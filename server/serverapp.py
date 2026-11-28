"""SSC Server: the web service. Accounts with roles, the Parent view for guest/user, the full app + admin panel for admin."""
import json
import os
import re
import secrets
import ssl
import subprocess
import sys
import threading
import time
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import auth
import core
import serverconf
import ssc

from ssc_version import VERSION
HERE = Path(__file__).resolve().parent
START = time.time()
TLS = {"on": False}                  # is this process really serving HTTPS (it falls back to HTTP if there is no certificate)


def _page(name, prefs_api=True, classic=False):
    for d in (HERE, core.APPDIR, HERE.parent):
        f = d / name
        if f.exists():
            return core.load_page(f, prefs_api=prefs_api, classic=classic)
    raise FileNotFoundError(name)


def addresses():
    """[(interface, 'a.b.c.d/p')] from `ip`, with a socket fallback."""
    try:
        r = subprocess.run(["ip", "-j", "-4", "addr"], capture_output=True, text=True, timeout=5)
        a = serverconf.parse_ip_json(r.stdout) if r.returncode == 0 else []
    except (OSError, ValueError, subprocess.SubprocessError):
        a = []
    if not a:
        a = [("net", h + "/24") for h in ssc.lan_hosts() if h[:1].isdigit()]
    return a


def request_apply(parts):
    """Ask the root helper to apply system settings (network, ssh, firewall, web restart)."""
    d = serverconf.state_dir() / "apply"
    d.mkdir(parents=True, exist_ok=True)
    tmp = d / ".request.tmp"
    tmp.write_text(json.dumps({"parts": sorted(set(parts)), "at": time.time()}))
    os.replace(tmp, d / "request.json")


def apply_result():
    try:
        return json.loads((serverconf.state_dir() / "apply" / "result.json").read_text())
    except (OSError, ValueError):
        return None


def is_global(s):
    """A server administrator: not tied to any space. Only these may touch settings, the machine and other spaces."""
    return bool(s) and s["role"] == "admin" and not s.get("space")


def is_scoped(s):
    """An account that lives inside one space (its administrator, a student, or that space's parents)."""
    return bool(s) and bool(s.get("space")) and not s.get("anon")


def allowed_profiles(s):
    """The profile ids this session may look at - the single place that decides, for every route.
    server administrator: all | space administrator: every profile of the space | student: exactly its own profile |
    guest/user inside a space: the profiles of that space it was given | guest/user outside spaces: only profiles outside spaces |
    the shared passwordless guest: the first profile outside spaces."""
    if not s or s.get("anon"):
        return core.profile_ids("")[:1]
    if is_global(s):
        return core.profile_ids()
    space = s.get("space") or ""
    pool = core.profile_ids(space)
    if s["role"] == "admin":
        return pool
    own = auth.limits_for(s["name"])["profiles"]
    if s["role"] == "student":
        return [p for p in pool if p in own][:1]
    return [p for p in pool if p in own] if own else pool


WEBUNTIS_HOST = re.compile(r"^([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+webuntis\.com$")
SCOPED_GET = ("/api/state", "/api/search", "/api/config")                 # what a space administrator / student may use of the full app's API
SCOPED_POST = ("/api/done", "/api/seen", "/api/homework/dismiss", "/api/homework/restore", "/api/plan", "/api/plan/delete")
SCOPED_EXPORT = re.compile(r"^/export/(ics|homework|exams|grades|absences)$")
NO_PROFILE_OK = ("/health", "/login", "/api/login", "/api/login-info", "/api/logout", "/api/me", "/api/prefs", "/api/password", "/space")


def member_labels(space):
    return {p["id"]: p["label"] for p in core.profile_list(space)}


def make_member(space, role, name, password, label="", profiles=(), who="console"):
    """Create an account inside a space. A student gets a profile of its own (data, WebUntis key and Parent link are separate);
    a guest/user must be given at least one profile of this space; an admin here administers this space only."""
    all_ids = core.profile_ids(space)
    if role == "student":
        pid = core.profile_add("p" + secrets.token_hex(4), label or name, space)
        try:
            auth.create(name, password, "student", who=who, space=space)
            auth.set_limits(name, profiles=[pid], who=who, all_sections=serverconf.SECTIONS, all_profiles=core.profile_ids(space))
        except Exception:
            core.profile_remove(pid)
            raise
        return pid
    if role in ("guest", "user"):
        ids = [x for x in profiles if x]
        if not ids or any(x not in all_ids for x in ids):
            raise auth.AuthError("choose which student(s) of this space the account may see")
        auth.create(name, password, role, who=who, space=space)
        auth.set_limits(name, profiles=ids, who=who, all_sections=serverconf.SECTIONS, all_profiles=all_ids)
        return None
    if role == "admin":
        auth.create(name, password, "admin", who=who, space=space)
        return None
    raise auth.AuthError("role must be admin, student, user or guest")


def collect_alerts(cfg):
    """Everything wrong with the machine right now (see health.py)."""
    import health
    import sysinfo
    info = sysinfo.info(serverconf.system_root())
    import untis_direct
    keep = getattr(core._PROFILE, "id", None)
    lasts, has_key = [], False
    try:
        for pid in core.profile_ids():
            core.set_profile(pid)
            if untis_direct.load_credentials():
                has_key = True
                lasts.append(max((t for t, _ in core.load().values()), default=0))
    finally:
        core._PROFILE.id = keep
    return health.alerts(info, health.read_scan(), cfg["health"], synced_at=min(lasts) if lasts else 0, sync_minutes=cfg["sync"]["minutes"], has_key=has_key,
                         dns_ok=health.dns_ok() if (has_key or cfg["autoupdate"]["enabled"] or cfg["mail"]["enabled"]) else None)


def sync_core_settings(cfg):
    """Settings the shared app code reads from config.json."""
    core.save_config({**core.config(), "sync_mode": "direct", "links_long": cfg["links"]["long"], "links_expire_days": cfg["links"]["expire_days"]})


def parse_who(text):
    """`who` output -> [{user, tty}] (who is logged in on which terminal)."""
    out = []
    for line in str(text).splitlines():
        parts = line.split()
        if len(parts) >= 2:
            out.append({"user": parts[0], "tty": parts[1]})
    return out


def _getty_from_proc(proc="/proc"):
    """No system bus for this user (minimal image): look for the login program on tty1 directly."""
    try:
        for d in os.listdir(proc):
            if d.isdigit():
                with open(f"{proc}/{d}/cmdline", "rb") as f:
                    parts = f.read().split(b"\0")
                if parts and (parts[0].endswith(b"agetty") or parts[0].endswith(b"login")) and b"tty1" in parts:
                    return "active"
    except OSError:
        pass
    return "unknown"


def console_info():
    """Is there a login on the machine's own screen (tty1), and who is logged in there? (Shown in the admin panel; the install tests check it.)"""
    info = {"getty": "unknown", "logged_in": []}
    try:
        info["getty"] = subprocess.run(["systemctl", "is-active", "getty@tty1.service"], capture_output=True, text=True, timeout=5).stdout.strip() or _getty_from_proc()
        info["logged_in"] = parse_who(subprocess.run(["who"], capture_output=True, text=True, timeout=5).stdout)
    except (OSError, subprocess.SubprocessError):
        pass
    return info


def _wifi_card():
    try:
        import applycfg
        return applycfg.wifi_interface()
    except Exception:
        return None


PARTS = {"network": ("network",), "ssh": ("ssh",), "firewall": ("firewall",), "web": ("web",), "links": ("links",)}


def changed_parts(old, new):
    parts = []
    for k in ("network", "ssh", "firewall", "web", "links", "console", "power", "schedule"):
        if old[k] != new[k]:
            parts.append(k)
    if old["wifi"] != new["wifi"] and "network" not in parts:
        parts.append("network")
    if old["power"]["wol"] != new["power"]["wol"] and "network" not in parts:
        parts.append("network")
    if old["web"]["port"] != new["web"]["port"] or old["links"] != new["links"]:
        parts.append("firewall")
    return parts


def make_handler():
    Base = ssc.make_handler()

    class SH(Base):
        server_version, sys_version = "SSC-Server", ""

        # ---- auth plumbing
        def guard(self):
            return True   # reachable by IP or name; protection is the login + same-site cookie

        def sess(self):
            m = re.search(r"(?:^|;\s*)ssc_sess=([A-Za-z0-9_-]+)", self.headers.get("Cookie", ""))
            return auth.session(m.group(1)) if m else None

        def authed(self):
            """Used by the inherited full-app API. Server administrators: everything. A space administrator or student: only the
            routes on the allow-list (the gate below sets the flag per request) - every other route is refused."""
            s = self.sess()
            if is_global(s):
                return True
            return bool(is_scoped(s) and s["role"] in ("admin", "student") and getattr(self, "_scoped_ok", False))

        def gate(self, method, path, s):
            """Per request: choose the profile, and decide whether a scoped account may use this path of the inherited API."""
            allowed = self.enter(s)
            self._scoped_ok = False
            if s and not allowed and not path.startswith("/api/space/") and path not in NO_PROFILE_OK:
                return self.j(403, {"error": "no student profile is assigned to this account yet - ask your administrator"})
            if is_scoped(s) and s["role"] in ("admin", "student"):
                self._scoped_ok = (path in SCOPED_GET or bool(SCOPED_EXPORT.match(path))) if method == "GET" else path in SCOPED_POST
            return None

        def ip(self):
            return self.client_address[0]

        def enter(self, s):
            """Point this request at the child/profile the account has chosen (and may see)."""
            allowed = allowed_profiles(s)
            m = re.search(r"(?:^|;\s*)ssc_profile=([a-z0-9-]{1,20})", self.headers.get("Cookie", ""))
            want = m.group(1) if m else core.MAIN
            core.set_profile(want if want in allowed else (allowed[0] if allowed else core.MAIN))
            return allowed

        def cfg(self):
            return serverconf.load()

        def cookie(self, sid, cfg, clear=False):
            age = 0 if clear else cfg["web"]["session_hours"] * 3600
            return {"Set-Cookie": f"ssc_sess={'' if clear else sid}; HttpOnly; SameSite=Strict; Path=/; Max-Age={age}" + ("; Secure" if TLS["on"] else "")}

        def j(self, code, obj, extra=None):
            return self.send(code, json.dumps(obj), "application/json", extra)

        def html(self, code, page, extra=None):
            return self.send(code, page, "text/html; charset=utf-8", extra)

        def redirect(self, to):
            return self.send(302, "", "text/plain", {"Location": to})

        # ---- GET
        def do_GET(self):
            u = urlparse(self.path)
            s = self.sess()
            denied = self.gate("GET", u.path, s)
            if denied is not None:
                return denied
            if u.path == "/health":
                import updater
                return self.j(200, {"ok": True, "service": "ssc-server", "version": VERSION, "commit": updater.installed_commit()[:7]})
            if u.path == "/api/login-info":
                return self.j(200, {"guest": self.cfg()["web"]["guest_passwordless"], "setup_needed": not auth.has_admin()})
            if u.path == "/login":
                return self.html(200, _page("login.html", prefs_api=False))
            if u.path == "/":
                if not s:
                    return self.html(200, _page("login.html", prefs_api=False))
                classic = self.ui_for(s) == "classic"
                if s["role"] in ("admin", "student") and not s.get("anon"):                      # the full app (a space administrator / student: for their own profiles)
                    return self.html(200, self.chrome(_page("index.html", classic=classic), s, True))
                return self.html(200, self.chrome(_page("parent.html", classic=classic), s, False))
            if u.path == "/admin":
                if not is_global(s):
                    return self.redirect("/space" if is_scoped(s) and s["role"] in ("admin", "student") else "/")
                return self.html(200, _page("admin.html"))
            if u.path == "/space":
                if not (is_scoped(s) and s["role"] in ("admin", "student")):
                    return self.redirect("/admin" if is_global(s) else "/")
                return self.html(200, _page("space.html"))
            if u.path.startswith("/api/space/"):
                return self.space_get(u, s)
            if u.path == "/data.json":
                if not s:
                    return self.j(401, {"error": "sign in first"})
                cfg = self.cfg()
                full = s["role"] in ("admin", "student") and not s.get("anon")
                secs = list(serverconf.SECTIONS) if full else cfg["sections"]["user" if s["role"] != "guest" else "guest"]
                own = [] if s.get("anon") or is_global(s) else auth.limits_for(s["name"])["sections"]
                if own:                                            # this account's own list (never more than the settings allow for its role)
                    secs = [x for x in secs if x in own] or secs[:1]
                out = core.parent_state(sections=secs)
                allowed = allowed_profiles(s)
                labels = {p["id"]: p["label"] for p in core.profile_list()}
                out["profile"] = core.profile_id()
                out["profiles"] = [{"id": p, "label": labels.get(p, p)} for p in allowed]
                return self.send(200, json.dumps(out), "application/json")
            if u.path == "/api/profiles":
                if not s:
                    return self.j(401, {"error": "not signed in"})
                labels = {p["id"]: p["label"] for p in core.profile_list()}
                return self.j(200, {"current": core.profile_id(), "profiles": [{"id": p, "label": labels.get(p, p)} for p in allowed_profiles(s)]})
            if u.path == "/api/prefs":
                if not s:
                    return self.j(401, {"error": "not signed in"})
                return self.j(200, {} if s.get("anon") else auth.get_prefs(s["name"]))
            if u.path == "/api/sync" and is_scoped(s):                          # the shared sync log lists other students' changes: never theirs
                return self.j(200, {"running": ssc.SYNC["running"], "ok": ssc.SYNC["ok"], "log": ""})
            if u.path == "/api/update/check":
                if not is_global(s):
                    return self.j(403, {"error": "admin only"})
                import updater
                try:
                    return self.j(200, dict(updater.check(), flavor="server", apply=apply_result()))
                except updater.UpdateError as e:
                    return self.j(502, {"error": str(e), "current": VERSION, "flavor": "server"})
            if u.path == "/api/me":
                return self.j(200, {"name": s["name"], "role": s["role"], "space": s.get("space", "")}) if s else self.j(401, {"error": "not signed in"})
            if u.path.startswith("/api/admin/"):
                return self.admin_get(u, s)
            return super().do_GET()

        def ui_for(self, s):
            """Beta or Classic for this request. A signed-in account's saved choice decides (it follows the person to every browser, and a cookie that
            a script could not set - or Safari dropped - can't disagree with it); everybody else gets what the cookie says."""
            if s and not s.get("anon"):
                saved = auth.get_prefs(s["name"]).get("ui")
                if saved in core.PREF_UIS:
                    return saved
            return core.ui_choice(self.headers.get("Cookie", ""))

        def chrome(self, page, s, admin):
            import html as _html
            allowed = allowed_profiles(s)
            labels = {p["id"]: p["label"] for p in core.profile_list()}
            pick = ""
            if len(allowed) > 1:                                     # several children: a switcher (the choice is a cookie, checked on every request)
                opts = "".join(f"<option value='{_html.escape(p)}'{' selected' if p == core.profile_id() else ''}>{_html.escape(labels.get(p, p))}</option>" for p in allowed)
                pick = ("<select aria-label='Child' translate='no' style='font:13px system-ui;border-radius:99px;border:0;padding:3px 8px' "
                        "onchange=\"document.cookie='ssc_profile='+this.value+'; Path=/; SameSite=Strict';location.reload()\">" + opts + "</select>")
            bar = ("<style>@media(max-width:900px){#sscbar{bottom:calc(78px + env(safe-area-inset-bottom)) !important;right:8px !important;font-size:12px !important;"
                   "padding:5px 11px !important;gap:9px !important;max-width:calc(100vw - 16px)}#sscbar span.nm{display:none}}</style>"
                   "<div id='sscbar' style='position:fixed;z-index:99;right:12px;bottom:12px;font:13px system-ui;background:rgba(20,24,38,.88);color:#fff;"
                   "padding:7px 12px;border-radius:99px;display:flex;gap:12px;align-items:center'>" + pick +
                   f"<span class='nm'>{'Admin' if is_global(s) else s['role'].title()}: {re.sub('[<>&]', '', s['name'])}</span>"
                   + ("<a style='color:#9db4ff' href='/admin'>Admin panel</a>" if is_global(s) else "")
                   + ("<a style='color:#9db4ff' href='/space'>My space</a>" if is_scoped(s) and s["role"] in ("admin", "student") else "")
                   + "<a style='color:#9db4ff' href='#' onclick=\"fetch('/api/logout',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'}).then(()=>location.href='/')\">Log out</a></div>")
            return page.replace("</body>", bar + "</body>", 1)

        # ---- POST
        def do_POST(self):
            u = urlparse(self.path)
            s0 = self.sess()
            denied = self.gate("POST", u.path, s0)
            if denied is not None:
                return denied
            if u.path == "/api/login":
                return self.login()
            if u.path == "/api/update/install":
                s = self.sess()
                if not is_global(s):
                    return self.j(403, {"error": "admin only"})
                import updater
                try:                                     # re-checked here; the root helper downloads and verifies it again by itself
                    info = updater.check()
                except updater.UpdateError as e:
                    return self.j(502, {"error": str(e)})
                if not info["update_available"]:
                    return self.j(409, {"error": "already up to date", "current": VERSION})
                if info.get("ci") == "failed":
                    return self.j(409, {"error": "the automatic tests of the newest commit failed - not installing it"})
                request_apply(["update"])
                auth.audit(auth.db(), s["name"], "update-requested", info["latest"], self.ip())
                return self.j(200, {"requested": True, "from": VERSION, "from_commit": updater.installed_commit()[:7], "to": info["latest"]})
            if u.path == "/api/sync" and is_scoped(s0):                         # only the student(s) this account may see, never everybody's
                ssc.run_sync_bg(core.profile_id())
                return self.j(200, {})
            if u.path.startswith("/api/space/") or u.path == "/api/password":
                return self.space_post(u, s0)
            if u.path == "/api/prefs":
                s = self.sess()
                b = self.body()
                if not s:
                    return self.j(401, {"error": "not signed in"})
                if b is None:
                    return self.j(415, {"error": "json only"})
                if s.get("anon"):                      # the shared passwordless guest has nobody's settings to keep
                    return self.j(200, {"saved": False})
                saved = auth.set_prefs(s["name"], b)
                extra = {"Set-Cookie": f"ssc_ui={saved['ui']}; Path=/; Max-Age=31536000; SameSite=Lax"} if saved.get("ui") in core.PREF_UIS else None
                return self.j(200, {"saved": True, "prefs": saved}, extra)
            if u.path == "/api/logout":
                m = re.search(r"ssc_sess=([A-Za-z0-9_-]+)", self.headers.get("Cookie", ""))
                auth.logout(m.group(1) if m else None)
                return self.j(200, {}, self.cookie("", self.cfg(), clear=True))
            if u.path.startswith("/api/admin/"):
                return self.admin_post(u)
            return super().do_POST()

        def body(self):
            if self.headers.get("Content-Type", "").split(";")[0] != "application/json":
                return None
            n = min(int(self.headers.get("Content-Length") or 0), 1_000_000)
            try:
                return json.loads(self.rfile.read(n) or b"{}")
            except ValueError:
                return None

        def login(self):
            b = self.body()
            if b is None:
                return self.j(415, {"error": "json only"})
            cfg = self.cfg()
            try:
                if b.get("guest") and cfg["web"]["guest_passwordless"]:
                    sid, role = auth.guest_session(self.ip(), cfg["web"]["session_hours"]), "guest"
                else:
                    sid = auth.login(str(b.get("name", "")).strip().lower(), str(b.get("password", "")), self.ip(), hours=cfg["web"]["session_hours"])
                    role = auth.session(sid)["role"]
            except auth.AuthError as e:
                return self.j(429 if "Too many" in str(e) else 401, {"error": str(e)})
            return self.j(200, {"role": role, "space": auth.session(sid).get("space", "")}, self.cookie(sid, cfg))

        # ---- a space: its administrators and students (everything here is scoped by allowed_profiles / the account's own space)
        def space_scope(self, s, admin_only=False):
            """-> (space id, visible profile ids) or None after answering 403."""
            if not s:
                self.j(401, {"error": "not signed in"})
                return None
            if not (is_scoped(s) and s["role"] in ("admin", "student")) or (admin_only and s["role"] != "admin"):
                self.j(403, {"error": "not allowed"})
                return None
            return s["space"], allowed_profiles(s)

        def space_get(self, u, s):
            scope = self.space_scope(s)
            if scope is None:
                return
            space, visible = scope
            cfg = self.cfg()
            if u.path != "/api/space/overview":
                return self.j(404, {"error": "not found"})
            addrs = addresses()
            labels = member_labels(space)
            pm = core.config().get("profile_mail") or {}
            profs, keep = [], getattr(core._PROFILE, "id", None)
            import untis_direct
            try:
                for pid in visible:
                    core.set_profile(pid)
                    expired, ends = core.parent_link_expiry()
                    profs.append({"id": pid, "label": labels.get(pid, pid), "untis": untis_direct.describe(),
                                  "synced_at": max((t for t, _ in core.load().values()), default=0),
                                  "links": serverconf.link_urls(cfg, addrs, core.parent_token()) if cfg["links"]["enabled"] else [],
                                  "expired": expired, "expires_at": ends, "mail_to": list(pm.get(pid) or [])})
            finally:
                core._PROFILE.id = keep
            people = []
            if s["role"] == "admin":
                people = [dict(name=a["name"], role=a["role"], disabled=a["disabled"], expires=a["expires"], profiles=a["profiles"], last_login=a["last_login"])
                          for a in auth.accounts_in(space)]
            lab = next((x["label"] for x in core.space_list() if x["id"] == space), space)
            return self.j(200, {"role": s["role"], "name": s["name"], "space": {"id": space, "label": lab}, "profiles": profs, "people": people,
                                "links_enabled": cfg["links"]["enabled"], "mail_enabled": cfg["mail"]["enabled"]})

        def untis_save(self, b, who, ip, strict=False):
            """Save a WebUntis key for the current profile and (optionally) test it -> response dict.
            strict (space accounts): the server must be a WebUntis cloud address - otherwise a student could make this machine contact any host."""
            import untis_direct
            if b.get("qr"):
                cfg = untis_direct.parse_qr(str(b["qr"]))
                if not cfg:
                    raise ValueError("That link doesn't work: " + untis_direct.explain_qr(str(b["qr"])))
            else:
                cfg = untis_direct.check_credentials(b.get("server"), b.get("school"), b.get("user"), b.get("key"))
            if strict and not WEBUNTIS_HOST.match(cfg["server"].lower()):
                raise ValueError("The WebUntis address must end in .webuntis.com (like xyz.webuntis.com). Another address has to be set by the server administrator.")
            untis_direct.save_credentials(cfg)
            core.save_config({**core.config(), "sync_mode": "direct"})
            auth.audit(auth.db(), who, "untis-key-set", f"{core.profile_id()}: {cfg['user']} @ {cfg['school']}", ip)
            if b.get("test"):
                try:
                    untis_direct.call(cfg, "getUserData2017")
                    return {"ok": True, "tested": True, "message": "Saved. WebUntis accepted the key."}
                except untis_direct.UntisError as e:
                    return {"ok": True, "tested": False, "message": f"Saved, but WebUntis refused it: {e}. " + untis_direct.login_hint(e)}
                except OSError as e:
                    return {"ok": True, "tested": False, "message": f"Saved, but this server can't reach {cfg['server']}: {e}"}
            return {"ok": True}

        def space_post(self, u, s):
            if u.path == "/api/password":                                       # everybody who signed in with a name may change their own password
                b = self.body()
                if not s or s.get("anon"):
                    return self.j(401, {"error": "not signed in"})
                if b is None:
                    return self.j(415, {"error": "json only"})
                try:
                    auth.change_own_password(s["name"], str(b.get("current", "")), str(b.get("new", "")), ip=self.ip())
                except auth.AuthError as e:
                    return self.j(400, {"error": str(e)})
                return self.j(200, {"changed": True}, self.cookie("", self.cfg(), clear=True))
            scope = self.space_scope(s)
            if scope is None:
                return
            space, visible = scope
            b = self.body()
            if b is None:
                return self.j(415, {"error": "json only"})
            who, cfg = s["name"], self.cfg()
            path = u.path[len("/api/space/"):]
            is_admin = s["role"] == "admin"
            keep = getattr(core._PROFILE, "id", None)
            try:
                if path in ("untis", "untis/clear", "sync", "link/rotate", "mail"):
                    pid = str(b.get("profile", ""))
                    if pid not in visible:                                       # a student's own profile, or one of the space's: never anyone else's
                        return self.j(403, {"error": "not your profile"})
                    core.set_profile(pid)
                    if path == "untis":
                        return self.j(200, self.untis_save(b, who, self.ip(), strict=True))
                    if path == "untis/clear":
                        import untis_direct
                        untis_direct.clear_credentials()
                        auth.audit(auth.db(), who, "untis-key-cleared", pid, self.ip())
                    elif path == "sync":
                        ssc.run_sync_bg(pid)
                    elif path == "link/rotate":
                        core.parent_token(rotate=True)
                        ssc.pcache_reset()
                        auth.audit(auth.db(), who, "link-rotated", pid, self.ip())
                    else:                                                        # mail: who gets this student's change mails (through the server's mail account)
                        to = b.get("to", [])
                        to = to if isinstance(to, list) else [x for x in re.split(r"[,;\s]+", str(to)) if x]
                        if len(to) > 5 or any(not serverconf.ADDR_RE.match(str(a)) or len(str(a)) > 254 for a in to):
                            raise ValueError("up to 5 valid e-mail addresses")
                        pm = dict(core.config().get("profile_mail") or {})
                        pm[pid] = [str(a).strip() for a in to]
                        core.save_config({**core.config(), "profile_mail": pm})
                        auth.audit(auth.db(), who, "profile-mail-set", pid, self.ip())
                    return self.j(200, {})
                if not is_admin:
                    return self.j(403, {"error": "your space administrator does that"})
                if path == "profiles":
                    act = str(b.get("action", ""))
                    if act == "add":
                        pid = core.profile_add("p" + secrets.token_hex(4), b.get("label"), space)
                    else:
                        pid = str(b.get("id", ""))
                        if pid not in core.profile_ids(space):
                            return self.j(403, {"error": "not in your space"})
                        if act == "rename":
                            core.profile_rename(pid, b.get("label"))
                        elif act == "remove":
                            import untis_direct
                            core.set_profile(pid)
                            untis_direct.clear_credentials()
                            core.profile_remove(pid)
                        else:
                            raise ValueError("unknown action")
                    auth.audit(auth.db(), who, "profile-" + act, pid, self.ip())
                    return self.j(200, {"id": pid})
                if path == "accounts":
                    pid = make_member(space, str(b.get("role", "")), str(b.get("name", "")).strip().lower(), str(b.get("password", "")), str(b.get("label", "")),
                                      b.get("profiles") or [], who)
                    return self.j(200, {"profile": pid})
                if path in ("accounts/delete", "accounts/password", "accounts/disable", "accounts/limits"):
                    name = str(b.get("name", ""))
                    target = next((a for a in auth.accounts_in(space) if a["name"] == name), None)
                    if not target:
                        return self.j(403, {"error": "not an account of your space"})
                    if name == who and path in ("accounts/delete", "accounts/disable"):
                        raise auth.AuthError("you can't do that to the account you are using")
                    if path == "accounts/delete":
                        auth.delete(name, who=who)
                        if target["role"] == "student" and b.get("delete_profile"):
                            import untis_direct
                            for pid in target["profiles"]:
                                if pid in core.profile_ids(space):
                                    core.set_profile(pid)
                                    untis_direct.clear_credentials()
                                    core.profile_remove(pid)
                    elif path == "accounts/password":
                        auth.set_password(name, str(b.get("password", "")), who=who)
                    elif path == "accounts/disable":
                        auth.set_disabled(name, bool(b.get("disabled")), who=who)
                    else:
                        if target["role"] == "student" and b.get("profiles") is not None:
                            raise auth.AuthError("a student always has exactly its own profile")
                        auth.set_limits(name, expires=b.get("expires"), sections=b.get("sections"), profiles=b.get("profiles"), who=who,
                                        all_sections=serverconf.SECTIONS, all_profiles=core.profile_ids(space))
                    return self.j(200, {})
                return self.j(404, {"error": "not found"})
            except (auth.AuthError, ValueError) as e:
                return self.j(400, {"error": str(e)})
            finally:
                core._PROFILE.id = keep

        # ---- admin API
        def admin_get(self, u, s):
            if not is_global(s):
                return self.j(403, {"error": "admin only"})
            cfg = self.cfg()
            if u.path == "/api/admin/status":
                addrs = addresses()
                state = core.state()
                links = {"enabled": cfg["links"]["enabled"], "long": cfg["links"]["long"],
                         "urls": serverconf.link_urls(cfg, addrs, core.parent_token()) if cfg["links"]["enabled"] else []}
                import untis_direct
                import autoupdate
                keep = core.profile_id()
                kids = []
                for p in core.profile_list():
                    core.set_profile(p["id"])
                    kids.append({"id": p["id"], "label": p["label"], "space": p["space"], "untis": untis_direct.describe()})
                core.set_profile(keep)
                expired, ends = core.parent_link_expiry()
                links["expires_at"], links["expired"] = ends, expired
                spaces = [dict(s_, profiles=len(core.profile_ids(s_["id"])), accounts=len(auth.accounts_in(s_["id"]))) for s_ in core.space_list()]
                return self.j(200, {"profiles": kids, "spaces": spaces, "autoupdate": autoupdate.last(), "version": VERSION, "uptime": int(time.time() - START), "addresses": addrs,
                                    "urls": serverconf.server_urls(cfg, addrs), "hostname": cfg["network"]["hostname"], "links": links,
                                    "accounts": auth.accounts(), "untis": untis_direct.describe(), "synced_at": state["synced_at"],
                                    "apply": apply_result(), "sync": ssc.SYNC,
                                    "wifi_card": _wifi_card(), "console": console_info()})
            if u.path == "/api/admin/config":
                return self.j(200, cfg)
            if u.path == "/api/admin/audit":
                return self.j(200, auth.audit_log())
            if u.path == "/api/admin/system":
                import sysinfo
                import schedule
                import tls
                import wake
                root = serverconf.system_root()
                return self.j(200, dict(sysinfo.info(root), tls=dict(tls.describe(root), serving=TLS["on"]), rtc=schedule.has_rtc(root), can_suspend=wake.can_suspend(root),
                                        wol={"enabled": cfg["power"]["wol"], "own": wake.own_macs(root), "devices": cfg["wol"]["devices"]}, waketest=wake.last_test()))
            if u.path == "/api/admin/health":
                import health
                return self.j(200, {"alerts": collect_alerts(cfg), "scan": health.read_scan(), "enabled": cfg["health"]["enabled"]})
            if u.path == "/api/admin/mail":
                import mailer
                return self.j(200, {"password_set": mailer.has_password(), "mail": cfg["mail"]})
            if u.path == "/api/admin/logs":
                import sysinfo
                from urllib.parse import parse_qs
                q = parse_qs(u.query)
                try:
                    return self.j(200, {"unit": q.get("unit", ["web"])[0], "text": sysinfo.journal(q.get("unit", ["web"])[0], int(q.get("n", ["100"])[0]))})
                except ValueError:
                    return self.j(400, {"error": "unknown log"})
            if u.path == "/api/admin/sessions":
                return self.j(200, auth.list_sessions())
            if u.path == "/api/admin/backup":
                import datetime
                return self.j(200, {"ssc_backup": 1, "version": VERSION, "created": datetime.datetime.now().isoformat(timespec="seconds"),
                                    "note": "Settings only: no passwords, no accounts, no WebUntis key.", "settings": cfg})
            return self.j(404, {"error": "not found"})

        def admin_post(self, u):
            s = self.sess()
            if not is_global(s):
                return self.j(403, {"error": "admin only"})
            b = self.body()
            if b is None:
                return self.j(415, {"error": "json only"})
            who = s["name"]
            cfg_now = self.cfg
            if b.get("profile") in core.profile_ids():           # the WebUntis key / sync of one child
                core.set_profile(b["profile"])
            try:
                if u.path == "/api/admin/config":
                    old = self.cfg()
                    new = serverconf.save(serverconf.merge(json.loads(json.dumps(old)), b))
                    parts = changed_parts(old, new)
                    sync_core_settings(new)
                    if new["links"]["long"] != old["links"]["long"]:
                        core.parent_token(rotate=True)       # a new, long link replaces the short one
                    if parts:
                        request_apply(parts)
                    auth.audit(auth.db(), who, "settings-changed", ",".join(parts) or "no system change", self.ip())
                    return self.j(200, {"config": new, "apply": parts})
                if u.path == "/api/admin/waketest":
                    import schedule
                    import wake
                    root = serverconf.system_root()
                    if not schedule.has_rtc(root):
                        raise ValueError("this machine has no hardware clock (RTC) to wake it up")
                    if not wake.can_suspend(root):
                        raise ValueError("this machine can't suspend to memory - use the 'quiet' sleep mode")
                    request_apply(["waketest"])
                    auth.audit(auth.db(), who, "wake-test", "", self.ip())
                    return self.j(200, {"requested": "waketest", "seconds": wake.TEST_SECONDS})
                if u.path == "/api/admin/wol":
                    import wake
                    dev = next((d for d in cfg_now()["wol"]["devices"] if d["name"] == str(b.get("name", ""))), None)
                    if not dev:
                        raise ValueError("choose one of the saved devices")
                    sent = wake.send(dev["mac"], wake.broadcasts([a for _, a in addresses()]))
                    auth.audit(auth.db(), who, "wake-on-lan", dev["name"], self.ip())
                    return self.j(200, {"sent": sent, "device": dev["name"]})
                if u.path == "/api/admin/mail/password":
                    import mailer
                    mailer.set_password(str(b.get("password", "")))
                    auth.audit(auth.db(), who, "mail-password-" + ("set" if b.get("password") else "cleared"), "", self.ip())
                    return self.j(200, {"password_set": mailer.has_password()})
                if u.path == "/api/admin/mail/test":
                    import mailer
                    try:
                        mailer.send_test(cfg_now())
                    except mailer.MailError as e:
                        raise ValueError(str(e))
                    auth.audit(auth.db(), who, "mail-test", "", self.ip())
                    return self.j(200, {"sent": True})
                if u.path == "/api/admin/accounts/limits":
                    auth.set_limits(str(b.get("name", "")), expires=b.get("expires"), sections=b.get("sections"), profiles=b.get("profiles"), who=who,
                                    all_sections=serverconf.SECTIONS, all_profiles=core.profile_ids())
                    return self.j(200, {})
                if u.path == "/api/admin/profiles":
                    act = str(b.get("action", ""))
                    if act == "add":
                        pid = core.profile_add(b.get("id"), b.get("label"), str(b.get("space", "")))
                    elif act == "rename":
                        core.profile_rename(str(b.get("id", "")), b.get("label")); pid = b.get("id")
                    elif act == "space":
                        core.profile_set_space(str(b.get("id", "")), str(b.get("space", "")))
                        pid = b.get("id")
                    elif act == "remove":
                        pid = str(b.get("id", ""))
                        import untis_direct
                        core.set_profile(pid)
                        untis_direct.clear_credentials()
                        core.profile_remove(pid)
                    else:
                        raise ValueError("unknown action")
                    auth.audit(auth.db(), who, "profile-" + act, str(pid), self.ip())
                    return self.j(200, {"profiles": core.profile_list()})
                if u.path in ("/api/admin/power", "/api/admin/service"):
                    import sysinfo
                    if u.path.endswith("power"):
                        action = str(b.get("action", ""))
                        if action == "poweroff_wake":
                            import applycfg
                            import schedule
                            spec = str(b.get("wake", "")).strip()
                            spec = "+" + spec if spec.isdigit() else spec
                            if not schedule.has_rtc(serverconf.system_root()):
                                raise ValueError("this machine has no hardware clock (RTC) to switch it on again")
                            applycfg.wake_epoch(spec)                      # validates the time
                            part = "poweroff-wake:" + spec
                            request_apply([part])
                            auth.audit(auth.db(), who, "power-off-and-wake", spec, self.ip())
                            return self.j(200, {"requested": part})
                        if action not in ("reboot", "poweroff"):
                            raise ValueError("action must be reboot, poweroff or poweroff_wake")
                        request_apply([action])
                        auth.audit(auth.db(), who, "power-" + action, "", self.ip())
                        return self.j(200, {"requested": action})
                    name = str(b.get("name", ""))
                    if name not in sysinfo.RESTARTABLE or b.get("action") != "restart":
                        raise ValueError("only these can be restarted: " + ", ".join(sysinfo.RESTARTABLE))
                    request_apply(["restart:" + name])
                    auth.audit(auth.db(), who, "service-restart", name, self.ip())
                    return self.j(200, {"requested": name})
                if u.path == "/api/admin/restore":
                    old = self.cfg()
                    data = b.get("settings")
                    if not isinstance(data, dict):
                        raise ValueError("that is not a settings backup")
                    new = serverconf.save(serverconf.merge(json.loads(json.dumps(serverconf.DEFAULTS)), data))
                    parts = changed_parts(old, new)
                    sync_core_settings(new)
                    if new["links"]["long"] != old["links"]["long"]:
                        core.parent_token(rotate=True)
                    if parts:
                        request_apply(parts)
                    auth.audit(auth.db(), who, "settings-restored", ",".join(parts) or "no system change", self.ip())
                    return self.j(200, {"config": new, "apply": parts})
                if u.path == "/api/admin/spaces":
                    act = str(b.get("action", ""))
                    sid = str(b.get("id", "")).strip().lower()
                    if act == "add":
                        sid = core.space_add(sid, b.get("label"))
                    elif act == "rename":
                        core.space_rename(sid, b.get("label"))
                    elif act == "remove":
                        core.space_remove(sid, accounts=len(auth.accounts_in(sid)))
                    else:
                        raise ValueError("unknown action")
                    auth.audit(auth.db(), who, "space-" + act, sid, self.ip())
                    return self.j(200, {"spaces": core.space_list()})
                if u.path == "/api/admin/spaces/member":
                    space = str(b.get("space", ""))
                    if space not in core.space_ids():
                        raise ValueError("no such space")
                    pid = make_member(space, str(b.get("role", "")), str(b.get("name", "")).strip().lower(), str(b.get("password", "")), str(b.get("label", "")),
                                      b.get("profiles") or [], who)
                    return self.j(200, {"profile": pid})
                if u.path == "/api/admin/accounts/space":
                    if b.get("name") == who and b.get("space"):
                        raise auth.AuthError("you can't move the account you are using into a space")
                    auth.set_space(str(b.get("name", "")), str(b.get("space", "")), who=who)
                    return self.j(200, {})
                if u.path == "/api/admin/accounts/signout":
                    name = str(b.get("name", ""))
                    n = len([1 for v in auth.list_sessions() if v["name"] == name])
                    auth.drop_sessions(name)
                    auth.audit(auth.db(), who, "signed-out", f"{name} ({n} session(s))", self.ip())
                    return self.j(200, {"closed": n})
                if u.path == "/api/admin/wifi":
                    old = self.cfg()
                    cfg = json.loads(json.dumps(old))
                    w = cfg["wifi"]
                    w["enabled"] = bool(b.get("enabled", True))
                    if w["enabled"]:
                        ssid = str(b.get("ssid", w["ssid"]))
                        w.update(ssid=ssid, open=bool(b.get("open")), hidden=bool(b.get("hidden")))
                        if b.get("country") is not None:
                            w["country"] = str(b.get("country", ""))
                        pw = str(b.get("passphrase", ""))
                        if not w["open"]:
                            if pw:
                                serverconf.stage_wifi_psk(ssid, serverconf.derive_psk(ssid, pw))
                            elif old["wifi"]["ssid"] != ssid or old["wifi"]["open"]:
                                raise ValueError("enter the Wi-Fi password for this network")
                    new = serverconf.save(cfg)
                    request_apply(["network"])
                    auth.audit(auth.db(), who, "wifi-changed", new["wifi"]["ssid"] if new["wifi"]["enabled"] else "off", self.ip())
                    return self.j(200, {"config": new, "apply": ["network"]})
                if u.path == "/api/admin/accounts":
                    auth.create(str(b.get("name", "")).strip().lower(), str(b.get("password", "")), str(b.get("role", "")), who=who)
                elif u.path == "/api/admin/accounts/delete":
                    if b.get("name") == who:
                        raise auth.AuthError("you can't delete the account you are using")
                    auth.delete(str(b.get("name", "")), who=who)
                elif u.path == "/api/admin/accounts/password":
                    auth.set_password(str(b.get("name", "")), str(b.get("password", "")), who=who)
                elif u.path == "/api/admin/accounts/role":
                    auth.set_role(str(b.get("name", "")), str(b.get("role", "")), who=who)
                elif u.path == "/api/admin/accounts/disable":
                    if b.get("name") == who and b.get("disabled"):
                        raise auth.AuthError("you can't disable the account you are using")
                    auth.set_disabled(str(b.get("name", "")), bool(b.get("disabled")), who=who)
                elif u.path == "/api/admin/untis":
                    import untis_direct
                    if b.get("qr"):
                        cfg = untis_direct.parse_qr(str(b["qr"]))
                        if not cfg:
                            raise ValueError("That link doesn't work: " + untis_direct.explain_qr(str(b["qr"])))
                    else:
                        cfg = untis_direct.check_credentials(b.get("server"), b.get("school"), b.get("user"), b.get("key"))
                    untis_direct.save_credentials(cfg)
                    core.save_config({**core.config(), "sync_mode": "direct"})
                    auth.audit(auth.db(), who, "untis-key-set", cfg["user"] + " @ " + cfg["school"], self.ip())
                    if b.get("test"):                          # "save and test": say right away whether WebUntis accepts it
                        try:
                            untis_direct.call(cfg, "getUserData2017")
                            return self.j(200, {"ok": True, "tested": True, "message": "Saved. WebUntis accepted the key."})
                        except untis_direct.UntisError as e:
                            return self.j(200, {"ok": True, "tested": False, "message": f"Saved, but WebUntis refused it: {e}. " + untis_direct.login_hint(e)})
                        except OSError as e:
                            return self.j(200, {"ok": True, "tested": False, "message": f"Saved, but this server can't reach {cfg['server']}: {e}"})
                elif u.path == "/api/admin/untis/clear":
                    import untis_direct
                    untis_direct.clear_credentials()
                elif u.path == "/api/admin/sync":
                    ssc.run_sync_bg(b.get("profile") if b.get("profile") in core.profile_ids() else None)
                elif u.path == "/api/admin/links/rotate":
                    core.parent_token(rotate=True)
                    auth.audit(auth.db(), who, "link-rotated", "", self.ip())
                else:
                    return self.j(404, {"error": "not found"})
            except (auth.AuthError, ValueError) as e:
                return self.j(400, {"error": str(e)})
            return self.j(200, {})
    return SH


def _log(msg):
    print(msg, flush=True)


def _stale_profiles(cfg, now):
    import untis_direct
    keep = getattr(core._PROFILE, "id", None)
    due = []
    try:
        for pid in core.profile_ids():
            core.set_profile(pid)
            if untis_direct.load_credentials():
                last = max((t for t, _ in core.load().values()), default=0)
                if now - last > cfg["sync"]["minutes"] * 60:
                    due.append(pid)
    finally:
        core._PROFILE.id = keep
    return due


def tick(state, now=None, wall=None):
    """One pass of the background work (every 30 s): sync, daily mail, health alerts, automatic update. `state` keeps
    when things were last tried. Every part is on its own: one failing must not stop the others."""
    import autoupdate
    import mailer
    now = now or time.time()
    wall = wall or __import__("datetime").datetime.now()
    cfg = serverconf.load()
    try:
        if not ssc.SYNC["running"] and _stale_profiles(cfg, now):
            ssc.run_sync_bg()
    except Exception as e:
        _log("sync check failed: " + str(e))
    try:
        if mailer.digest_due(cfg, wall) and now - state.get("digest_try", 0) > 1800:
            state["digest_try"] = now
            mailer.send_digest(cfg, wall)
    except Exception as e:
        _log("daily mail failed: " + str(e))
    try:
        if cfg["health"]["enabled"] and now - state.get("health", 0) > 600:
            state["health"] = now
            fresh = __import__("health").new_alerts(collect_alerts(cfg), now)
            if fresh:
                _log("health: " + "; ".join(a["text"] for a in fresh))
                mailer.send_alerts(cfg, fresh)
    except Exception as e:
        _log("health check failed: " + str(e))
    try:
        if autoupdate.due(cfg, wall):
            import updater
            _log("automatic update: " + autoupdate.run(cfg, updater.check, request_apply, wall))
    except Exception as e:
        _log("automatic update failed: " + str(e))


def _sync_loop(stop):
    state = {}
    while not stop.wait(30):
        tick(state)


def build(cfg=None):
    cfg = cfg or serverconf.load()
    srv = ThreadingHTTPServer((cfg["web"]["bind"], cfg["web"]["port"]), make_handler())
    TLS["on"] = False
    if cfg["web"]["https"]:
        tls = serverconf.system_root() / "etc/ssc/tls"
        try:
            ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            ctx.minimum_version = ssl.TLSVersion.TLSv1_2
            ctx.load_cert_chain(tls / "cert.pem", tls / "key.pem")
            srv.socket = ctx.wrap_socket(srv.socket, server_side=True)
            TLS["on"] = True
        except (OSError, ssl.SSLError) as e:        # no certificate yet: plain HTTP is better than no server at all (the admin can fix it)
            print("HTTPS is on in the settings, but the certificate can't be used (" + str(e) + ") - serving plain HTTP", flush=True)
    return srv


def run():
    os.environ.setdefault("SSC_VAULT", "file")
    cfg = serverconf.load()
    sync_core_settings(cfg)
    core.db()
    srv = build(cfg)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    links = None
    if cfg["links"]["enabled"]:
        try:
            links = ThreadingHTTPServer((cfg["web"]["bind"], cfg["links"]["port"]), ssc.make_parent_handler())
            threading.Thread(target=links.serve_forever, daemon=True).start()
        except OSError as e:
            print("Parent link server not started:", e, flush=True)
    stop = threading.Event()
    threading.Thread(target=_sync_loop, args=(stop,), daemon=True).start()
    print(f"SSC Server {VERSION} on {cfg['web']['bind']}:{cfg['web']['port']} ({'https' if TLS['on'] else 'http'})", flush=True)
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        stop.set()
