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
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))
os.environ.setdefault("SSC_VAULT", "file")

import install_plan as plan  # noqa: E402
import qa  # noqa: E402
import serverconf  # noqa: E402
import ssci_file  # noqa: E402

TARGET = Path("/target")
SOURCE = "/run/live/rootfs/filesystem.squashfs/"


def _log(m):
    """To the screen this program runs on, and also to the system console (the serial console in a VM) when those differ."""
    print(m, flush=True)
    try:
        if os.ttyname(1) != "/dev/console":
            with open("/dev/console", "w") as c:
                c.write(m + "\n")
    except OSError:
        pass


class Runner:
    def __init__(self, dry=False, log=None):
        self.dry, self.log = dry, log or _log

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
    must_exist = ("usr/local/bin/ssconfig", "usr/local/bin/ssc-install", "usr/local/bin/ssc-imgen", "opt/ssc/ssc.py", "opt/ssc/server/ssconfig.py",
                  "opt/ssc/server/serverapp.py", "etc/systemd/system/ssc-server.service", "etc/systemd/logind.conf.d/ssc-power.conf")
    if not run.dry:                  # the copy must really contain the SSC tools, otherwise the new server can't be configured
        missing = [m for m in must_exist if not (root / m).exists()]
        if missing:
            raise RuntimeError("the installed system is missing: " + ", ".join(missing))
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
        # SSC state: the validated settings + the accounts, created with the real code against the target's state directory
        state = str(root / "var/lib/ssc")
        env = {"SSC_DATA_DIR": state, "SSC_VAULT": "file", "SSC_NEW_PASSWORD": ans["password"]}
        save = "import sys,json;sys.path.insert(0,sys.argv[1]);import serverconf;serverconf.save(json.loads(sys.argv[2]))"
        cfg = json.loads(json.dumps(ans["settings"]))
        if ans.get("wifi"):
            cfg["wifi"] = {k: v for k, v in ans["wifi"].items() if k != "psk"}
        run([sys.executable, "-c", save, str(HERE), json.dumps(cfg)], env=env)
        run([sys.executable, str(HERE / "ssconfig.py"), "account", "add", ans["admin"], "admin"], env=env)
        for name, role, pw in ans.get("accounts", []):
            run([sys.executable, str(HERE / "ssconfig.py"), "account", "add", name, role], env={**env, "SSC_NEW_PASSWORD": pw})
        if ans.get("untis"):               # the WebUntis key goes straight into the new server's private store
            run([sys.executable, "-c", "import sys,json;sys.path.insert(0,sys.argv[1]);sys.path.insert(0,sys.argv[2]);import untis_direct,core;"
                 "untis_direct.save_credentials(json.loads(sys.argv[3]));core.save_config({**core.config(),'sync_mode':'direct'})",
                 str(HERE.parent), str(HERE), json.dumps(ans["untis"])], env=env)
        ch("chown", "-R", "ssc:ssc", "/var/lib/ssc")
        # system files from the same validated settings (templates only)
        import applycfg
        full = serverconf.validate(cfg)
        noop = lambda c: subprocess.CompletedProcess(c, 0, "", "")
        parts = ["network", "ssh", "firewall", "power"] + (["schedule"] if full["schedule"]["enabled"] else [])
        if not run.dry:
            if ans.get("wifi") and ans["wifi"].get("psk"):
                serverconf.stage_wifi_psk(ans["wifi"]["ssid"], ans["wifi"]["psk"])
            for part in parts[:3]:
                applycfg.apply_part(part, full, root, noop)
        # Linux login account (console + SSH): same name and password as the web admin
        ch("useradd", "-m", "-s", "/bin/bash", "-G", "sudo", ans["admin"])
        ch("chpasswd", input=f"{ans['admin']}:{ans['password']}\n")
        ch("passwd", "-l", "root")
        if not run.dry:                    # these need the Linux login account (console auto-login) or the target's own /sys (RTC)
            for part in parts[3:] + (["console"] if full["console"]["autologin_user"] else []):
                applycfg.apply_part(part, full, root, noop)
        if full["schedule"]["enabled"]:
            ch("systemctl", "enable", "ssc-sleep.timer", *(["ssc-wake.timer"] if full["schedule"]["mode"] == "quiet" else []), check=False)
        if not full["power"]["block_sleep"]:
            ch("systemctl", "unmask", *serverconf.SLEEP_TARGETS, check=False)
        ch("systemctl", "enable", "ssc-server", "ssc-apply.path", "ssc-banner", "nftables", "ssh", "ssc-sync.timer", check=False)

        # live-medium-only units must not exist on the installed system (they are skipped there, but the menu unit's old Conflicts=
        # used to cancel the console login at every boot)
        if not run.dry:
            for name in ("ssc-live-console.service", "ssc-autoinstall.service"):
                (root / "etc/systemd/system" / name).unlink(missing_ok=True)
                (root / "etc/systemd/system/multi-user.target.wants" / name).unlink(missing_ok=True)
            (root / "usr/local/sbin/ssc-live-menu").unlink(missing_ok=True)
        # the installed system must show a login prompt on the screen (tty1), not a blank cursor
        ch("systemctl", "enable", "getty@tty1.service", check=False)
        if not run.dry and not (root / "etc/systemd/system/getty.target.wants/getty@tty1.service").exists():
            raise RuntimeError("the console login (getty@tty1) is not enabled in the installed system")
        if not run.dry and (root / "etc/systemd/system/ssc-live-console.service").exists():
            raise RuntimeError("the live-only console unit is still on the installed system")

        step("Removing the live-system parts")
        ch("sh", "-c", "apt-get -y purge live-boot live-boot-initramfs-tools live-config live-config-systemd live-config-sysvinit 2>/dev/null || "
                       "dpkg --purge --force-depends live-boot live-boot-initramfs-tools live-config live-config-systemd || true")
        if not run.dry:   # the image ships no /etc/default/grub, so write the whole file
            (root / "etc/default").mkdir(parents=True, exist_ok=True)
            (root / "etc/default/grub").write_text(
                'GRUB_DEFAULT=0\nGRUB_TIMEOUT=2\nGRUB_DISTRIBUTOR="SSC Server"\nGRUB_CMDLINE_LINUX_DEFAULT=""\n'
                'GRUB_CMDLINE_LINUX="console=tty0 console=ttyS0,115200"\nGRUB_DISABLE_OS_PROBER=true\n')
            # UEFI only: hand the firmware framebuffer on to the kernel, otherwise the screen stays blank (only a cursor).
            # A condition in grub.cfg, so the same disk still boots fine in BIOS mode.
            g = root / "etc/grub.d/09_ssc_efi"
            g.write_text("#!/bin/sh\ncat <<'EOF'\nif [ \"${grub_platform}\" = \"efi\" ]; then\n  insmod efi_gop\n  set gfxpayload=keep\nfi\nEOF\n")
            g.chmod(0o755)

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


