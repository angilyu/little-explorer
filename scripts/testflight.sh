#!/usr/bin/env bash
# Build Little Explorer and upload it to TestFlight: `scripts/testflight.sh`.
# Devices with TestFlight (and its Automatic Updates on) get each new build without a cable.
#
# Needs an App Store Connect API key (Users and Access -> Integrations -> Team Keys, role Admin):
#   ASC_KEY_ID, ASC_ISSUER_ID, and the key file at ASC_KEY_PATH
#   (default ~/.appstoreconnect/private_keys/AuthKey_<ASC_KEY_ID>.p8).
# They can live in ~/.config/little-explorer/asc.env. The build number is the current date and time.
set -euo pipefail
cd "$(dirname "$0")/.."

asc_env="$HOME/.config/little-explorer/asc.env"
[[ -f "$asc_env" ]] && { set -a; source "$asc_env"; set +a; }
: "${ASC_KEY_ID:?set ASC_KEY_ID}" "${ASC_ISSUER_ID:?set ASC_ISSUER_ID}"
ASC_KEY_PATH="${ASC_KEY_PATH:-$HOME/.appstoreconnect/private_keys/AuthKey_${ASC_KEY_ID}.p8}"
[[ -f "$ASC_KEY_PATH" ]] || { echo "no API key file at $ASC_KEY_PATH" >&2; exit 1; }
TEAM="${DEVELOPMENT_TEAM:-NMJBY8WL8T}"
grep -qE '^PIP_ACCESS_KEY *= *[^ ]' Config/Local.xcconfig 2>/dev/null \
  || echo "warning: Config/Local.xcconfig has no PIP_ACCESS_KEY; Milo won't be able to connect" >&2

build="$(date +%Y%m%d.%H%M)"
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
auth=(-allowProvisioningUpdates -authenticationKeyPath "$ASC_KEY_PATH"
      -authenticationKeyID "$ASC_KEY_ID" -authenticationKeyIssuerID "$ASC_ISSUER_ID")

echo "Archiving build $build..."
xcodebuild -project LittleExplorer.xcodeproj -scheme LittleExplorer -configuration Release \
  -destination 'generic/platform=iOS' -archivePath "$work/LittleExplorer.xcarchive" \
  -derivedDataPath "$work/dd" DEVELOPMENT_TEAM="$TEAM" CURRENT_PROJECT_VERSION="$build" \
  "${auth[@]}" -quiet archive

cat > "$work/export.plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>method</key><string>app-store-connect</string>
  <key>destination</key><string>upload</string>
  <key>teamID</key><string>$TEAM</string>
  <key>signingStyle</key><string>automatic</string>
  <key>uploadSymbols</key><true/>
  <key>testFlightInternalTestingOnly</key><true/>
</dict></plist>
EOF

echo "Uploading to App Store Connect..."
xcodebuild -exportArchive -archivePath "$work/LittleExplorer.xcarchive" \
  -exportOptionsPlist "$work/export.plist" -exportPath "$work/export" "${auth[@]}"
echo "Uploaded build $build. It shows up in TestFlight after Apple finishes processing (usually 5-15 minutes)."
