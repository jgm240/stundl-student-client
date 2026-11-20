#!/bin/bash
# Builds and signs the SSC widget host app (host + widget extension) with the command line tools only - no Xcode needed.
# Usage: ./build.sh [signing identity name or hash]   (default: first valid code signing identity; "-" = ad-hoc)
set -euo pipefail
cd "$(dirname "$0")"
ID="${1:-$(security find-identity -v -p codesigning | sed -n 's/.*"\(.*\)"/\1/p' | head -1)}"
ID="${ID:--}"
ARCH="$(uname -m)"; T="$ARCH-apple-macos14.0"
APP=build/SSC.app; EXT="$APP/Contents/PlugIns/SSCWidget.appex"
rm -rf build; mkdir -p "$APP/Contents/MacOS" "$EXT/Contents/MacOS"

swiftc -O -parse-as-library -target "$T" App/HostApp.swift -o "$APP/Contents/MacOS/SSC"
swiftc -O -parse-as-library -application-extension -target "$T" Widget/SSCWidget.swift \
  -o "$EXT/Contents/MacOS/SSCWidget" -Xlinker -e -Xlinker _NSExtensionMain

cat > "$APP/Contents/Info.plist" <<P
<?xml version="1.0" encoding="UTF-8"?><!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
<key>CFBundleIdentifier</key><string>com.stundl.ssc.host</string>
<key>CFBundleExecutable</key><string>SSC</string>
<key>CFBundleName</key><string>SSC Widget</string>
<key>CFBundlePackageType</key><string>APPL</string>
<key>CFBundleShortVersionString</key><string>1.0</string><key>CFBundleVersion</key><string>1</string>
<key>LSMinimumSystemVersion</key><string>14.0</string><key>NSPrincipalClass</key><string>NSApplication</string>
</dict></plist>
P
cat > "$EXT/Contents/Info.plist" <<P
<?xml version="1.0" encoding="UTF-8"?><!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
<key>CFBundleIdentifier</key><string>com.stundl.ssc.host.widget</string>
<key>CFBundleExecutable</key><string>SSCWidget</string>
<key>CFBundleName</key><string>SSCWidget</string>
<key>CFBundleDisplayName</key><string>SSC</string>
<key>CFBundlePackageType</key><string>XPC!</string>
<key>CFBundleShortVersionString</key><string>1.0</string><key>CFBundleVersion</key><string>1</string>
<key>LSMinimumSystemVersion</key><string>14.0</string>
<key>NSExtension</key><dict><key>NSExtensionPointIdentifier</key><string>com.apple.widgetkit-extension</string></dict>
</dict></plist>
P
cat > build/sandbox.entitlements <<P
<?xml version="1.0" encoding="UTF-8"?><!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict><key>com.apple.security.app-sandbox</key><true/></dict></plist>
P

# sign inside-out: extension first, then the app
codesign --force --sign "$ID" --entitlements build/sandbox.entitlements --timestamp=none "$EXT"
codesign --force --sign "$ID" --entitlements build/sandbox.entitlements --timestamp=none "$APP"
codesign --verify --deep --strict --verbose=2 "$APP"
echo "Built and signed with: $ID  ->  $PWD/$APP"
