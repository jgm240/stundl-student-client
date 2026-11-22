#!/usr/bin/env python3
"""ssc-install - text-based installer for SSC Server (runs from the live system, as root).

  ssc-install            interactive installer (whiptail)
  ssc-install --auto     unattended: reads answers.json from a disk labelled SSCANS, installs, powers off
  ssc-install --dry-run  print what would be done

It erases the chosen disk completely: GPT, a BIOS-boot partition, an EFI partition (64-bit images) and one ext4 root
partition; copies the running system to it, writes the configuration, installs GRUB (BIOS and UEFI) and removes the
live-system packages.
"""
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))
os.environ.setdefault("SSC_VAULT", "file")

import install_plan as plan  # noqa: E402

TARGET = Path("/target")
SOURCE = "/run/live/rootfs/filesystem.squashfs/"


class Runner:
    def __init__(self, dry=False, log=lambda m: print(m, flush=True)):
        self.dry, self.log = dry, log

    def __call__(self, cmd, check=True, input=None, env=None):
        self.log("$ " + " ".join(cmd))
        if self.dry:
            return subprocess.CompletedProcess(cmd, 0, "", "")
        r = subprocess.run(cmd, input=input, capture_output=True, text=True, env={**os.environ, **(env or {})})
        if check and r.returncode != 0:
            raise RuntimeError(f"{' '.join(cmd[:3])} failed: {(r.stderr or r.stdout).strip()[-300:]}")
        return r


def arch():
    m = os.uname().machine
    return "amd64" if m == "x86_64" else "i386"


def find_disks():
    r = subprocess.run(["lsblk", "-J", "-b", "-d", "-p", "-o", "NAME,SIZE,MODEL,TYPE,RO,RM"], capture_output=True, text=True)
    live = set()
    src = subprocess.run(["findmnt", "-no", "SOURCE", "/run/live/medium"], capture_output=True, text=True).stdout.strip()
    if src:
        p = subprocess.run(["lsblk", "-no", "PKNAME", src], capture_output=True, text=True).stdout.strip().splitlines()
        live = {"/dev/" + x for x in p if x}
    return plan.parse_lsblk(r.stdout, live)


def uuid_of(dev):
    return subprocess.run(["blkid", "-s", "UUID", "-o", "value", dev], capture_output=True, text=True).stdout.strip()


