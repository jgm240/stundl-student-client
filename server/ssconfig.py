#!/usr/bin/env python3
"""ssconfig - configure an SSC Server from the console.

  ssconfig                      menu (whiptail)
  ssconfig showip               the server's IP addresses and web address
  ssconfig showlink             the secret Parent link (and QR code if `qrencode` exists)
  ssconfig status               summary
  ssconfig get [setting]        show settings          e.g.  ssconfig get network
  ssconfig set <setting> <val>  change one setting     e.g.  ssconfig set links.long true
                                                             ssconfig set network.mode static
  ssconfig ip <addr/len> <gateway> [dns,dns]   fixed IP address       ssconfig dhcp   automatic address
  ssconfig account add|list|passwd|role|disable|enable|delete ...
  ssconfig untis                set the WebUntis (Untis Mobile) key from a QR text
  ssconfig sync                 fetch data now
  ssconfig rotate-link          make a new Parent link
  ssconfig apply [parts]        write system files (network ssh firewall web)
  ssconfig first-boot           first-run wizard (creates the admin account)
"""
import getpass
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))
os.environ.setdefault("SSC_VAULT", "file")
os.environ.setdefault("SSC_DATA_DIR", "/var/lib/ssc")

import auth  # noqa: E402
import core  # noqa: E402
import serverconf  # noqa: E402

PARTS_FOR = {"network": "network", "ssh": "ssh", "firewall": "firewall", "web": "web", "links": "links"}


def addresses():
    try:
        r = subprocess.run(["ip", "-j", "-4", "addr"], capture_output=True, text=True, timeout=5)
        a = serverconf.parse_ip_json(r.stdout) if r.returncode == 0 else []
    except (OSError, ValueError, subprocess.SubprocessError):
        a = []
    return a


def fix_owner():
    """ssconfig runs as root; the service runs as 'ssc' and must still be able to use these files."""
    if os.geteuid() != 0:
        return
    try:
        import pwd
        u = pwd.getpwnam("ssc")
    except (ImportError, KeyError):
        return
    root = serverconf.state_dir()
    for p in [root, *root.rglob("*")]:
        try:
            os.chown(p, u.pw_uid, u.pw_gid)
        except OSError:
            pass


def apply_parts(parts):
    if not parts:
        return
    import applycfg
    if os.geteuid() != 0 and not os.environ.get("SSC_ROOT"):
        import serverapp
        serverapp.request_apply(parts)
        print("Not root: change requested; it is applied in a few seconds.")
        return
    res = applycfg.apply(parts)
    for d in res["done"]:
        print("  ok:", d)
    for f in res["failed"]:
        print("  FAILED:", f)


def changed(old, new):
    return [k for k in ("network", "ssh", "firewall", "web", "links") if old[k] != new[k]] + \
           (["firewall"] if (old["web"]["port"], old["links"]) != (new["web"]["port"], new["links"]) else [])


def set_setting(path, value):
    old = serverconf.load()
    if value.lower() in ("true", "false"):
        value = value.lower() == "true"
    elif value.isdigit():
        value = int(value)
    elif path in ("network.dns", "ssh.allow_users", "sections.guest", "sections.user"):
        value = [x for x in value.replace(",", " ").split() if x]
    new = serverconf.update(path, value)
    if new["links"]["long"] != old["links"]["long"]:
        core.save_config({**core.config(), "links_long": new["links"]["long"]})
        core.parent_token(rotate=True)
        print("Parent link renewed - see `ssconfig showlink`.")
    fix_owner()
    apply_parts(sorted(set(changed(old, new))))
    return new


# ------------------------------------------------------------------ show commands
def cmd_showip(_=None):
    cfg = serverconf.load()
    addrs = addresses()
    if not addrs:
        print("No network address yet. Check the cable, or run:  ssconfig dhcp")
        return 1
    urls = serverconf.server_urls(cfg, addrs)
    for (ifname, a), u in zip(addrs, urls):
        print(f"{ifname:<10} {a:<20} {u}")
    return 0


def qr(text):
    if shutil.which("qrencode"):
        subprocess.run(["qrencode", "-t", "ANSIUTF8", text])


def cmd_showlink(args=None):
    cfg = serverconf.load()
    if not cfg["links"]["enabled"]:
        print("Parent links are switched off.  Turn on:  ssconfig set links.enabled true")
        return 1
    addrs = addresses()
    if not addrs:
        print("No network address yet.")
        return 1
    tok = core.parent_token()
    fix_owner()
    links = serverconf.link_urls(cfg, addrs, tok)
    for l in links:
        print("Browser:      ", l["browser"])
        print("Wall display: ", l["wall"])
    if "--qr" in (args or []) or sys.stdout.isatty():
        qr(links[0]["browser"])
    print("\nThis link is a secret: anyone with it can see the Parent view. `ssconfig rotate-link` replaces it.")
    return 0


