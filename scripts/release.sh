#!/usr/bin/env bash
# Release Little Explorer from one pushed commit, so a version number always means the same code:
#
#   scripts/release.sh beta    # "Little Explorer (beta)" (com.angilyu.littleexplorer.beta) -> TestFlight
#   scripts/release.sh prod    # "Little Explorer" (com.angilyu.littleexplorer) -> App Store Connect
#   scripts/release.sh both    # both, with the same build number
#   scripts/release.sh check   # only the checks below, and what would be released
#
# Options:
#   --dry-run       archive and verify both apps, but don't upload or tag (checks only warn)
#   --new-build     a new build number for this version (e.g. after Apple rejected a build)
#   --build N       use build number N
#   --allow-branch  release a pushed commit that isn't on origin/main
#
# Both apps are archived from the same checkout with the same MARKETING_VERSION, the same build
# number (CFBundleVersion, the date and time) and the commit's SHA in Info.plist (`GitCommit`), and
# the commit is tagged v<version>-<build>. It refuses to run on uncommitted changes, on a commit
# that isn't pushed (or not on main), or on a version that another commit was already released as:
# new code needs a new MARKETING_VERSION. Releasing `prod` after `beta` at the same commit reuses
# the beta's build number. A prod build is only uploaded: attach it to an App Store version and
# submit it for review in App Store Connect yourself.
#
# Uploading needs an App Store Connect API key (Users and Access -> Integrations -> Team Keys,
# role Admin): ASC_KEY_ID, ASC_ISSUER_ID and the key file at ASC_KEY_PATH (default
# ~/.appstoreconnect/private_keys/AuthKey_<ASC_KEY_ID>.p8), e.g. in ~/.config/little-explorer/asc.env.
# See "Releasing the app" in docs/operations.md.
set -euo pipefail
cd "$(dirname "$0")/.."

usage() { echo "usage: $0 beta|prod|both|check [--dry-run] [--new-build] [--build N] [--allow-branch]" >&2; exit 2; }
mode="${1:-}"
case "$mode" in beta|prod|both|check) shift ;; *) usage ;; esac
dry_run=false new_build=false allow_branch=false build=""
while (($#)); do
  case "$1" in
    --dry-run) dry_run=true ;;
    --new-build) new_build=true ;;
    --allow-branch) allow_branch=true ;;
    --build) shift; build="${1:-}"; [[ "$build" =~ ^[0-9]+(\.[0-9]+)*$ ]] || usage ;;
    *) usage ;;
  esac
  shift
done

REMOTE="${RELEASE_REMOTE:-origin}"
MAIN="${RELEASE_BRANCH:-main}"
PROJECT=LittleExplorer.xcodeproj
problems=0
refuse() {
  if $dry_run; then echo "warning: $*" >&2; problems=$((problems + 1)); else echo "refusing: $*" >&2; exit 1; fi
}

# --- the checks --------------------------------------------------------------------------------

git fetch --quiet --tags --force "$REMOTE" || refuse "couldn't fetch from $REMOTE"
[[ -z "$(git status --porcelain)" ]] || refuse "the working tree has uncommitted or untracked changes"
head="$(git rev-parse HEAD)"
if [[ -z "$(git branch -r --contains "$head" 2>/dev/null)" ]]; then
  refuse "commit ${head:0:7} isn't pushed to $REMOTE"
elif ! git merge-base --is-ancestor "$head" "$REMOTE/$MAIN" 2>/dev/null; then
  if $allow_branch || [[ "$mode" == check ]]; then
    echo "warning: commit ${head:0:7} isn't on $REMOTE/$MAIN" >&2
  else
    refuse "commit ${head:0:7} isn't on $REMOTE/$MAIN (merge it first, or pass --allow-branch)"
  fi
fi

versions="$(sed -nE 's/.*MARKETING_VERSION = ([^;]+);.*/\1/p' "$PROJECT/project.pbxproj" | sort -u)"
[[ -n "$versions" && "$(wc -l <<<"$versions")" -eq 1 ]] \
  || { echo "refusing: the project's MARKETING_VERSIONs differ: $(tr "\n" " " <<<"$versions")" >&2; exit 1; }
version="$versions"

# One version, one commit: every v<version> tag must be this commit.
released=""
for tag in $(git tag -l "v$version" "v$version-*"); do
  commit="$(git rev-list -n 1 "$tag")"
  if [[ "$commit" != "$head" ]]; then
    refuse "$version was already released from ${commit:0:7} ($tag); this is different code, so bump MARKETING_VERSION"
  else
    released="$tag"
  fi
done
if [[ -z "$build" ]]; then
  last="$(git tag -l "v$version-*" --points-at "$head" | sed "s/^v$version-//" | sort -t. -k1,1n -k2,2n | tail -1)"
  if [[ -n "$last" ]] && ! $new_build; then build="$last"; else build="$(date +%Y%m%d.%H%M)"; fi
fi
tag="v$version-$build"

echo "Little Explorer $version ($build) from ${head:0:7}, tag $tag${released:+ (released before as $released)}"
if [[ "$mode" == check ]]; then
  ((problems == 0)) && echo "OK to release."
  exit 0
fi

case "$mode" in
  beta) apps=(Beta) ;;
  prod) apps=(Release) ;;
  both) apps=(Beta Release) ;;
esac
if $dry_run; then apps=(Beta Release); fi

if ! $dry_run; then
  asc_env="$HOME/.config/little-explorer/asc.env"
  # shellcheck disable=SC1090
  [[ -f "$asc_env" ]] && { set -a; source "$asc_env"; set +a; }
  : "${ASC_KEY_ID:?set ASC_KEY_ID}" "${ASC_ISSUER_ID:?set ASC_ISSUER_ID}"
  ASC_KEY_PATH="${ASC_KEY_PATH:-$HOME/.appstoreconnect/private_keys/AuthKey_${ASC_KEY_ID}.p8}"
  [[ -f "$ASC_KEY_PATH" ]] || { echo "no API key file at $ASC_KEY_PATH" >&2; exit 1; }
