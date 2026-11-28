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


def allowed_profiles(s):
    """The profile ids this session may look at (an administrator: all; a limited account: its list; the shared guest: the main one)."""
    ids = core.profile_ids()
    if not s or s.get("anon"):
        return ids[:1]
    if s["role"] == "admin":
        return ids
    own = auth.limits_for(s["name"])["profiles"]
    return [p for p in ids if p in own] if own else ids


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
    return health.alerts(info, health.read_scan(), cfg["health"], synced_at=min(lasts) if lasts else 0, sync_minutes=cfg["sync"]["minutes"], has_key=has_key)


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

        def authed(self):  # used by the inherited full-app API: admins only
            s = self.sess()
            return bool(s and s["role"] == "admin")

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
            self.enter(s)
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
                if s["role"] == "admin":
                    return self.html(200, self.chrome(_page("index.html", classic=core.ui_choice(self.headers.get("Cookie", "")) == "classic"), s, True))
                return self.html(200, self.chrome(_page("parent.html", classic=core.ui_choice(self.headers.get("Cookie", "")) == "classic"), s, False))
            if u.path == "/admin":
                if not (s and s["role"] == "admin"):
                    return self.redirect("/")
                return self.html(200, _page("admin.html"))
            if u.path == "/data.json":
                if not s:
                    return self.j(401, {"error": "sign in first"})
                cfg = self.cfg()
                secs = cfg["sections"]["user" if s["role"] != "guest" else "guest"] if s["role"] != "admin" else list(serverconf.SECTIONS)
                own = [] if s.get("anon") or s["role"] == "admin" else auth.limits_for(s["name"])["sections"]
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
            if u.path == "/api/update/check":
                if not (s and s["role"] == "admin"):
                    return self.j(403, {"error": "admin only"})
                import updater
                try:
                    return self.j(200, dict(updater.check(), flavor="server", apply=apply_result()))
                except updater.UpdateError as e:
                    return self.j(502, {"error": str(e), "current": VERSION, "flavor": "server"})
            if u.path == "/api/me":
                return self.j(200, {"name": s["name"], "role": s["role"]}) if s else self.j(401, {"error": "not signed in"})
            if u.path.startswith("/api/admin/"):
                return self.admin_get(u, s)
            return super().do_GET()

        def chrome(self, page, s, admin):
            import html as _html
            allowed = allowed_profiles(s)
            labels = {p["id"]: p["label"] for p in core.profile_list()}
            pick = ""
            if len(allowed) > 1:                                     # several children: a switcher (the choice is a cookie, checked on every request)
                opts = "".join(f"<option value='{_html.escape(p)}'{' selected' if p == core.profile_id() else ''}>{_html.escape(labels.get(p, p))}</option>" for p in allowed)
                pick = ("<select aria-label='Child' translate='no' style='font:13px system-ui;border-radius:99px;border:0;padding:3px 8px' "
                        "onchange=\"document.cookie='ssc_profile='+this.value+'; Path=/; SameSite=Strict';location.reload()\">" + opts + "</select>")
            bar = ("<div style='position:fixed;z-index:99;right:12px;bottom:12px;font:13px system-ui;background:rgba(20,24,38,.88);color:#fff;"
                   "padding:7px 12px;border-radius:99px;display:flex;gap:12px;align-items:center'>" + pick +
                   f"<span>{'Admin' if admin else s['role'].title()}: {re.sub('[<>&]', '', s['name'])}</span>"
                   + ("<a style='color:#9db4ff' href='/admin'>Admin panel</a>" if admin else "")
                   + "<a style='color:#9db4ff' href='#' onclick=\"fetch('/api/logout',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'}).then(()=>location.href='/')\">Log out</a></div>")
            return page.replace("</body>", bar + "</body>", 1)

        # ---- POST
        def do_POST(self):
            u = urlparse(self.path)
            self.enter(self.sess())
            if u.path == "/api/login":
                return self.login()
            if u.path == "/api/update/install":
                s = self.sess()
                if not (s and s["role"] == "admin"):
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
            if u.path == "/api/prefs":
                s = self.sess()
                b = self.body()
                if not s:
                    return self.j(401, {"error": "not signed in"})
                if b is None:
                    return self.j(415, {"error": "json only"})
                if s.get("anon"):                      # the shared passwordless guest has nobody's settings to keep
                    return self.j(200, {"saved": False})
                return self.j(200, {"saved": True, "prefs": auth.set_prefs(s["name"], b)})
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
            return self.j(200, {"role": role}, self.cookie(sid, cfg))

        # ---- admin API
        def admin_get(self, u, s):
            if not (s and s["role"] == "admin"):
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
                    kids.append({"id": p["id"], "label": p["label"], "untis": untis_direct.describe()})
                core.set_profile(keep)
                expired, ends = core.parent_link_expiry()
                links["expires_at"], links["expired"] = ends, expired
                return self.j(200, {"profiles": kids, "autoupdate": autoupdate.last(), "version": VERSION, "uptime": int(time.time() - START), "addresses": addrs,
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
            if not (s and s["role"] == "admin"):
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
                        pid = core.profile_add(b.get("id"), b.get("label"))
                    elif act == "rename":
                        core.profile_rename(str(b.get("id", "")), b.get("label")); pid = b.get("id")
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