def cmd_banner(_=None):
    """Text shown above the login prompt (written at boot by ssc-banner.service)."""
    cfg = serverconf.load()
    urls = serverconf.server_urls(cfg, addresses())
    lines = ["", "  SSC Server  -  " + cfg["network"]["hostname"],
             "  Web:  " + (", ".join(urls) if urls else "(waiting for a network address)"),
             "  Show the Parent link:   sudo ssconfig showlink      Settings:   sudo ssconfig", ""]
    root = serverconf.system_root()
    p = root / "etc/issue.d/20-ssc.issue"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("\n".join(lines) + "\n")
    return 0


def cmd_status(_=None):
    cfg = serverconf.load()
    print("SSC Server")
    print("  hostname :", cfg["network"]["hostname"])
    print("  network  :", cfg["network"]["mode"], cfg["network"]["address"] or "")
    print("  web      :", ("https" if cfg["web"]["https"] else "http"), "port", cfg["web"]["port"], "| bind", cfg["web"]["bind"])
    print("  links    :", "on" if cfg["links"]["enabled"] else "off", "port", cfg["links"]["port"], "| long links:", "required" if cfg["links"]["long"] else "no")
    print("  ssh      :", "on" if cfg["ssh"]["enabled"] else "off", "port", cfg["ssh"]["port"], "| root login:", "yes" if cfg["ssh"]["permit_root"] else "no",
          "| passwords:", "yes" if cfg["ssh"]["password_auth"] else "keys only")
    print("  firewall :", "on" if cfg["firewall"]["enabled"] else "off")
    acc = auth.accounts()
    print("  accounts :", ", ".join(f"{a['name']} ({a['role']}{', disabled' if a['disabled'] else ''})" for a in acc) or "none - run `ssconfig first-boot`")
    try:
        import untis_direct
        print("  WebUntis :", untis_direct.describe())
    except Exception:
        pass
    cmd_showip()


# ------------------------------------------------------------------ accounts
def ask_password(prompt="Password: "):
    p1 = getpass.getpass(prompt)
    if p1 != getpass.getpass("Again: "):
        raise auth.AuthError("the two passwords differ")
    return p1


def cmd_account(args):
    if not args:
        print("usage: ssconfig account add <name> <guest|user|admin> | list | passwd <name> | role <name> <role> | disable|enable|delete <name>")
        return 2
    sub, rest = args[0], args[1:]
    try:
        if sub == "list":
            for a in auth.accounts():
                print(f"{a['name']:<20}{a['role']:<8}{'disabled' if a['disabled'] else ''}")
        elif sub == "add" and len(rest) >= 2:
            auth.create(rest[0].lower(), os.environ.get("SSC_NEW_PASSWORD") or ask_password(), rest[1])
            print("Created.")
        elif sub == "passwd" and rest:
            auth.set_password(rest[0], os.environ.get("SSC_NEW_PASSWORD") or ask_password("New password: "))
            print("Changed. That account was signed out.")
        elif sub == "role" and len(rest) == 2:
            auth.set_role(rest[0], rest[1])
        elif sub in ("disable", "enable") and rest:
            auth.set_disabled(rest[0], sub == "disable")
        elif sub == "delete" and rest:
            auth.delete(rest[0])
        else:
            return cmd_account([])
    except auth.AuthError as e:
        print("Error:", e)
        return 1
    fix_owner()
    return 0


def cmd_untis(_=None):
    import untis_direct
    print("Paste the Untis Mobile QR text (untis://setschool?...). In WebUntis: Profile > Access > Untis Mobile > show QR.")
    cfg = untis_direct.parse_qr(input("> ").strip())
    if not cfg:
        print("That doesn't look like an Untis Mobile QR text.")
        return 1
    untis_direct.save_credentials(cfg)
    core.save_config({**core.config(), "sync_mode": "direct"})
    fix_owner()
    print("Saved for", cfg["user"], "at", cfg["school"])
    return 0


def cmd_first_boot(_=None):
    print("SSC Server setup\n")
    if not auth.has_admin():
        print("Create the administrator account.")
        while True:
            try:
                name = input("Admin name [admin]: ").strip().lower() or "admin"
                auth.create(name, ask_password("Admin password (12+ characters): "), "admin")
                break
            except auth.AuthError as e:
                print("  ", e)
    fix_owner()
    host = input(f"Hostname [{serverconf.load()['network']['hostname']}]: ").strip()
    if host:
        set_setting("network.hostname", host)
    if input("Require long Parent links? [y/N] ").strip().lower().startswith("y"):
        set_setting("links.long", "true")
    print("\nDone. Anything else:  ssconfig")
    cmd_showip()
    return 0