def install(ans, run, root=TARGET, efi=None, a=None):
    """Does the whole installation. `ans` is validated (install_plan.validate_answers)."""
    a = a or arch()
    efi = os.path.isdir("/sys/firmware/efi") if efi is None else efi
    disk = ans["disk"]
    step = lambda t: run.log("== " + t)

    step("Partitioning " + disk)
    run(["umount", "-R", str(root)], check=False)
    run(["swapoff", "-a"], check=False)
    for c in plan.partition_commands(disk, a):
        run(c)
    run(["partprobe", disk], check=False)
    run(["udevadm", "settle"], check=False)
    time.sleep(0 if run.dry else 1)
    for c in plan.mkfs_commands(disk, a):
        run(c)

    layout = plan.layout(a, efi)
    root_dev = plan.part(disk, layout[-1]["n"])
    efi_dev = plan.part(disk, 2) if a == "amd64" else None
    step("Copying the system")
    root.mkdir(parents=True, exist_ok=True)
    run(["mount", root_dev, str(root)])
    if efi_dev:
        (root / "boot/efi").mkdir(parents=True, exist_ok=True)
        run(["mount", efi_dev, str(root / "boot/efi")])
    run(["rsync", "-aHAX", "--numeric-ids", *[f"--exclude={e}" for e in plan.RSYNC_EXCLUDES], SOURCE, str(root) + "/"])
    for d in ("proc", "sys", "dev", "run", "tmp", "mnt", "media"):
        (root / d).mkdir(exist_ok=True)
    os.chmod(root / "tmp", 0o1777) if not run.dry else None

    step("Configuring")
    for m in ("dev", "proc", "sys", "run"):
        run(["mount", "--rbind", "/" + m, str(root / m)])
    ch = lambda *c, **k: run(["chroot", str(root), *c], **k)
    try:
        if not run.dry:
            (root / "etc/fstab").write_text(plan.fstab(uuid_of(root_dev), uuid_of(efi_dev) if efi_dev else None))
            (root / "etc/hostname").write_text(ans["hostname"] + "\n")
            tz = ans["timezone"]
            (root / "etc/timezone").write_text(tz + "\n")
            lt = root / "etc/localtime"
            lt.unlink(missing_ok=True)
            lt.symlink_to(f"/usr/share/zoneinfo/{tz}")
            (root / "etc/default/keyboard").write_text(f'XKBMODEL="pc105"\nXKBLAYOUT="{ans["keymap"]}"\nBACKSPACE="guess"\n')
        ch("sh", "-c", "rm -f /etc/machine-id /var/lib/dbus/machine-id /etc/ssh/ssh_host_*; systemd-machine-id-setup; "
                       "dpkg-reconfigure -f noninteractive openssh-server")
        # SSC state: settings + the admin account, created with the real code against the target's state directory
        state = str(root / "var/lib/ssc")
        env = {"SSC_DATA_DIR": state, "SSC_VAULT": "file", "SSC_NEW_PASSWORD": ans["password"]}
        cfg = {"network": ans["network"], "ssh": {"enabled": ans["ssh"]}}
        if ans.get("wifi"):
            cfg["wifi"] = {k: v for k, v in ans["wifi"].items() if k != "psk"}
        run([sys.executable, "-c", "import sys,json;sys.path.insert(0,sys.argv[1]);import serverconf;serverconf.save(json.loads(sys.argv[2]))",
             str(HERE), json.dumps(cfg)], env=env)
        run([sys.executable, str(HERE / "ssconfig.py"), "account", "add", ans["admin"], "admin"], env=env)
        ch("chown", "-R", "ssc:ssc", "/var/lib/ssc")
        # system files from the same validated settings (templates only)
        import applycfg
        import serverconf
        full = serverconf.validate(cfg)
        noop = lambda c: subprocess.CompletedProcess(c, 0, "", "")
        if not run.dry:
            if ans.get("wifi") and ans["wifi"].get("psk"):
                serverconf.stage_wifi_psk(ans["wifi"]["ssid"], ans["wifi"]["psk"])
            for part in ("network", "ssh", "firewall"):
                applycfg.apply_part(part, full, root, noop)
        # Linux login account (console + SSH): same name and password as the web admin
        ch("useradd", "-m", "-s", "/bin/bash", "-G", "sudo", ans["admin"])
        ch("chpasswd", input=f"{ans['admin']}:{ans['password']}\n")
        ch("passwd", "-l", "root")
        if ans.get("autologin"):
            full2 = serverconf.validate({**cfg, "console": {"autologin_user": ans["admin"]}})
            run([sys.executable, "-c", "import sys,json;sys.path.insert(0,sys.argv[1]);import serverconf;serverconf.save(json.loads(sys.argv[2]))",
                 str(HERE), json.dumps({**cfg, "console": {"autologin_user": ans["admin"]}})], env=env)
            if not run.dry:
                applycfg.apply_part("console", full2, root, noop)
            ch("chown", "-R", "ssc:ssc", "/var/lib/ssc")      # the settings file was rewritten as root
        ch("systemctl", "enable", "ssc-server", "ssc-apply.path", "ssc-banner", "nftables", "ssh", "ssc-sync.timer", check=False)

        # the installed system must show a login prompt on the screen (tty1), not a blank cursor
        ch("systemctl", "enable", "getty@tty1.service", check=False)
        if not run.dry and not (root / "etc/systemd/system/getty.target.wants/getty@tty1.service").exists():
            raise RuntimeError("the console login (getty@tty1) is not enabled in the installed system")

        step("Removing the live-system parts")
        ch("sh", "-c", "apt-get -y purge live-boot live-boot-initramfs-tools live-config live-config-systemd live-config-sysvinit 2>/dev/null || "
                       "dpkg --purge --force-depends live-boot live-boot-initramfs-tools live-config live-config-systemd || true")
        if not run.dry:   # the image ships no /etc/default/grub, so write the whole file
            (root / "etc/default").mkdir(parents=True, exist_ok=True)
            (root / "etc/default/grub").write_text(
                'GRUB_DEFAULT=0\nGRUB_TIMEOUT=2\nGRUB_DISTRIBUTOR="SSC Server"\nGRUB_CMDLINE_LINUX_DEFAULT="quiet"\n'
                'GRUB_CMDLINE_LINUX="console=tty0 console=ttyS0,115200 nomodeset"\nGRUB_DISABLE_OS_PROBER=true\n# UEFI: hand the firmware framebuffer to the kernel, otherwise the screen stays blank (only a cursor)\nGRUB_GFXMODE=auto\nGRUB_GFXPAYLOAD_LINUX=keep\nGRUB_TERMINAL_OUTPUT="gfxterm"\n')

        step("Installing the boot loader")
        ch("update-initramfs", "-u", "-k", "all")
        ch("grub-install", "--target=i386-pc", "--recheck", disk)
        if efi_dev:
            ch("grub-install", "--target=x86_64-efi", "--efi-directory=/boot/efi", "--removable", "--no-nvram", "--recheck", check=efi)
        ch("update-grub")
    finally:
        step("Finishing")
        run(["sync"], check=False)
        for m in ("dev", "proc", "sys", "run"):
            run(["mount", "--make-rslave", str(root / m)], check=False)
        if run(["umount", "-R", str(root)], check=False).returncode != 0:
            run(["umount", "-R", "-l", str(root)], check=False)
    step("Done")


