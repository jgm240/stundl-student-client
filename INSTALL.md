# Installing Stundl Student Client (SSC)

Builds for every platform are made by GitHub Actions: open the repository's **Actions** tab, pick the latest successful run
of *Desktop tests and builds* (Windows, Linux) or *Build unsigned iOS IPAs* (iPhone/iPad), and download the artifact
(you need to be signed in to GitHub). Or build it yourself - see the bottom.

All programs are **unsigned** (no paid developer certificates), so your OS will warn you the first time.

## Windows
1. Download `SSC-Windows` → unzip `SSC-windows.zip` somewhere permanent (e.g. `C:\Program Files\SSC` or your user folder).
2. Double-click `SSC\SSC.exe`. SmartScreen says "Windows protected your PC" → **More info → Run anyway**.
3. First start: pick your school, choose a browser (Chrome/Edge are used if installed, otherwise Chromium is downloaded
   once), sign in in the window that opens.
4. Command line / terminal view: in Windows Terminal run `ssc-cli\ssc-cli.exe tui` (or `ssc-cli.exe -h`).
5. Optional background jobs (Task Scheduler): `ssc-cli.exe schedule install` (daily sync + reminders),
   `ssc-cli.exe menubar install` (tray icon at sign-in), `ssc-cli.exe parent install` (Parent mode).
Secrets are kept in **Windows Credential Manager**. Needs the Edge WebView2 runtime (already on Windows 11 and current
Windows 10) for the window.

## Linux (GUI and terminal)
1. Download `SSC-Linux` → `tar xzf SSC-linux.tar.gz`.
2. `./ssc/ssc` opens the window; **without a desktop session it opens the terminal view automatically**. Force either:
   `./ssc/ssc gui` or `./ssc/ssc tui`.
3. Secrets need a **Secret Service keyring** (GNOME Keyring, KWallet or KeePassXC), unlocked. SSC never stores secrets
   in plain files; without a keyring you'll be told to set one up.
4. Optional: `./ssc/ssc schedule install` (systemd user timers), `./ssc/ssc parent install` (systemd user service),
   `./ssc/ssc menubar install` (tray icon autostart; GNOME needs the AppIndicator extension).
5. If the window doesn't open, install Qt's system libraries: `sudo apt install libegl1 libxkbcommon-x11-0 libxcb-cursor0`.
Notifications use `notify-send` when present. Everything runs in your user account; no root needed.

## macOS
Build and sign it with your own certificate: `./build_app.sh` → `dist/Stundl Student Client.app`.

## iPhone / iPad: SSC Mobile (autonomous) and SSC-SAFE (Parent-mode client)
1. Download the artifact `SSC-iOS-unsigned-ipas` and unzip it: `SSC-Mobile-unsigned.ipa`, `SSC-SAFE-unsigned.ipa`.
2. Unsigned IPAs must be signed by a sideloading tool with your own Apple ID: **Sideloadly** or **AltStore** (free Apple
   ID: apps last 7 days, then re-sign; max 3 at once), or **TrollStore** where supported.
3. **SSC Mobile**: needs the Untis Mobile QR code - in WebUntis (computer): your name → Profile → *Freigaben / Access* →
   *Untis Mobile* → show QR code. Scan it in the app. The key is stored in the iOS Keychain, this device only, behind
   Face ID / passcode (a device passcode must be set), and can't be shown or exported.
   If your school has Untis Mobile switched off, use SSC-SAFE instead.
4. **SSC-SAFE**: paste the Parent link from the Mac (SSC → Settings → Parent mode).

## Home Assistant
See `homeassistant/README.md`: HACS custom repository `https://github.com/jgm240/stundl-student-client` (Integration),
restart, then *Add integration → Stundl Student Client (SSC)* and choose **Autonomous** (Untis Mobile QR text) or **Parent link**.

## Build it yourself
```bash
pip install -r requirements.txt pyinstaller
python build.py                 # Windows / Linux  -> dist/
./build_app.sh                  # macOS (signs with your certificate)
./ios/build_ipa.sh              # iOS, needs full Xcode + xcodegen -> ios/*.ipa
```
Run the tests: `python -m unittest discover -s tests -v`.
