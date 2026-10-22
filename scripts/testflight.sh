#!/usr/bin/env bash
# Build Little Explorer and upload it to TestFlight: `scripts/testflight.sh [--app-store]`.
# Devices with TestFlight (and its Automatic Updates on) get each new build without a cable.
# By default the build is for internal testers only and can't be submitted for App Review;
# `--app-store` uploads a build that can also be attached to an App Store version.
#
# Needs an App Store Connect API key (Users and Access -> Integrations -> Team Keys, role Admin):
#   ASC_KEY_ID, ASC_ISSUER_ID, and the key file at ASC_KEY_PATH
#   (default ~/.appstoreconnect/private_keys/AuthKey_<ASC_KEY_ID>.p8).
# They can live in ~/.config/little-explorer/asc.env. The build number is the current date and time.
set -euo pipefail
cd "$(dirname "$0")/.."

internal_only=true
case "${1:-}" in
  "") ;;
  --app-store) internal_only=false ;;
  *) echo "usage: $0 [--app-store]" >&2; exit 2 ;;
esac

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

# Archive unsigned; the export signs it for App Store distribution, which (unlike a development
# profile) doesn't need any registered devices.
echo "Archiving build $build..."
xcodebuild -project LittleExplorer.xcodeproj -scheme LittleExplorer -configuration Release \
  -destination 'generic/platform=iOS' -archivePath "$work/LittleExplorer.xcarchive" \
  -derivedDataPath "$work/dd" DEVELOPMENT_TEAM="$TEAM" CURRENT_PROJECT_VERSION="$build" \
  CODE_SIGNING_ALLOWED=NO -quiet archive

cat > "$work/export.plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>method</key><string>app-store-connect</string>
  <key>destination</key><string>upload</string>
  <key>teamID</key><string>$TEAM</string>
  <key>signingStyle</key><string>automatic</string>
  <key>uploadSymbols</key><true/>
  <key>testFlightInternalTestingOnly</key><$internal_only/>
</dict></plist>
EOF

echo "Uploading to App Store Connect..."
xcodebuild -exportArchive -archivePath "$work/LittleExplorer.xcarchive" \
  -exportOptionsPlist "$work/export.plist" -exportPath "$work/export" "${auth[@]}" 2>&1 | tee "$work/export.log" \
  | grep -E "Upload succeeded|EXPORT|error"
logs="$(sed -nE 's/.*Created bundle at path "([^"]+)".*/\1/p' "$work/export.log" | head -1)"
delivery="$(grep -rhoE 'Delivery UUID: [0-9a-f-]+' "$logs" 2>/dev/null | head -1 | awk '{print $3}')"
[[ -n "$delivery" ]] || { echo "Uploaded build $build, but couldn't find its delivery id to follow processing." >&2; exit 0; }

# Apple checks the build after the upload; a rejection only shows up here (or in an email).
echo "Uploaded build $build (delivery $delivery). Waiting for Apple to process it..."
for _ in $(seq 1 60); do
  status="$(xcrun altool --build-status --delivery-id "$delivery" --api-key "$ASC_KEY_ID" \
    --api-issuer "$ASC_ISSUER_ID" --p8-file-path "$ASC_KEY_PATH" 2>&1)"
  if grep -q "BUILD-STATUS: FAILED" <<<"$status"; then echo "$status" >&2; exit 1; fi
  if grep -qE "BUILD-STATUS: (COMPLETE|VALID|SUCCESS)" <<<"$status"; then
    echo "Build $build is ready in TestFlight."; exit 0
  fi
  sleep 30
done
echo "Still processing after 30 minutes; check App Store Connect." >&2
