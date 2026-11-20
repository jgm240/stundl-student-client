#!/bin/bash
# Builds a self-contained "Stundl Student Client.app" (no Python or venv needed to RUN it) and signs it.
# Usage: ./build_app.sh [signing identity]   default: first valid code signing identity, "-" = ad-hoc
set -euo pipefail
cd "$(dirname "$0")"
ID="${1:-$(security find-identity -v -p codesigning | sed -n 's/.*"\(.*\)"/\1/p' | head -1)}"; ID="${ID:--}"
BUILD="$(mktemp -d)"                       # throwaway build environment, deleted at the end
python3 -m venv "$BUILD/venv"
"$BUILD/venv/bin/pip" install -q --upgrade pip
"$BUILD/venv/bin/pip" install -q pyinstaller -r requirements.txt
"$BUILD/venv/bin/pyinstaller" --noconfirm --windowed --name "Stundl Student Client" \
  --osx-bundle-identifier com.stundl.ssc \
  --distpath dist --workpath "$BUILD/work" --specpath "$BUILD" \
  --icon "$PWD/assets/icon.icns" --add-data "$PWD/index.html:." --add-data "$PWD/parent.html:." --add-data "$PWD/assets/menubar.png:." \
  --collect-all playwright --collect-all textual --collect-all rich \
  --collect-submodules webview --collect-submodules rumps \
  --hidden-import vault --hidden-import keyring.backends.macOS --collect-submodules keyring --hidden-import segno --hidden-import tui --hidden-import menubar --hidden-import core \
  --hidden-import objc --hidden-import AppKit --hidden-import Foundation --hidden-import WebKit \
  app_main.py
APP="dist/Stundl Student Client.app"
/usr/libexec/PlistBuddy -c "Add :NSHighResolutionCapable bool true" "$APP/Contents/Info.plist" 2>/dev/null || true
codesign --force --deep --sign "$ID" --timestamp=none "$APP"
codesign --verify --deep --strict "$APP" && echo "Built and signed ($ID): $PWD/$APP"
rm -rf "$BUILD"
