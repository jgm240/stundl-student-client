#!/bin/bash
# Builds Stundl Safe Client (SSC-SAFE) as an UNSIGNED iOS app and packs it as SSC-SAFE-unsigned.ipa.
# Needs a Mac with full Xcode (the iOS SDK) and xcodegen:  brew install xcodegen
# No Xcode? Use the GitHub workflow in .github/workflows/ios-ipa.yml - GitHub's macOS servers build it for you.
# The IPA is not signed: install it with a sideloading tool that signs it for you (AltStore, Sideloadly, ...).
set -euo pipefail
cd "$(dirname "$0")"
command -v xcodegen >/dev/null || { echo "Install xcodegen first: brew install xcodegen"; exit 1; }
xcrun --sdk iphoneos --show-sdk-path >/dev/null 2>&1 || { echo "The iOS SDK was not found - this needs full Xcode, not just the command line tools."; exit 1; }
xcodegen generate
rm -rf build Payload SSC-SAFE-unsigned.ipa
xcodebuild -project SSCSafe.xcodeproj -scheme SSCSafe -configuration Release -sdk iphoneos -destination 'generic/platform=iOS' \
  -derivedDataPath build CODE_SIGNING_ALLOWED=NO CODE_SIGNING_REQUIRED=NO CODE_SIGN_IDENTITY="" build
mkdir Payload
cp -R "build/Build/Products/Release-iphoneos/SSC-SAFE.app" Payload/
zip -qry SSC-SAFE-unsigned.ipa Payload
rm -rf Payload
echo "Built: $PWD/SSC-SAFE-unsigned.ipa"
