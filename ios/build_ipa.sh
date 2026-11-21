#!/bin/bash
# Builds the iOS apps as UNSIGNED IPAs:  SSC-SAFE-unsigned.ipa  and  SSC-Mobile-unsigned.ipa
# Needs a Mac with full Xcode (the iOS SDK) and xcodegen:  brew install xcodegen
# No Xcode? Use the GitHub workflow in .github/workflows/ios-ipa.yml - GitHub's macOS servers build them for you.
# An unsigned IPA must be signed by a sideloading tool (AltStore, Sideloadly, ...) before it installs.
set -euo pipefail
cd "$(dirname "$0")"
command -v xcodegen >/dev/null || { echo "Install xcodegen first: brew install xcodegen"; exit 1; }
xcrun --sdk iphoneos --show-sdk-path >/dev/null 2>&1 || { echo "The iOS SDK was not found - this needs full Xcode, not just the command line tools."; exit 1; }
xcodegen generate
rm -rf build Payload *.ipa
for pair in "SSCSafe:SSC-SAFE" "SSCMobile:SSC-Mobile"; do
  scheme="${pair%%:*}"; product="${pair##*:}"
  xcodebuild -project SSC.xcodeproj -scheme "$scheme" -configuration Release -sdk iphoneos -destination 'generic/platform=iOS' \
    -derivedDataPath "build/$scheme" CODE_SIGNING_ALLOWED=NO CODE_SIGNING_REQUIRED=NO CODE_SIGN_IDENTITY="" build
  mkdir Payload
  cp -R "build/$scheme/Build/Products/Release-iphoneos/$product.app" Payload/
  zip -qry "$product-unsigned.ipa" Payload
  rm -rf Payload
  echo "Built: $PWD/$product-unsigned.ipa"
done
