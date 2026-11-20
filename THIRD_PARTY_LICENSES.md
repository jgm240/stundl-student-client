# Third-party software

SSC is MIT-licensed (see `LICENSE`). It uses the following open-source packages, all under permissive licenses.
They are installed by `pip install -r requirements.txt` and are bundled into the app you build with `build_app.sh`;
if you distribute that built app, keep their license notices (each package's own license file ships with it).

| Package | License |
|---|---|
| Playwright (Python) | Apache-2.0 |
| Textual, Rich | MIT |
| pywebview | BSD-3-Clause |
| rumps | BSD-3-Clause |
| segno | BSD-3-Clause |
| keyring (and jaraco.*, more-itertools) | MIT |
| PyObjC | MIT |
| greenlet, pyee | MIT |
| Pillow (icon scripts only; not bundled) | MIT-CMU (HPND) |

**Build tool:** PyInstaller is GPL-2.0-or-later *with a special exception* that explicitly allows distributing the
applications it builds under any license. It is only used to build the app and is not part of this source.

**Downloaded at run time, not part of this repository:** the Chromium / Firefox / WebKit browser builds that Playwright
downloads on first use are covered by their own licenses.

**Home Assistant integration:** runs inside Home Assistant (Apache-2.0) and imports its APIs.
