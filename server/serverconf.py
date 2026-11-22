"""SSC Server configuration: one validated settings file, and pure functions that turn it into system files.

The single source of truth is  <state dir>/server.json  (default /var/lib/ssc/server.json). Both `ssconfig` (console)
and the admin panel (web) edit it through save(), which validates everything. A small root helper (ssconfig apply)
re-validates the file and renders /etc files from the fixed templates below - never from raw user text.
"""
import copy
import ipaddress
import json
import os
import re
import tempfile
from pathlib import Path

try:
    import core
    SECTIONS = core.PARENT_SECTIONS
except Exception:  # pragma: no cover
    SECTIONS = ("today", "timetable", "homework", "exams", "plan", "changes", "grades", "absences")

ROLES = ("guest", "user", "admin")
LID_ACTIONS = ("ignore", "suspend", "poweroff")
DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
SCHEDULE_MODES = ("quiet", "suspend", "poweroff")
TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
SLEEP_TARGETS = ("sleep.target", "suspend.target", "hibernate.target", "hybrid-sleep.target")

DEFAULTS = {
    "web": {"port": 8080, "bind": "0.0.0.0", "https": False, "session_hours": 12, "guest_passwordless": False},
    "links": {"enabled": True, "long": False, "port": 8766},
    "network": {"mode": "dhcp", "interface": "auto", "address": "", "gateway": "", "dns": [], "hostname": "ssc-server"},
    "console": {"autologin_user": ""},
    "power": {"lid": "ignore", "block_sleep": True},
    "schedule": {"enabled": False, "off": "22:00", "on": "06:00", "days": list(DAYS), "mode": "quiet", "spin_down_disks": True, "screen_off": True, "stop_ssh": False},
    "wifi": {"enabled": False, "ssid": "", "open": False, "hidden": False, "country": ""},
    "ssh": {"enabled": True, "port": 22, "permit_root": False, "password_auth": True, "allow_users": []},
    "sections": {"guest": ["today", "timetable", "homework", "exams", "plan", "changes"],
                 "user": list(SECTIONS)},
    "sync": {"minutes": 30},
    "firewall": {"enabled": True},
}


def state_dir():
    return Path(os.environ.get("SSC_DATA_DIR") or "/var/lib/ssc")


def config_path():
    return state_dir() / "server.json"


def system_root():
    """Prefix for the files under /etc (tests use a temp directory; production is /)."""
    return Path(os.environ.get("SSC_ROOT") or "/")