# ------------------------------------------------------------------ interactive part
def wt(*args):
    r = subprocess.run(["whiptail", "--backtitle", "SSC Server installer", *args], stderr=subprocess.PIPE, text=True)
    return r.returncode, r.stderr.strip()


def ask(disks):
    a = {}
    rows = []
    for d in disks:
        rows += [d["path"], f"{plan.human(d['size'])}  {d['model']}{'  (removable)' if d['removable'] else ''}"]
    if not rows:
        wt("--msgbox", "No disk found that is large enough (4 GB or more).", "9", "60")
        return None
    rc, a["disk"] = wt("--title", "Choose the disk", "--menu", "SSC Server will be installed on this disk.\nEVERYTHING ON IT WILL BE ERASED.", "18", "74", "6", *rows)
    if rc:
        return None
    rc, v = wt("--title", "Confirm", "--inputbox", f"This erases {a['disk']}.\nType ERASE (capital letters) to continue:", "10", "60")
    if rc or v != "ERASE":
        return None
    rc, a["hostname"] = wt("--inputbox", "Name of this server (hostname)", "8", "60", "ssc-server")
    rc, a["admin"] = wt("--inputbox", "Name of the administrator account.\nIt is used for the web admin panel and for logging in on this machine and over SSH.", "11", "70", "admin")
    while True:
        rc, p1 = wt("--passwordbox", "Administrator password (at least 6 characters)", "9", "60")
        if rc:
            return None
        rc, p2 = wt("--passwordbox", "Repeat the password", "9", "60")
        if p1 == p2 and len(p1) >= 6:
            a["password"] = p1
            break
        wt("--msgbox", "The passwords differ or are shorter than 6 characters.", "8", "60")
    rc, mode = wt("--menu", "Network", "12", "64", "2", "dhcp", "Automatic address (DHCP) - recommended", "static", "Fixed IP address")
    a["network"] = {"mode": mode or "dhcp"}
    if mode == "static":
        rc, addr = wt("--inputbox", "Address with prefix, e.g. 192.168.1.50/24", "8", "60")
        rc, gw = wt("--inputbox", "Gateway (your router)", "8", "60")
        rc, dns = wt("--inputbox", "DNS server(s), separated by comma", "8", "60", gw)
        a["network"].update(address=addr, gateway=gw, dns=dns)
    if any((Path("/sys/class/net") / n / "wireless").exists() for n in os.listdir("/sys/class/net")):
        if wt("--yesno", "A Wi-Fi card was found. Connect this server to a Wi-Fi network?", "8", "64")[0] == 0:
            rc, ssid = wt("--inputbox", "Wi-Fi network name (SSID)", "8", "60")
            rc, pw = wt("--passwordbox", "Wi-Fi password (leave empty for an open network)", "8", "60")
            rc, cc = wt("--inputbox", "Country code (two letters, e.g. AT, DE, US)", "8", "60", "AT")
            a["wifi"] = {"ssid": ssid, "passphrase": pw, "open": not pw, "country": cc}
    rc, a["timezone"] = wt("--inputbox", "Time zone (e.g. Europe/Vienna, UTC)", "8", "60", "UTC")
    rc, a["keymap"] = wt("--inputbox", "Keyboard layout (us, de, at, fr, ...)", "8", "60", "us")
    a["ssh"] = wt("--yesno", "Turn on SSH (remote login)?", "8", "50")[0] == 0
    a["autologin"] = wt("--defaultno", "--yesno", "Log in automatically on this server's own screen and keyboard? (Console only: SSH and the web panel still ask for a password.)\n\nConvenient, but anyone who can touch the machine then has the administrator account.", "11", "70")[0] == 0
    return a


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    dry = "--dry-run" in args
    if os.geteuid() != 0 and not dry:
        print("ssc-install must run as root")
        return 1
    run = Runner(dry)
    disks = find_disks() if not dry else [{"path": "/dev/sda", "size": 32 * 10 ** 9, "model": "dry", "removable": False}]
    if "--auto" in args:
        return auto(run, disks)
    a = ask(disks)
    if not a:
        print("Cancelled - nothing was changed.")
        return 1
    return finish(a, disks, run, interactive=True)