# ------------------------------------------------------------------ whiptail menu
def wt(*args):
    r = subprocess.run(["whiptail", *args], stderr=subprocess.PIPE, text=True)
    return r.returncode, r.stderr.strip()


def menu():
    if not shutil.which("whiptail"):
        print(__doc__)
        return 0
    while True:
        rc, ch = wt("--title", "SSC Server", "--menu", "Configure this server", "20", "70", "11",
                    "ip", "Show IP address and web address", "link", "Show the Parent link", "net", "Network (DHCP / fixed IP)",
                    "web", "Web port, HTTPS, long links", "acc", "Accounts", "ssh", "SSH settings", "untis", "WebUntis key",
                    "sync", "Fetch data now", "rot", "New Parent link", "status", "Status", "quit", "Leave")
        if rc != 0 or ch == "quit":
            return 0
        try:
            if ch == "ip":
                show("IP addresses", capture(cmd_showip))
            elif ch == "link":
                show("Parent link", capture(cmd_showlink, ["--noqr"]))
            elif ch == "status":
                show("Status", capture(cmd_status))
            elif ch == "net":
                menu_net()
            elif ch == "web":
                menu_web()
            elif ch == "ssh":
                menu_ssh()
            elif ch == "acc":
                menu_acc()
            elif ch == "untis":
                rc, q = wt("--inputbox", "Paste the Untis Mobile QR text", "10", "74")
                if rc == 0:
                    import untis_direct
                    c = untis_direct.parse_qr(q)
                    if c:
                        untis_direct.save_credentials(c)
                        core.save_config({**core.config(), "sync_mode": "direct"})
                        fix_owner()
                        wt("--msgbox", f"Saved for {c['user']} at {c['school']}", "8", "50")
                    else:
                        wt("--msgbox", "That is not an Untis Mobile QR text.", "8", "50")
            elif ch == "sync":
                show("Sync", capture(lambda: subprocess.run([sys.executable, str(HERE.parent / "ssc.py"), "sync", "--direct"])))
            elif ch == "rot":
                core.parent_token(rotate=True)
                fix_owner()
                wt("--msgbox", "New Parent link created. The old one no longer works.", "8", "56")
        except (ValueError, auth.AuthError) as e:
            wt("--msgbox", "Error: " + str(e), "10", "70")


def capture(fn, *a):
    import contextlib
    import io
    b = io.StringIO()
    with contextlib.redirect_stdout(b):
        fn(*a)
    return b.getvalue() or "(nothing)"


def show(title, text):
    wt("--title", title, "--scrolltext", "--msgbox", text, "20", "78")


def menu_net():
    rc, ch = wt("--menu", "Network", "12", "60", "3", "dhcp", "Automatic address (DHCP)", "static", "Fixed IP address", "host", "Hostname")
    if rc != 0:
        return
    if ch == "dhcp":
        set_setting("network.mode", "dhcp")
    elif ch == "host":
        rc, v = wt("--inputbox", "Hostname", "8", "50", serverconf.load()["network"]["hostname"])
        if rc == 0:
            set_setting("network.hostname", v)
    else:
        n = serverconf.load()["network"]
        rc, a = wt("--inputbox", "Address with prefix, e.g. 192.168.1.50/24", "8", "60", n["address"])
        if rc != 0:
            return
        rc, g = wt("--inputbox", "Gateway (router)", "8", "60", n["gateway"])
        if rc != 0:
            return
        rc, d = wt("--inputbox", "DNS servers, separated by comma", "8", "60", ",".join(n["dns"]))
        if rc != 0:
            return
        cfg = serverconf.load()
        cfg["network"].update(mode="static", address=a, gateway=g, dns=d)
        old = serverconf.load()
        new = serverconf.save(cfg)
        fix_owner()
        apply_parts(changed(old, new))


def menu_web():
    cfg = serverconf.load()
    rc, ch = wt("--menu", "Web", "14", "64", "5", "port", f"Web port ({cfg['web']['port']})", "long", f"Require long link ({cfg['links']['long']})",
                "https", f"HTTPS ({cfg['web']['https']})", "lport", f"Link port ({cfg['links']['port']})", "guest", f"Guest without password ({cfg['web']['guest_passwordless']})")
    if rc != 0:
        return
    if ch in ("long", "https", "guest"):
        path = {"long": "links.long", "https": "web.https", "guest": "web.guest_passwordless"}[ch]
        cur = cfg["links"]["long"] if ch == "long" else cfg["web"]["https" if ch == "https" else "guest_passwordless"]
        set_setting(path, "false" if cur else "true")
    else:
        rc, v = wt("--inputbox", "Port number", "8", "40", str(cfg["web"]["port"] if ch == "port" else cfg["links"]["port"]))
        if rc == 0:
            set_setting("web.port" if ch == "port" else "links.port", v)