def has_wifi_card():
    try:
        return any((Path("/sys/class/net") / n / "wireless").exists() for n in os.listdir("/sys/class/net"))
    except OSError:
        return False


def ask(disks, preset=None, erase_given=False):
    """The installer's questions (shared with sscigen); anything already in `preset` is not asked again. -> (answers, extras) or None."""
    try:
        return qa.interview(qa.make_ui("SSC Server installer"), preset, disks=disks, file_mode=False, wifi_card=has_wifi_card(), erase_given=erase_given)
    except qa.Cancelled:
        return None


REQUIRED = ("disk", "hostname", "admin", "password", "timezone", "keymap", "ssh", "autologin", "network")


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    if "--example" in args:
        print(ssci_file.EXAMPLE)
        return 0
    if "--check" in args:
        import sscigen
        return sscigen.main(args)
    dry = "--dry-run" in args
    if os.geteuid() != 0 and not dry:
        print("ssc-install must run as root")
        return 1
    run = Runner(dry)
    disks = find_disks() if not dry else [{"path": "/dev/sda", "size": 32 * 10 ** 9, "model": "dry", "removable": False}]
    if "--auto" in args:
        return auto(run, disks)
    res = ask(disks)
    if not res:
        print("Cancelled - nothing was changed.")
        return 1
    return finish(res[0], disks, run, interactive=True)


