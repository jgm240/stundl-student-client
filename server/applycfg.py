"""Turns the validated server.json into system files and service actions. Runs as root (ssconfig / ssc-apply.service).

Everything written comes from the fixed templates in serverconf - never from raw text. `run` is injectable so tests can
check the commands without touching the machine; SSC_ROOT redirects /etc for tests.
"""
import json
import os
import re
import subprocess
import time
from pathlib import Path

import serverconf


def _run(cmd, check=False):
    return subprocess.run(cmd, capture_output=True, text=True, timeout=60, check=check)


def _write(root, rel, text, mode=0o644):
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name("." + p.name + ".new")
    tmp.write_text(text)
    os.chmod(tmp, mode)
    os.replace(tmp, p)


def default_interface(root):
    d = root / "sys/class/net"
    try:
        names = sorted(n.name for n in d.iterdir() if n.name != "lo" and not n.name.startswith(("docker", "veth", "br-", "virbr")))
    except OSError:
        names = []
    return names[0] if names else "eth0"


def apply_part(part, cfg, root=None, run=_run):
    """-> list of human-readable notes. Raises RuntimeError when a part could not be applied (and was rolled back)."""
    root = root or serverconf.system_root()
    if part == "network":
        n = cfg["network"]
        iface = default_interface(root) if n["interface"] == "auto" else n["interface"]
        _write(root, "etc/network/interfaces.d/ssc", serverconf.render_interfaces(n, iface))
        _write(root, "etc/hostname", n["hostname"] + "\n")
        hosts = root / "etc/hosts"
        try:
            lines = [l for l in hosts.read_text().splitlines() if not l.startswith("127.0.1.1")]
        except OSError:
            lines = ["127.0.0.1\tlocalhost"]
        _write(root, "etc/hosts", "\n".join(lines + [f"127.0.1.1\t{n['hostname']}"]) + "\n")
        run(["hostname", n["hostname"]])
        run(["ifdown", "--force", iface])
        r = run(["ifup", iface])
        if r.returncode != 0:
            raise RuntimeError("network: ifup failed: " + (r.stderr or r.stdout).strip()[:200])
        return [f"network applied on {iface} ({n['mode']})"]
    if part == "ssh":
        s = cfg["ssh"]
        path = "etc/ssh/sshd_config.d/ssc.conf"
        old = (root / path).read_text() if (root / path).exists() else None
        _write(root, path, serverconf.render_sshd(s))
        r = run(["sshd", "-t"])
        if r.returncode != 0:
            if old is None:
                (root / path).unlink()
            else:
                _write(root, path, old)
            raise RuntimeError("ssh: sshd rejected the settings, previous settings kept: " + (r.stderr or "").strip()[:200])
        run(["systemctl", "enable" if s["enabled"] else "disable", "--now" if s["enabled"] else "--now", "ssh"])
        if s["enabled"]:
            run(["systemctl", "reload-or-restart", "ssh"])
        return ["ssh " + ("enabled" if s["enabled"] else "disabled")]
    if part == "firewall":
        _write(root, "etc/nftables.conf", "#!/usr/sbin/nft -f\n" + serverconf.render_nftables(cfg))
        r = run(["nft", "-c", "-f", str(root / "etc/nftables.conf")])
        if r.returncode != 0:
            raise RuntimeError("firewall: nft rejected the rules: " + (r.stderr or "").strip()[:200])
        run(["nft", "-f", str(root / "etc/nftables.conf")])
        run(["systemctl", "enable", "nftables"])
        return ["firewall " + ("on" if cfg["firewall"]["enabled"] else "off")]
    if part in ("web", "links"):
        run(["systemctl", "restart", "ssc-server"])
        return ["web service restarted"]
    raise RuntimeError("unknown part " + part)


ORDER = ("network", "firewall", "ssh", "web", "links")


def apply(parts, cfg=None, root=None, run=_run):
    cfg = cfg or serverconf.load()
    done, failed = [], []
    parts = [p for p in ORDER if p in set(parts)]
    if "links" in parts and "web" in parts:
        parts.remove("links")
    for p in parts:
        try:
            done += apply_part(p, cfg, root, run)
        except (RuntimeError, OSError, subprocess.SubprocessError) as e:
            failed.append(str(e))
    res = {"at": time.time(), "ok": not failed, "done": done, "failed": failed}
    d = serverconf.state_dir() / "apply"
    d.mkdir(parents=True, exist_ok=True)
    (d / "result.json").write_text(json.dumps(res))
    return res


def process_request(root=None, run=_run):
    """The web panel only drops a request file; this runs as root and re-validates everything from server.json."""
    req = serverconf.state_dir() / "apply" / "request.json"
    try:
        parts = [p for p in json.loads(req.read_text()).get("parts", []) if p in ORDER]
        req.unlink()
    except (OSError, ValueError):
        return None
    return apply(parts, root=root, run=run)


def apply_all(root=None, run=_run):
    return apply(list(ORDER), root=root, run=run)