# ------------------------------------------------------------------ validation
HOSTNAME_RE = re.compile(r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$")
USER_RE = re.compile(r"^[a-z_][a-z0-9_-]{0,31}$")
IFACE_RE = re.compile(r"^[a-z][a-z0-9.-]{0,14}$")


def _port(v, name):
    try:
        n = int(v)
    except (TypeError, ValueError):
        raise ValueError(f"{name}: port must be a number")
    if not 1 <= n <= 65535:
        raise ValueError(f"{name}: port must be 1-65535")
    return n


def _bool(v):
    return str(v).strip().lower() in ("1", "true", "yes", "on") if not isinstance(v, bool) else v


def _ipv4(v, name):
    try:
        return str(ipaddress.IPv4Address(str(v).strip()))
    except ValueError:
        raise ValueError(f"{name}: '{v}' is not a valid IPv4 address")


def validate(cfg):
    """-> a normalized copy, or ValueError with a message that says what is wrong."""
    c = merge(copy.deepcopy(DEFAULTS), cfg or {})
    w = c["web"]
    w["port"] = _port(w["port"], "web")
    w["bind"] = _ipv4(w["bind"], "web bind address")
    w["https"], w["guest_passwordless"] = _bool(w["https"]), _bool(w["guest_passwordless"])
    try:
        w["session_hours"] = int(w["session_hours"])
    except (TypeError, ValueError):
        raise ValueError("session hours must be a number")
    if not 1 <= w["session_hours"] <= 168:
        raise ValueError("session hours must be 1-168")

    l = c["links"]
    l["enabled"], l["long"] = _bool(l["enabled"]), _bool(l["long"])
    l["port"] = _port(l["port"], "links")
    if l["enabled"] and l["port"] == w["port"]:
        raise ValueError("the link port must differ from the web port")

    n = c["network"]
    if n["mode"] not in ("dhcp", "static"):
        raise ValueError("network mode must be dhcp or static")
    n["hostname"] = str(n["hostname"]).strip().lower()
    if not HOSTNAME_RE.match(n["hostname"]):
        raise ValueError("hostname: letters, digits and '-' only (max 63)")
    n["interface"] = str(n["interface"]).strip() or "auto"
    if n["interface"] != "auto" and not IFACE_RE.match(n["interface"]):
        raise ValueError("interface name looks invalid")
    if n["mode"] == "static":
        try:
            iface = ipaddress.IPv4Interface(str(n["address"]).strip())
        except ValueError:
            raise ValueError("static address must look like 192.168.1.50/24")
        if iface.network.prefixlen < 8 or iface.network.prefixlen > 30:
            raise ValueError("prefix length must be between /8 and /30")
        if iface.ip in (iface.network.network_address, iface.network.broadcast_address):
            raise ValueError("that address is the network or broadcast address")
        n["address"] = str(iface)
        gw = ipaddress.IPv4Address(_ipv4(n["gateway"], "gateway")) if n["gateway"] else None
        if gw is not None:
            if gw not in iface.network:
                raise ValueError("the gateway must be inside the same network as the address")
            if gw == iface.ip:
                raise ValueError("the gateway can't be the server's own address")
        n["gateway"] = str(gw) if gw else ""
        dns = n["dns"] if isinstance(n["dns"], list) else [x for x in re.split(r"[,\s]+", str(n["dns"])) if x]
        if len(dns) > 3:
            raise ValueError("at most 3 DNS servers")
        n["dns"] = [_ipv4(d, "DNS server") for d in dns]
    else:
        n["address"], n["gateway"], n["dns"] = "", "", []

    wf = c["wifi"]
    wf["enabled"], wf["open"], wf["hidden"] = _bool(wf["enabled"]), _bool(wf["open"]), _bool(wf["hidden"])
    wf["ssid"] = str(wf["ssid"])
    wf["country"] = str(wf["country"]).strip().upper()
    if wf["country"] and not re.fullmatch(r"[A-Z]{2}", wf["country"]):
        raise ValueError("Wi-Fi country: two letters, e.g. AT, DE, US")
    if wf["enabled"]:
        check_ssid(wf["ssid"])
    elif wf["ssid"]:
        check_ssid(wf["ssid"])

    pw = c["power"]
    if pw["lid"] not in LID_ACTIONS:
        raise ValueError("lid action must be one of: " + ", ".join(LID_ACTIONS))
    pw["block_sleep"] = _bool(pw["block_sleep"])

    sc = c["schedule"]
    for k in ("enabled", "spin_down_disks", "screen_off", "stop_ssh"):
        sc[k] = _bool(sc[k])
    for k in ("off", "on"):
        sc[k] = str(sc[k]).strip()
        if not TIME_RE.match(sc[k]):
            raise ValueError("schedule times look like 22:00 (24-hour clock)")
    if sc["off"] == sc["on"]:
        raise ValueError("the switch-off and switch-on times must differ")
    days = sc["days"] if isinstance(sc["days"], list) else [x for x in re.split(r"[,\s]+", str(sc["days"]).lower()) if x]
    days = [str(d).strip().lower()[:3] for d in days]
    if not days or any(d not in DAYS for d in days):
        raise ValueError("schedule days: choose from " + ", ".join(DAYS))
    sc["days"] = [d for d in DAYS if d in days]
    if sc["mode"] not in SCHEDULE_MODES:
        raise ValueError("schedule mode must be one of: " + ", ".join(SCHEDULE_MODES))

    al = str(c["console"]["autologin_user"]).strip()
    if al and (not USER_RE.match(al) or al == "root"):
        raise ValueError("auto-login user: a normal user name (not root)")
    c["console"]["autologin_user"] = al

    s = c["ssh"]
    s["enabled"], s["permit_root"], s["password_auth"] = _bool(s["enabled"]), _bool(s["permit_root"]), _bool(s["password_auth"])
    s["port"] = _port(s["port"], "ssh")
    if s["enabled"] and s["port"] in (w["port"], l["port"] if l["enabled"] else -1):
        raise ValueError("the SSH port must differ from the web and link ports")
    users = s["allow_users"] if isinstance(s["allow_users"], list) else [x for x in re.split(r"[,\s]+", str(s["allow_users"])) if x]
    for u in users:
        if not USER_RE.match(u):
            raise ValueError(f"'{u}' is not a valid user name")
    s["allow_users"] = users

    for role in ("guest", "user"):
        secs = c["sections"][role]
        secs = secs if isinstance(secs, list) else [x for x in re.split(r"[,\s]+", str(secs)) if x]
        bad = [x for x in secs if x not in SECTIONS]
        if bad or not secs:
            raise ValueError(f"sections for {role}: choose from {', '.join(SECTIONS)}")
        c["sections"][role] = [x for x in SECTIONS if x in secs]

    try:
        c["sync"]["minutes"] = int(c["sync"]["minutes"])
    except (TypeError, ValueError):
        raise ValueError("sync minutes must be a number")
    if not 5 <= c["sync"]["minutes"] <= 1440:
        raise ValueError("sync interval must be 5-1440 minutes")
    c["firewall"]["enabled"] = _bool(c["firewall"]["enabled"])
    return c


def merge(base, over):
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            merge(base[k], v)
        else:
            base[k] = v
    return base


def load():
    try:
        raw = json.loads(config_path().read_text())
    except (OSError, ValueError):
        raw = {}
    try:
        return validate(raw)
    except ValueError:
        return validate({})   # a damaged file never stops the server from starting with safe defaults


def save(cfg):
    """Validate, then write atomically (readable by the service group, writable by the service account)."""
    c = validate(cfg)
    p = config_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=p.parent, prefix=".server-", suffix=".json")
    with os.fdopen(fd, "w") as f:
        json.dump(c, f, indent=2)
    os.chmod(tmp, 0o640)
    os.replace(tmp, p)
    return c


