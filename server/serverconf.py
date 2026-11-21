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

DEFAULTS = {
    "web": {"port": 8080, "bind": "0.0.0.0", "https": False, "session_hours": 12, "guest_passwordless": False},
    "links": {"enabled": True, "long": False, "port": 8766},
    "network": {"mode": "dhcp", "interface": "auto", "address": "", "gateway": "", "dns": [], "hostname": "ssc-server"},
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


# ------------------------------------------------------------------ rendering (fixed templates, validated input only)
def render_interfaces(net, iface):
    head = "# Managed by ssconfig - changes are overwritten\n"
    if net["mode"] == "dhcp":
        return head + f"auto {iface}\niface {iface} inet dhcp\n"
    ip = ipaddress.IPv4Interface(net["address"])
    out = head + f"auto {iface}\niface {iface} inet static\n    address {ip.ip}\n    netmask {ip.netmask}\n"
    if net["gateway"]:
        out += f"    gateway {net['gateway']}\n"
    if net["dns"]:
        out += f"    dns-nameservers {' '.join(net['dns'])}\n"
    return out


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
