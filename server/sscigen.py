#!/usr/bin/env python3
"""sscigen - make an autoinstall.ssci (SSC Install file) by answering the installer's questions.

  sscigen                        ask everything, write ./autoinstall.ssci
  sscigen -o /Volumes/USB/autoinstall.ssci
  sscigen --edit old.ssci        keep what the file already says and ask only what is missing
  sscigen --check autoinstall.ssci   check a file without installing anything
  sscigen --example              print a commented example

It is the installer's question list, but it installs nothing: the answers are written to a plain-text file. Put the file on a USB
stick (or any attached drive); a machine booted from the SSC Server installer then installs by itself with these settings
(settings that are not in the file are asked on screen). The file contains passwords in clear text: keep it private.
Runs on Linux, macOS and Windows (WSL); with whiptail installed it looks like the installer, otherwise it uses plain prompts.
"""
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))

import install_plan as plan  # noqa: E402
import qa  # noqa: E402
import ssci_file  # noqa: E402

FAKE_DISK = [{"path": "/dev/sda", "size": 64 * 10 ** 9, "model": "any", "removable": False}]


def check_text(text):
    """-> (problems, notes). A file is fine when the installer would accept it (disk checks aside: they depend on the target machine)."""
    try:
        a, extras = ssci_file.load(text)
    except ssci_file.SsciError as e:
        return [str(e)], []
    notes, problems = [], []
    missing = [k for k in ("disk", "hostname", "admin", "password", "timezone", "keymap", "ssh", "autologin", "network") if k not in a]
    if missing:
        notes.append("not in the file (asked on screen during the install, or defaults with 'ask = no'): " + ", ".join(missing))
    probe = {**a, "disk": a.get("disk", "auto"), "password": a.get("password") or "placeholder-ok"}
    try:
        plan.validate_answers(probe, FAKE_DISK)
    except ValueError as e:
        problems.append(str(e))
    if extras["erase"]:
        notes.append("erase = yes: the target disk is erased without asking again")
    elif not missing:
        problems.append("a complete file needs 'erase = yes' to install unattended (otherwise the installer asks for the confirmation)")
    return problems, notes


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    if "-h" in args or "--help" in args:
        print(__doc__)
        return 0
    if "--example" in args:
        print(ssci_file.EXAMPLE)
        return 0
    if "--check" in args:
        path = args[args.index("--check") + 1] if len(args) > args.index("--check") + 1 else None
        if not path:
            print("usage: sscigen --check FILE")
            return 2
        problems, notes = check_text(Path(path).read_text(encoding="utf-8", errors="replace"))
        for n in notes:
            print("note:", n)
        for p in problems:
            print("PROBLEM:", p)
        print("OK" if not problems else "Not OK")
        return 0 if not problems else 1
    out = Path(args[args.index("-o") + 1] if "-o" in args else args[args.index("--output") + 1] if "--output" in args else "autoinstall.ssci")
    preset, old_extras = None, None
    if "--edit" in args:
        src = Path(args[args.index("--edit") + 1])
        preset, old_extras = ssci_file.load(src.read_text(encoding="utf-8"))
    ui = qa.make_ui("sscigen - SSC Install file generator")
    try:
        a, extras = qa.interview(ui, preset, disks=None, file_mode=True, extras_preset=old_extras)
    except qa.Cancelled:
        print("Cancelled - nothing was written.")
        return 1
    text = ssci_file.dump(a, extras)
    problems, notes = check_text(text)
    if problems:
        print("The answers don't add up:")
        for p in problems:
            print("  -", p)
        return 1
    ssci_file.write(out, text)
    ui.msgbox(f"Written: {out}\n\nPut it on a USB stick (the file name must stay autoinstall.ssci), boot the machine from the SSC Server "
              f"installer with the stick attached, and it installs by itself.\n\nThe file holds passwords in clear text: keep it private.")
    print(f"Wrote {out} (readable by you only).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