fi
TEAM="${DEVELOPMENT_TEAM:-NMJBY8WL8T}"
grep -qE '^PIP_ACCESS_KEY *= *[^ ]' Config/Local.xcconfig 2>/dev/null \
  || echo "warning: Config/Local.xcconfig has no PIP_ACCESS_KEY; Milo won't be able to connect" >&2

work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT

bundle_id() { [[ "$1" == Beta ]] && echo com.angilyu.littleexplorer.beta || echo com.angilyu.littleexplorer; }
label() { [[ "$1" == Beta ]] && echo "beta" || echo "prod"; }

# --- archive both from this checkout, then check they say the same thing -------------------------

for config in "${apps[@]}"; do
  echo "Archiving $(label "$config") $version ($build)..."
  # Unsigned; the export signs it for App Store distribution, which needs no registered devices.
  xcodebuild -project "$PROJECT" -scheme LittleExplorer -configuration "$config" \
    -destination 'generic/platform=iOS' -archivePath "$work/$config.xcarchive" \
    -derivedDataPath "$work/dd" DEVELOPMENT_TEAM="$TEAM" CURRENT_PROJECT_VERSION="$build" \
    GIT_COMMIT="$head" CODE_SIGNING_ALLOWED=NO -quiet archive
  plist="$work/$config.xcarchive/Products/Applications/LittleExplorer.app/Info.plist"
  read_key() { /usr/libexec/PlistBuddy -c "Print :$1" "$plist"; }
  for check in "CFBundleShortVersionString=$version" "CFBundleVersion=$build" "GitCommit=$head" \
               "CFBundleIdentifier=$(bundle_id "$config")"; do
    got="$(read_key "${check%%=*}")"
    [[ "$got" == "${check#*=}" ]] || { echo "$config archive: ${check%%=*} is '$got', not '${check#*=}'" >&2; exit 1; }
  done
  echo "  $(read_key CFBundleIdentifier) \"$(read_key CFBundleDisplayName)\" $version ($build) $(read_key GitCommit | cut -c1-7)"
done

if $dry_run; then
  echo "Dry run: archived and checked $(printf '%s ' "${apps[@]}")but uploaded and tagged nothing."
  ((problems == 0)) || echo "$problems check(s) would have refused a real release." >&2
  exit 0
fi

# --- upload, tag, and follow Apple's processing ------------------------------------------------

auth=(-allowProvisioningUpdates -authenticationKeyPath "$ASC_KEY_PATH"
      -authenticationKeyID "$ASC_KEY_ID" -authenticationKeyIssuerID "$ASC_ISSUER_ID")
deliveries=()
for config in "${apps[@]}"; do
  # The beta app only ever goes to TestFlight; the prod build can also be attached to an App Store
  # version (it is never submitted for review from here).
  internal_only=false
  [[ "$config" == Beta ]] && internal_only=true
  cat > "$work/$config-export.plist" <<EOF
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
  echo "Uploading $(label "$config") to App Store Connect..."
  xcodebuild -exportArchive -archivePath "$work/$config.xcarchive" \
    -exportOptionsPlist "$work/$config-export.plist" -exportPath "$work/$config-export" "${auth[@]}" 2>&1 \
    | tee "$work/$config-export.log" | grep -E "Upload succeeded|EXPORT|error" || true
  grep -q "Upload succeeded" "$work/$config-export.log" \
    || { echo "The $(label "$config") upload failed; see the errors above." >&2; exit 1; }

  if [[ -z "$(git tag -l "$tag")" ]]; then
    git tag -a "$tag" -m "Little Explorer $version ($build)" "$head"
    git push --quiet "$REMOTE" "refs/tags/$tag"
    echo "Tagged ${head:0:7} as $tag."
  fi

  logs="$(sed -nE 's/.*Created bundle at path "([^"]+)".*/\1/p' "$work/$config-export.log" | head -1)"
  delivery="$(grep -rhoE 'Delivery UUID: [0-9a-f-]+' "$logs" 2>/dev/null | head -1 | awk '{print $3}')"
  if [[ -n "$delivery" ]]; then deliveries+=("$config=$delivery")
  else echo "Uploaded $(label "$config") $build, but couldn't find its delivery id to follow processing." >&2; fi
done

# Apple checks each build after the upload; a rejection only shows up here (or in an email).
for entry in ${deliveries[@]+"${deliveries[@]}"}; do
  config="${entry%%=*}" delivery="${entry#*=}"
  echo "Waiting for Apple to process $(label "$config") $build (delivery $delivery)..."
  ready=false
  for _ in $(seq 1 60); do
    status="$(xcrun altool --build-status --delivery-id "$delivery" --api-key "$ASC_KEY_ID" \
      --api-issuer "$ASC_ISSUER_ID" --p8-file-path "$ASC_KEY_PATH" 2>&1)"
    if grep -q "BUILD-STATUS: FAILED" <<<"$status"; then echo "$status" >&2; exit 1; fi
    if grep -qE "BUILD-STATUS: (COMPLETE|VALID|SUCCESS)" <<<"$status"; then ready=true; break; fi
    sleep 30
  done
  if $ready; then
    if [[ "$config" == Beta ]]; then echo "Beta $version ($build) is ready in TestFlight."
    else echo "$version ($build) is ready in App Store Connect: attach it to the $version App Store version and submit it for review."; fi
  else
    echo "$(label "$config") is still processing after 30 minutes; check App Store Connect." >&2
  fi
done
