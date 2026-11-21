#!/usr/bin/env python3
"""Builds the SSC desktop app with PyInstaller on Windows or Linux (on macOS use build_app.sh, which also signs).

  Windows -> dist/SSC-windows.zip   (SSC.exe = the app, ssc-cli.exe = command line / terminal view)
  Linux   -> dist/SSC-linux.tar.gz  (one program: `ssc` opens the window, `ssc tui` the terminal view)

Needs:  pip install -r requirements.txt pyinstaller
"""
import os
import shutil
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
WIN = sys.platform.startswith("win")
SEP = ";" if WIN else ":"
DATA = ["index.html", "parent.html", "assets/menubar.png", "assets/icon.png"]
HIDDEN = ["vault", "jobs", "menu_model", "tray", "tui", "segno", "pystray", "keyring.backends.Windows" if WIN else "keyring.backends.SecretService"]


def pyinstaller(name, windowed):
    cmd = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--distpath", str(ROOT / "dist"), "--workpath", str(ROOT / "build" / name),
           "--specpath", str(ROOT / "build"), "--name", name, "--windowed" if windowed else "--console"]
    for d in DATA:
        cmd += ["--add-data", f"{ROOT / d}{SEP}."]
    if WIN:
        from PIL import Image
        ico = ROOT / "build" / "icon.ico"
        ico.parent.mkdir(exist_ok=True)
        Image.open(ROOT / "assets" / "icon.png").save(ico, sizes=[(256, 256), (64, 64), (48, 48), (32, 32), (16, 16)])
        cmd += ["--icon", str(ico)]
    for pkg in ("playwright", "textual", "rich"):
        cmd += ["--collect-all", pkg]
    for pkg in ("webview", "keyring", "pystray"):
        cmd += ["--collect-submodules", pkg]
    for h in HIDDEN:
        cmd += ["--hidden-import", h]
    cmd.append(str(ROOT / "app_main.py"))
    subprocess.run(cmd, check=True, cwd=ROOT)


def main():
    if sys.platform == "darwin":
        sys.exit("On macOS use ./build_app.sh (it also signs the app).")
    shutil.rmtree(ROOT / "dist", ignore_errors=True)
    if WIN:
        pyinstaller("SSC", windowed=True)          # double-click: window / tray, no console flashing
        pyinstaller("ssc-cli", windowed=False)     # for the command line and the terminal view
        out = ROOT / "dist" / "SSC-windows.zip"
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
            for folder in ("SSC", "ssc-cli"):
                for f in (ROOT / "dist" / folder).rglob("*"):
                    if f.is_file():
                        z.write(f, f.relative_to(ROOT / "dist"))
    else:
        pyinstaller("ssc", windowed=False)         # console build: the same program serves the window and the terminal view
        out = ROOT / "dist" / "SSC-linux.tar.gz"
        with tarfile.open(out, "w:gz") as t:
            t.add(ROOT / "dist" / "ssc", arcname="ssc")
    print("Built:", out)


if __name__ == "__main__":
    main()
