# Stundl Student Client (SSC)

**Website & live demo: https://jgm240.github.io/stundl-student-client/** · **Downloads: [Releases](https://github.com/jgm240/stundl-student-client/releases)**

English speakers can call it **SSC Student Client** (the word Stundl is hard to pronunce in English)

An unofficial, independent macOS app that downloads your own WebUntis data (timetable, homework, exams, grades,
absences) so you can use it offline: GUI, terminal view, menu bar widget, a read-only "Parent mode" website for
wall displays, and your own study plan.

**Not affiliated with or endorsed by Untis GmbH (WebUntis) or Microsoft.** It reads only your own account, using your
own sign-in in a browser window; your password is never seen or stored. WebUntis' internal endpoints are
undocumented and may change or be restricted by your school.

## Install
See `INSTALL.md` for Windows, Linux (GUI + terminal), iPhone (SSC Mobile / SSC-SAFE) and Home Assistant.

## Build
```bash
./build_app.sh          # builds and signs "dist/Stundl Student Client.app" (no Python needed to run it)
```
Data lives in `~/.ssc` (secrets are in the macOS Keychain). Older installs are migrated automatically on first start.
Run `"dist/Stundl Student Client.app/Contents/MacOS/Stundl Student Client" -h` for all commands.

## SSC-SAFE (Stundl Safe Client) for iOS
`ios/` is an iOS app that is *only* a client of Parent mode: it shows the read-only Parent page and keeps a cached
copy of its data for offline use. (`SSC Mobile`, in the same folder, is the autonomous variant: it logs in to WebUntis itself with the Untis Mobile key.) It never asks for or sends WebUntis credentials; the only thing it holds
is the secret Parent link (in the Keychain). Build an **unsigned** IPA with full Xcode (`ios/build_ipa.sh`) or, without
Xcode, run the "Build unsigned SSC-SAFE IPA" GitHub Action and download the artifact. Unsigned IPAs must be signed by a
sideloading tool (AltStore, Sideloadly, ...) before they install.

## Home Assistant
`custom_components/ssc_student_client` is a custom integration (HACS-ready) that fetches the Parent-mode data by itself, caches it, and exposes sensors,
binary sensors and a calendar. Setup guide: `homeassistant/README.md`.

## SSC Server (ISO)
A Debian-based, installable server image (64-bit and 32-bit) with guest/user/admin accounts, an admin panel and the `ssconfig` tool (`ssconfig showip`, `ssconfig showlink`, custom IP, required long links, SSH settings). See `server/README.md`.

## PineTime / JWatch watch
`ssc pinetime scan|pair|sync` sets the watch clock and sends notifications over Bluetooth. On the JWatch firmware it also fills the **School** screen (next lessons, exams `Exam:`, homework `HW:`). Not tested on a real watch. Needs `pip install bleak`.

## Access cards (`ssc-imgen`)
`./ssc-imgen --user anna` (or `ssc imgen ...`, or `ssc-imgen` on the server ISO) makes a printable card like the Wi-Fi card in a hotel room: the SSC logo, `ip:port` (detected automatically on a server), user name, password, optional `--notes` and a QR code that opens the address. `.png`, `.jpg` and `.pdf`, light or `--dark`; `--lang de|fr|es|...` for 12 languages; `--demo` covers every value (and the QR code) with `[REDACTED]` banners for screenshots and demos - nothing of the real values is drawn, and the banners have a fixed size, so not even the password length leaks; `--network "Home WiFi"` shows the Wi-Fi network to join in the card header; `--cellularwarn` adds an amber "Connect to WiFi instead of mobile data to access SSC" notice (the address only works on your home network). The password is asked for without echo (or `--password-stdin`); it is only drawn on the picture, never stored, and the file is private to you (mode 600). Needs Pillow; the QR code needs segno.

## Languages, themes and colors
Every user picks their own **language** (English, Deutsch, Français, Español, Italiano, Português, Nederlands, Polski), **mode** (system / light / dark), **theme** (Default, Paper, Ocean, Forest, Midnight, Rose, Graphite) and **accent color** (8) with the palette button in the top bar (or Settings -> Appearance). On SSC Server the choice is saved per account; in the desktop app it is saved in your settings; the Parent page remembers it in the browser. The translations are mine and have not been checked by native speakers; the admin panel and the long help texts in Settings are English only.

## Updates
Settings -> Updates (or `ssc update`) checks this project's GitHub releases - only when you ask. The desktop app downloads the file for your system and keeps it only if its SHA-256 matches the release's `SHA256SUMS`; you then replace the app. An SSC Server installs the new code itself (`ssconfig update --install`, or the Overview tab of the admin panel): staged, compile- and import-checked, with the old version kept for `ssconfig update --rollback`.

## Security
No password is ever stored. The WebUntis session and the Parent link secret are kept in the macOS Keychain, not in
files; the data folder is private to your account; the local app server only answers the window the app opened; browser
sign-in sessions are deleted after each login/sync; the Parent site is read-only and answers only your home network.

## License
MIT - see `LICENSE`. Third-party notices: `THIRD_PARTY_LICENSES.md`.
