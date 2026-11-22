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

        def cfg(self):
            return serverconf.load()

        def cookie(self, sid, cfg, clear=False):
            age = 0 if clear else cfg["web"]["session_hours"] * 3600
            return {"Set-Cookie": f"ssc_sess={'' if clear else sid}; HttpOnly; SameSite=Strict; Path=/; Max-Age={age}" + ("; Secure" if cfg["web"]["https"] else "")}

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
            if u.path == "/health":
                return self.j(200, {"ok": True, "service": "ssc-server", "version": VERSION})
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
                return self.send(200, json.dumps(core.parent_state(sections=secs)), "application/json")
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
            bar = ("<div style='position:fixed;z-index:99;right:12px;bottom:12px;font:13px system-ui;background:rgba(20,24,38,.88);color:#fff;"
                   "padding:7px 12px;border-radius:99px;display:flex;gap:12px;align-items:center'>"
                   f"<span>{'Admin' if admin else s['role'].title()}: {re.sub('[<>&]', '', s['name'])}</span>"
                   + ("<a style='color:#9db4ff' href='/admin'>Admin panel</a>" if admin else "")
                   + "<a style='color:#9db4ff' href='#' onclick=\"fetch('/api/logout',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'}).then(()=>location.href='/')\">Log out</a></div>")
            return page.replace("</body>", bar + "</body>", 1)

        # ---- POST
        def do_POST(self):
            u = urlparse(self.path)
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
                request_apply(["update"])
                auth.audit(auth.db(), s["name"], "update-requested", info["latest"], self.ip())
                return self.j(200, {"requested": True, "from": VERSION, "to": info["latest"]})
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
                return self.j(200, {"version": VERSION, "uptime": int(time.time() - START), "addresses": addrs,
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
                return self.j(200, sysinfo.info(serverconf.system_root()))
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
            try:
                if u.path == "/api/admin/config":
                    old = self.cfg()
                    new = serverconf.save(serverconf.merge(json.loads(json.dumps(old)), b))
                    parts = changed_parts(old, new)
                    if new["links"]["long"] != old["links"]["long"]:
                        core.save_config({**core.config(), "links_long": new["links"]["long"]})
                        core.parent_token(rotate=True)       # a new, long link replaces the short one
                    if parts:
                        request_apply(parts)
                    auth.audit(auth.db(), who, "settings-changed", ",".join(parts) or "no system change", self.ip())
                    return self.j(200, {"config": new, "apply": parts})
                if u.path in ("/api/admin/power", "/api/admin/service"):
                    import sysinfo
                    if u.path.endswith("power"):
                        action = str(b.get("action", ""))
                        if action not in ("reboot", "poweroff"):
                            raise ValueError("action must be reboot or poweroff")
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
                    if new["links"]["long"] != old["links"]["long"]:
                        core.save_config({**core.config(), "links_long": new["links"]["long"]})
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
                    ssc.run_sync_bg()
                elif u.path == "/api/admin/links/rotate":
                    core.parent_token(rotate=True)
                    auth.audit(auth.db(), who, "link-rotated", "", self.ip())
                else:
                    return self.j(404, {"error": "not found"})
            except (auth.AuthError, ValueError) as e:
                return self.j(400, {"error": str(e)})
            return self.j(200, {})
    return SH


def _sync_loop(stop):
    import untis_direct
    while not stop.wait(30):
        try:
            cfg = serverconf.load()
            last = max((t for t, _ in core.load().values()), default=0)
            if untis_direct.load_credentials() and time.time() - last > cfg["sync"]["minutes"] * 60 and not ssc.SYNC["running"]:
                ssc.run_sync_bg()
        except Exception:
            pass


def build(cfg=None):
    cfg = cfg or serverconf.load()
    srv = ThreadingHTTPServer((cfg["web"]["bind"], cfg["web"]["port"]), make_handler())
    if cfg["web"]["https"]:
        tls = serverconf.system_root() / "etc/ssc/tls"
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.minimum_version = ssl.TLSVersion.TLSv1_2
        ctx.load_cert_chain(tls / "cert.pem", tls / "key.pem")
        srv.socket = ctx.wrap_socket(srv.socket, server_side=True)
    return srv


def run():
    os.environ.setdefault("SSC_VAULT", "file")
    cfg = serverconf.load()
    core.save_config({**core.config(), "sync_mode": "direct", "links_long": cfg["links"]["long"]})
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
    print(f"SSC Server {VERSION} on {cfg['web']['bind']}:{cfg['web']['port']} ({'https' if cfg['web']['https'] else 'http'})", flush=True)
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        stop.set()
