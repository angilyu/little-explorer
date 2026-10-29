#!/usr/bin/env bash
# Kept for old habits; releases now go through scripts/release.sh, which builds the beta and App
# Store apps from one pushed commit with the same version and build number:
#   scripts/testflight.sh              ->  scripts/release.sh beta   (Little Explorer (beta) -> TestFlight)
#   scripts/testflight.sh --app-store  ->  scripts/release.sh prod   (Little Explorer -> App Store Connect)
set -euo pipefail
case "${1:-}" in
  "") mode=beta ;;
  --app-store) mode=prod; shift ;;
  *) mode=beta ;;
esac
echo "scripts/testflight.sh is now scripts/release.sh $mode" >&2
exec "$(dirname "$0")/release.sh" "$mode" "$@"
