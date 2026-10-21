#!/usr/bin/env bash
# Regenerates docs/screenshots from a DEBUG build on an iPhone and an iPad simulator.
# Usage: scripts/screenshots.sh <iphone-udid> <ipad-udid>
set -euo pipefail
cd "$(dirname "$0")/.."
IPHONE=${1:?iphone simulator udid}
IPAD=${2:?ipad simulator udid}
BUNDLE=com.angilyu.littleexplorer
OUT=docs/screenshots
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT
mkdir -p "$OUT"

xcodebuild -project LittleExplorer.xcodeproj -scheme LittleExplorer -destination "id=$IPHONE" \
  -derivedDataPath build/dd -quiet build
APP=build/dd/Build/Products/Debug-iphonesimulator/LittleExplorer.app

launch() { local udid=$1; shift; xcrun simctl terminate "$udid" "$BUNDLE" 2>/dev/null || true; sleep 1; xcrun simctl launch "$udid" "$BUNDLE" "$@" >/dev/null; }
snap() {
  local udid=$1 name=$2 width=$3
  for _ in 1 2 3 4; do
    rm -f "$TMP/s.png"
    xcrun simctl io "$udid" screenshot "$TMP/s.png" >/dev/null 2>&1 || true
    [ -s "$TMP/s.png" ] && break
    sleep 1
  done
  sips -s format jpeg -s formatOptions 80 --resampleWidth "$width" "$TMP/s.png" --out "$OUT/$name.jpg" >/dev/null
}

for pair in "$IPHONE iphone 640" "$IPAD ipad 1000"; do
  read -r udid name width <<<"$pair"
  xcrun simctl boot "$udid" 2>/dev/null || true
  xcrun simctl install "$udid" "$APP"
  launch "$udid" -demoBook paris-food -demoPage 4; sleep 5; snap "$udid" "$name-yummy-paris" "$width"
  launch "$udid" -demoBook paris-fun -demoPage 5 -demoVoice listening; sleep 5; snap "$udid" "$name-fun-art-voice" "$width"
  launch "$udid" -demoBook paris-fun -demoPage 2 -demoRead; sleep 3.6; snap "$udid" "$name-read-aloud" "$width"
  launch "$udid"; sleep 4; snap "$udid" "$name-shelf" "$width"
done
echo "Screenshots written to $OUT"
