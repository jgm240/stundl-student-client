"""Pure planning helpers for ssc-install (no disk access here, so they are unit-tested)."""
import json
import re

MIN_BYTES = 4 * 1024 ** 3


def parse_lsblk(text, live_disks=()):
    """`lsblk -J -b -d -o NAME,SIZE,MODEL,TYPE,RO,RM` -> installable disks [{path,size,model,removable}]."""
    out = []
    for d in json.loads(text or "{}").get("blockdevices", []):
        name = d["name"] if d["name"].startswith("/dev/") else "/dev/" + d["name"]
        if d.get("type") != "disk" or str(d.get("ro")) in ("1", "True", "true") or name in live_disks:
            continue
        size = int(d.get("size") or 0)
        if size < MIN_BYTES or re.match(r"/dev/(loop|sr|ram|zram|fd)", name):
            continue
        out.append(dict(path=name, size=size, model=(d.get("model") or "").strip(), removable=str(d.get("rm")) in ("1", "True", "true")))
    return out


def human(n):
    for u in ("B", "KB", "MB", "GB", "TB"):
        if n < 1000 or u == "TB":
            return f"{n:.0f} {u}" if u == "B" else f"{n:.1f} {u}"
        n /= 1000


def part(disk, n):
    """/dev/sda + 3 -> /dev/sda3 ; /dev/nvme0n1 + 3 -> /dev/nvme0n1p3 ; /dev/mmcblk0 -> ...p3"""
    return f"{disk}p{n}" if disk[-1].isdigit() else f"{disk}{n}"


def layout(arch, efi):
    """Partition table: GPT with a BIOS-boot partition (always), an EFI system partition (amd64 only), then root."""
    parts = [dict(n=1, size="1MiB", code="EF02", name="biosboot", fs=None)]
    if arch == "amd64":
        parts.append(dict(n=2, size="256MiB", code="EF00", name="efi", fs="vfat"))
    parts.append(dict(n=len(parts) + 1, size=None, code="8300", name="root", fs="ext4"))
    return parts


def partition_commands(disk, arch="amd64"):
    cmds = [["sgdisk", "--zap-all", disk]]
    for p in layout(arch, True):
        cmds.append(["sgdisk", f"-n{p['n']}:0:{('+' + p['size']) if p['size'] else '0'}", f"-t{p['n']}:{p['code']}", f"-c{p['n']}:{p['name']}", disk])
    return cmds


def mkfs_commands(disk, arch="amd64"):
    cmds = []
    for p in layout(arch, True):
        if p["fs"] == "vfat":
            cmds.append(["mkfs.vfat", "-F", "32", "-n", "SSCEFI", part(disk, p["n"])])
        elif p["fs"] == "ext4":
            cmds.append(["mkfs.ext4", "-F", "-q", "-L", "ssc-root", part(disk, p["n"])])
    return cmds


def fstab(root_uuid, efi_uuid=None):
    lines = ["# /etc/fstab - written by ssc-install", f"UUID={root_uuid} / ext4 errors=remount-ro,noatime 0 1"]
    if efi_uuid:
        lines.append(f"UUID={efi_uuid} /boot/efi vfat umask=0077 0 2")
    return "\n".join(lines) + "\n"


RSYNC_EXCLUDES = ["/proc/*", "/sys/*", "/dev/*", "/run/*", "/tmp/*", "/mnt/*", "/media/*", "/lost+found", "/var/lib/ssc/*",
                  "/etc/fstab", "/etc/machine-id", "/var/lib/dbus/machine-id", "/root/.bash_history", "/var/log/*.log"]


def validate_answers(a, disks):
    """Answers for the installer (interactive or from the answers disk) -> normalized dict, or ValueError."""
    import serverconf
    import auth
    out = {}
    paths = [d["path"] for d in disks]
    disk = a.get("disk", "auto")
    if disk == "auto":
        if len(paths) != 1:
            raise ValueError("disk=auto needs exactly one installable disk (found %d)" % len(paths))
        disk = paths[0]
    if disk not in paths:
        raise ValueError(f"disk {disk} is not an installable disk")
    out["disk"] = disk
    out["hostname"] = serverconf.validate({"network": {"hostname": a.get("hostname", "ssc-server")}})["network"]["hostname"]
    out["admin"] = str(a.get("admin", "admin")).strip().lower()
    out["password"] = a.get("password", "")
    try:
        auth._check_name(out["admin"])
        auth._check_password("admin", out["password"])
    except auth.AuthError as e:
        raise ValueError(str(e))
    net = a.get("network", {})
    out["network"] = serverconf.validate({"network": {**net, "hostname": out["hostname"]}})["network"]
    out["timezone"] = a.get("timezone", "UTC")
    if not re.match(r"^[A-Za-z0-9_+./-]{1,40}$", out["timezone"]) or ".." in out["timezone"]:
        raise ValueError("timezone looks invalid")
    out["keymap"] = a.get("keymap", "us")
    if not re.match(r"^[a-z0-9_-]{1,20}$", out["keymap"]):
        raise ValueError("keymap looks invalid")
    out["ssh"] = bool(a.get("ssh", True))
    return out
