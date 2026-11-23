"""Wake-on-LAN (wake other machines; make this one wakeable) and the wake-up test for the sleep schedule."""
import json
import socket
import time
from pathlib import Path

import serverconf

TEST_SECONDS = 120


def magic_packet(mac):
    raw = bytes.fromhex(serverconf.normalize_mac(mac).replace(":", ""))
    return b"\xff" * 6 + raw * 16


def broadcasts(addrs):
    """Directed broadcast addresses of this server's networks (+ the global one): `addrs` = ['192.168.1.5/24', ...]."""
    import ipaddress
    out = ["255.255.255.255"]
    for a in addrs:
        try:
            out.append(str(ipaddress.IPv4Interface(a).network.broadcast_address))
        except ValueError:
            pass
    return list(dict.fromkeys(out))


def send(mac, targets=("255.255.255.255",), make_socket=None):
    """Send the magic packet (UDP port 9) to every target. -> number of sends that worked."""
    pkt = magic_packet(mac)
    ok = 0
    for t in targets:
        s = make_socket() if make_socket else socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            s.sendto(pkt, (t, 9))
            ok += 1
        except OSError:
            pass
        finally:
            s.close()
    return ok


def own_macs(root="/"):
    """This server's own wired MAC address(es): what you need to wake it from somewhere else."""
    out = []
    d = Path(root) / "sys/class/net"
    try:
        for n in sorted(d.iterdir()):
            if n.name == "lo" or (n / "wireless").exists() or (n / "phy80211").exists() or n.name.startswith(("docker", "veth", "br-", "virbr")):
                continue
            try:
                mac = (n / "address").read_text().strip().lower()
            except OSError:
                continue
            if serverconf.MAC_RE.match(mac) and mac != "00:00:00:00:00:00":
                out.append({"interface": n.name, "mac": mac})
    except OSError:
        pass
    return out


# ------------------------------------------------------------------ the wake-up test
def marker_path():
    return serverconf.state_dir() / "apply" / "waketest.json"


def can_suspend(root="/"):
    try:
        return "mem" in (Path(root) / "sys/power/state").read_text().split()
    except OSError:
        return False


def run_test(run, now=time.time, seconds=TEST_SECONDS):
    """Runs as root: suspend to RAM with an alarm `seconds` ahead. rtcwake returns once the machine is awake again;
    the marker file says when it went down and when it came back."""
    m = {"started": now(), "seconds": seconds, "finished": None, "rc": None, "error": ""}
    _save(m)
    r = run(["rtcwake", "-m", "mem", "-s", str(seconds)])
    m.update(finished=now(), rc=r.returncode, error=(r.stderr or "").strip()[:200] if r.returncode else "")
    _save(m)
    return m


def _save(m):
    p = marker_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(m))


def last_test():
    try:
        m = json.loads(marker_path().read_text())
    except (OSError, ValueError):
        return None
    return dict(m, verdict=verdict(m))


def verdict(m):
    """-> (state, text). The alarm is the only thing that should have woken the machine after `seconds`."""
    if not m.get("finished"):
        return ("running", "The test is running: the machine is going to sleep and should wake up by itself.")
    if m.get("rc"):
        return ("failed", "It could not go to sleep: " + (m.get("error") or "rtcwake failed") + ". Use the 'quiet' mode on this machine.")
    slept = m["finished"] - m["started"]
    want = m["seconds"]
    if abs(slept - want) <= 30 + want * 0.25 and slept >= want * 0.6:
        return ("ok", f"It woke up by itself after {int(slept)} s (alarm: {want} s). Wake-up by the hardware clock works on this machine.")
    if slept < want * 0.6:
        return ("early", f"It woke after only {int(slept)} s (alarm: {want} s) - something else woke it (network, keyboard, lid). Wake-up may still work; test again with nothing touching it.")
    return ("late", f"It came back after {int(slept)} s, much later than the alarm ({want} s): somebody woke it, or the alarm does not work. "
                    "Check the BIOS/UEFI setting 'wake on RTC alarm' (or 'resume by alarm'), then test again.")