def menu_ssh():
    s = serverconf.load()["ssh"]
    rc, ch = wt("--menu", "SSH", "14", "64", "5", "on", f"Enabled ({s['enabled']})", "port", f"Port ({s['port']})", "root", f"Root login ({s['permit_root']})",
                "pw", f"Password login ({s['password_auth']})", "users", f"Allowed users ({' '.join(s['allow_users']) or 'everyone'})")
    if rc != 0:
        return
    m = {"on": ("ssh.enabled", s["enabled"]), "root": ("ssh.permit_root", s["permit_root"]), "pw": ("ssh.password_auth", s["password_auth"])}
    if ch in m:
        set_setting(m[ch][0], "false" if m[ch][1] else "true")
    else:
        rc, v = wt("--inputbox", "Value" if ch == "port" else "User names, separated by space (empty = everyone)", "8", "60",
                   str(s["port"]) if ch == "port" else " ".join(s["allow_users"]))
        if rc == 0:
            set_setting("ssh.port" if ch == "port" else "ssh.allow_users", v if ch == "port" else v or "")


def menu_acc():
    rows = []
    for a in auth.accounts():
        rows += [a["name"], a["role"] + (" (disabled)" if a["disabled"] else "")]
    rc, ch = wt("--menu", "Accounts - pick one, or add", "20", "64", "10", "+ add", "Add an account", *rows)
    if rc != 0:
        return
    if ch == "+ add":
        rc, name = wt("--inputbox", "Account name", "8", "50")
        rc2, role = wt("--menu", "Role", "12", "60", "3", "guest", "Parent view", "user", "Parent view (more sections)", "admin", "Full app and admin panel")
        rc3, pw = wt("--passwordbox", "Password", "8", "50")
        if rc == rc2 == rc3 == 0:
            auth.create(name.lower(), pw, role)
    else:
        rc, act = wt("--menu", ch, "14", "50", "4", "pw", "Change password", "role", "Change role", "off", "Disable / enable", "del", "Delete")
        if rc != 0:
            return
        if act == "pw":
            rc, pw = wt("--passwordbox", "New password", "8", "50")
            if rc == 0:
                auth.set_password(ch, pw)
        elif act == "role":
            rc, role = wt("--menu", "Role", "12", "50", "3", "guest", "", "user", "", "admin", "")
            if rc == 0:
                auth.set_role(ch, role)
        elif act == "off":
            cur = next(a for a in auth.accounts() if a["name"] == ch)["disabled"]
            auth.set_disabled(ch, not cur)
        else:
            auth.delete(ch)
    fix_owner()


def main(argv=None):
    a = list(sys.argv[1:] if argv is None else argv)
    if not a:
        return menu()
    c, rest = a[0], a[1:]
    try:
        if c == "showip":
            return cmd_showip()
        if c == "showlink":
            return cmd_showlink(rest)
        if c == "status":
            return cmd_status()
        if c == "banner":
            return cmd_banner()
        if c == "get":
            cfg = serverconf.load()
            for k in (rest[0].split(".") if rest else []):
                cfg = cfg[k]
            print(json.dumps(cfg, indent=2))
            return 0
        if c == "set" and len(rest) == 2:
            set_setting(*rest)
            return 0
        if c == "dhcp":
            set_setting("network.mode", "dhcp")
            return 0
        if c == "ip" and len(rest) >= 2:
            cfg = serverconf.load()
            old = serverconf.load()
            cfg["network"].update(mode="static", address=rest[0], gateway=rest[1], dns=rest[2] if len(rest) > 2 else "")
            new = serverconf.save(cfg)
            fix_owner()
            apply_parts(changed(old, new) or ["network"])
            return 0
        if c == "account":
            return cmd_account(rest)
        if c == "untis":
            return cmd_untis()
        if c == "sync":
            return subprocess.call([sys.executable, str(HERE.parent / "ssc.py"), "sync", "--direct"])
        if c == "rotate-link":
            core.parent_token(rotate=True)
            fix_owner()
            return cmd_showlink()
        if c == "apply":
            import applycfg
            if os.geteuid() != 0 and not os.environ.get("SSC_ROOT"):
                print("apply needs root")
                return 1
            res = applycfg.apply(rest or list(applycfg.ORDER))
            return 0 if res["ok"] else 1
        if c == "process-request":      # used by ssc-apply.service (root)
            import applycfg
            applycfg.process_request()
            return 0
        if c == "first-boot":
            return cmd_first_boot()
        if c in ("-h", "--help", "help"):
            print(__doc__)
            return 0
    except ValueError as e:
        print("Error:", e)
        return 1
    print("Unknown command. `ssconfig help` lists them.")
    return 2


if __name__ == "__main__":
    sys.exit(main())