def finish(raw, disks, run, interactive=False):
    try:
        ans = plan.validate_answers(raw, disks)
    except ValueError as e:
        if interactive:
            wt("--msgbox", "Can't install: " + str(e), "10", "70")
        print("Error:", e)
        return 1
    try:
        install(ans, run)
    except RuntimeError as e:
        print("INSTALL FAILED:", e)
        if interactive:
            wt("--msgbox", "Installation failed:\n" + str(e), "14", "74")
        return 2
    print("SSC-INSTALL-OK")
    if interactive:
        wt("--msgbox", "Installed. Remove the installation medium; the server restarts now.\nAfter the restart log in and run:  ssconfig showip", "11", "70")
        subprocess.run(["reboot"])
    else:
        time.sleep(5)          # let the last log lines reach the console before the machine goes down
        subprocess.run(["poweroff"])
    return 0


def auto(run, disks):
    dev = Path("/dev/disk/by-label/SSCANS")
    if not dev.exists():
        print("no SSCANS disk")
        return 3
    mnt = Path("/mnt/sscans")
    mnt.mkdir(parents=True, exist_ok=True)
    run(["mount", "-o", "ro", str(dev), str(mnt)])
    try:
        raw = json.loads((mnt / "answers.json").read_text())
    finally:
        run(["umount", str(mnt)], check=False)
    disks = [d for d in disks if d["path"] != str(dev.resolve()) and not str(dev.resolve()).startswith(d["path"])]
    return finish(raw, disks, run)


if __name__ == "__main__":
    sys.exit(main())