def update(path, value):
    """update("network.mode", "static") - one dotted setting; validates the whole result."""
    c = load()
    cur = c
    keys = path.split(".")
    for k in keys[:-1]:
        cur = cur[k]
    if keys[-1] not in cur:
        raise ValueError(f"unknown setting '{path}'")
    cur[keys[-1]] = value
    return save(c)


# ------------------------------------------------------------------ Wi-Fi
def check_ssid(ssid):
    b = ssid.encode("utf-8", "strict")
    if not 1 <= len(b) <= 32:
        raise ValueError("Wi-Fi name (SSID): 1-32 bytes")
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in ssid):
        raise ValueError("Wi-Fi name contains control characters")
    return ssid


def derive_psk(ssid, passphrase):
    """WPA2-Personal key from name + passphrase (IEEE 802.11i: PBKDF2-HMAC-SHA1, 4096 rounds). Only this 64-hex key is
    ever stored - the passphrase itself is not written anywhere."""
    import hashlib
    check_ssid(ssid)
    if not 8 <= len(passphrase) <= 63 or any(ord(ch) < 32 or ord(ch) > 126 for ch in passphrase):
        raise ValueError("Wi-Fi password: 8-63 printable ASCII characters")
    return hashlib.pbkdf2_hmac("sha1", passphrase.encode("ascii"), ssid.encode("utf-8"), 4096, 32).hex()


WPA_CONF_PATH = "/etc/wpa_supplicant/ssc-wifi.conf"