def finish(raw, disks, run, interactive=False, action="poweroff"):
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
        subprocess.run(["reboot" if action == "reboot" or not action else action])
    else:
        time.sleep(5)          # let the last log lines reach the console before the machine goes down
        subprocess.run(["reboot" if action == "reboot" else "poweroff"])
    return 0


# ------------------------------------------------------------------ autoinstall.ssci
def _lsblk_tree():
    return subprocess.run(["lsblk", "-J", "-p", "-o", "NAME,FSTYPE,TYPE,PKNAME,MOUNTPOINT"], capture_output=True, text=True).stdout


def find_ssci(attempts=3, pause=4):
    """Look for autoinstall.ssci on the installer medium and on every other attached drive (read-only, never executed).
    -> (text, device, disk) or None. More than one file is an error: which one is meant?"""
    base = Path("/run/live/medium")
    text = ssci_file.read_from(base) if base.is_dir() else None
    if text is not None:
        return text, str(base), ""
    found = []
    for attempt in range(attempts):
        subprocess.run(["udevadm", "settle", "--timeout=10"], capture_output=True)
        found = []
        for dev, fs, disk in ssci_file.candidates(_lsblk_tree()):
            mnt = Path(tempfile.mkdtemp(prefix="ssci-"))
            r = subprocess.run(["mount", "-o", "ro,noexec,nosuid,nodev", *(["-t", "ntfs3"] if fs == "ntfs" else []), dev, str(mnt)], capture_output=True)
            try:
                if r.returncode == 0:
                    t = ssci_file.read_from(mnt)
                    if t is not None:
                        found.append((t, dev, disk))
            finally:
                if r.returncode == 0:
                    subprocess.run(["umount", str(mnt)], capture_output=True)
                shutil.rmtree(mnt, ignore_errors=True)
        if found:
            break
        time.sleep(pause)                      # USB sticks can show up a few seconds after boot
    if len(found) > 1:
        raise ssci_file.SsciError("found more than one autoinstall.ssci (" + ", ".join(f[1] for f in found) + ") - remove all but one")
    return found[0] if found else None


def countdown(seconds, what, log=print):
    for left in range(seconds, 0, -1):
        if left % 5 == 0 or left <= 5:
            log(f"AUTOINSTALL: {what} in {left} s - power the machine off now to cancel")
        time.sleep(1)


def auto(run, disks):
    try:
        found = find_ssci()
        if found is None:
            print("no autoinstall.ssci found")
            return 3
        text, device, source_disk = found
        a, extras = ssci_file.load(text)
    except ssci_file.SsciError as e:
        print("autoinstall.ssci:", e)
        return 1
    print(f"AUTOINSTALL: using the SSC Install file on {device}")
    disks = [d for d in disks if d["path"] != source_disk]          # the drive the file came from is never erased
    missing = [k for k in REQUIRED if k not in a]
    interactive = bool(missing) and extras["ask"]
    if interactive:                                                   # whatever the file does not say is asked, as usual
        res = ask(disks, preset=a, erase_given=extras["erase"])
        if not res:
            print("Cancelled - nothing was changed.")
            return 1
        a = res[0]
    else:
        if not extras["erase"]:
            print("autoinstall.ssci: a file without questions needs  erase = yes  (the target disk is erased) - nothing was changed")
            return 1
        try:
            probe = plan.validate_answers(a, disks)               # fail before the countdown, not after
        except ValueError as e:
            print("autoinstall.ssci:", e)
            return 1
        countdown(extras["wait"], f"erasing {probe['disk']} and installing", run.log)
    return finish(a, disks, run, interactive=interactive, action=extras["finish"])


if __name__ == "__main__":
    sys.exit(main())