def render_wpa(wifi, psk=None):
    """wpa_supplicant.conf from a validated section. The SSID is written as hex, so no character in it can alter the file."""
    lines = ["# Managed by ssconfig - changes are overwritten", "ctrl_interface=DIR=/run/wpa_supplicant GROUP=netdev", "update_config=0"]
    if wifi["country"]:
        lines.append(f"country={wifi['country']}")
    lines += ["network={", f"    ssid={wifi['ssid'].encode('utf-8').hex()}"]
    if wifi["hidden"]:
        lines.append("    scan_ssid=1")
    if wifi["open"]:
        lines.append("    key_mgmt=NONE")
    else:
        if not psk or not re.fullmatch(r"[0-9a-f]{64}", psk):
            raise ValueError("Wi-Fi key missing")
        lines += ["    key_mgmt=WPA-PSK", f"    psk={psk}"]
    lines.append("}")
    return "\n".join(lines) + "\n"


def psk_from_conf(text):
    m = re.search(r"^\s*psk=([0-9a-f]{64})\s*$", text or "", re.M)
    return m.group(1) if m else None


def ssid_from_conf(text):
    m = re.search(r"^\s*ssid=([0-9a-f]+)\s*$", text or "", re.M)
    try:
        return bytes.fromhex(m.group(1)).decode("utf-8") if m else None
    except ValueError:
        return None


def stage_wifi_psk(ssid, psk):
    """Hand a new key to the root helper: a 0600 file next to the apply request (consumed and deleted there)."""
    d = state_dir() / "apply"
    d.mkdir(parents=True, exist_ok=True)
    tmp = d / ".wifi.tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump({"ssid": ssid, "psk": psk}, f)
    os.replace(tmp, d / "wifi.psk")


def parse_iw_scan(text):
    """`iw dev X scan` -> [{ssid, signal_dbm, secured}] strongest first, one entry per name, hidden networks skipped."""
    nets, cur = {}, None
    for line in (text or "").splitlines():
        if line.startswith("BSS "):
            cur = {"ssid": "", "signal": -100.0, "secured": False}
            nets.setdefault(id(cur), cur)
            continue
        if cur is None:
            continue
        t = line.strip()
        if t.startswith("SSID:"):
            cur["ssid"] = t[5:].strip()
        elif t.startswith("signal:"):
            try:
                cur["signal"] = float(t.split()[1])
            except (IndexError, ValueError):
                pass
        elif t.startswith(("RSN:", "WPA:")) or "Privacy" in t:
            cur["secured"] = True
    best = {}
    for n in nets.values():
        raw = n["ssid"]
        if not raw or "\\x00" in raw or raw.startswith("\\x"):
            continue
        if raw not in best or n["signal"] > best[raw]["signal_dbm"]:
            best[raw] = {"ssid": raw, "signal_dbm": n["signal"], "secured": n["secured"]}
    return sorted(best.values(), key=lambda n: -n["signal_dbm"])


# ------------------------------------------------------------------ rendering (fixed templates, validated input only)
def render_interfaces(net, iface, wpa_conf=None, head=True):
    out = "# Managed by ssconfig - changes are overwritten\n" if head else ""
    if net["mode"] == "dhcp":
        out += f"auto {iface}\niface {iface} inet dhcp\n"
    else:
        ip = ipaddress.IPv4Interface(net["address"])
        out += f"auto {iface}\niface {iface} inet static\n    address {ip.ip}\n    netmask {ip.netmask}\n"
        if net["gateway"]:
            out += f"    gateway {net['gateway']}\n"
        if net["dns"]:
            out += f"    dns-nameservers {' '.join(net['dns'])}\n"
    if wpa_conf:
        out += f"    wpa-conf {wpa_conf}\n"
    return out


def render_network(cfg, wired, wlan=None):
    """With Wi-Fi on (and a wireless card present) the address settings go to the Wi-Fi interface and a wired port, if any,
    just gets DHCP. Otherwise the settings go to the wired port as before."""
    if cfg["wifi"]["enabled"] and wlan:
        out = "# Managed by ssconfig - changes are overwritten\n"
        if wired and wired != wlan:
            out += f"allow-hotplug {wired}\niface {wired} inet dhcp\n\n"
        return out + render_interfaces(cfg["network"], wlan, WPA_CONF_PATH, head=False)
    return render_interfaces(cfg["network"], wired or wlan or "eth0")


def render_logind(power):
    """systemd-logind drop-in: what closing the lid does. 'ignore' = the machine keeps running (an old laptop as a server).
    block_sleep also ignores the suspend/hibernate keys and idle actions (the sleep targets are masked as well)."""
    lid = power["lid"]
    lines = ["# Managed by ssconfig - changes are overwritten", "[Login]", f"HandleLidSwitch={lid}", f"HandleLidSwitchExternalPower={lid}",
             f"HandleLidSwitchDocked={lid}", "IdleAction=ignore"]
    if power["block_sleep"]:
        lines += ["HandleSuspendKey=ignore", "HandleHibernateKey=ignore", "HandleSuspendKeyLongPress=ignore", "HandleHibernateKeyLongPress=ignore"]
    return "\n".join(lines) + "\n"


def render_sshd(ssh):
    lines = ["# Managed by ssconfig - changes are overwritten", f"Port {ssh['port']}",
             f"PermitRootLogin {'yes' if ssh['permit_root'] else 'no'}",
             f"PasswordAuthentication {'yes' if ssh['password_auth'] else 'no'}", "PubkeyAuthentication yes",
             "X11Forwarding no", "MaxAuthTries 4", "LoginGraceTime 30"]
    if ssh["allow_users"]:
        lines.append("AllowUsers " + " ".join(ssh["allow_users"]))
    return "\n".join(lines) + "\n"


def render_nftables(cfg):
    if not cfg["firewall"]["enabled"]:
        return "# firewall disabled in ssconfig\nflush ruleset\n"
    ports = [cfg["web"]["port"]] + ([cfg["links"]["port"]] if cfg["links"]["enabled"] else [])
    rules = ["flush ruleset", "table inet ssc {", "  chain input {", "    type filter hook input priority 0; policy drop;",
             "    ct state established,related accept", "    iif lo accept", "    ip protocol icmp accept", "    ip6 nexthdr icmpv6 accept",
             f"    tcp dport {{ {', '.join(str(p) for p in ports)} }} accept"]
    if cfg["ssh"]["enabled"]:
        rules.append(f"    tcp dport {cfg['ssh']['port']} accept")
    rules += ["  }", "}"]
    return "# Managed by ssconfig - changes are overwritten\n" + "\n".join(rules) + "\n"


# ------------------------------------------------------------------ addresses (for showip / showlink / status)
def parse_ip_json(text):
    """Output of `ip -j -4 addr` -> [(interface, 'a.b.c.d/p')], without loopback."""
    out = []
    for i in json.loads(text or "[]"):
        if i.get("ifname") == "lo":
            continue
        for a in i.get("addr_info", []):
            if a.get("family") == "inet" and a.get("local"):
                out.append((i["ifname"], f"{a['local']}/{a.get('prefixlen', 24)}"))
    return out


def server_urls(cfg, addrs):
    scheme = "https" if cfg["web"]["https"] else "http"
    port = cfg["web"]["port"]
    default = (scheme == "https" and port == 443) or (scheme == "http" and port == 80)
    return [f"{scheme}://{a.split('/')[0]}{'' if default else ':' + str(port)}/" for _, a in addrs]


def link_urls(cfg, addrs, token):
    if not cfg["links"]["enabled"]:
        return []
    port = cfg["links"]["port"]
    return [dict(host=a.split("/")[0], browser=f"http://{a.split('/')[0]}:{port}/p/{token}/", wall=f"http://{a.split('/')[0]}:{port}/p/{token}/wall")
            for _, a in addrs]
