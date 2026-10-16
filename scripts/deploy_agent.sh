#!/usr/bin/env bash
# Deploy Pip to LiveKit Cloud: `scripts/deploy_agent.sh` (first run creates the agent, later runs update it).
# Reads LIVEKIT_*, OPENAI_API_KEY and PIP_* from the env file the server uses
# ($LITTLE_EXPLORER_ENV, else server/.env, else ~/.config/little-explorer/.env). Needs the `lk` CLI.
set -euo pipefail
cd "$(dirname "$0")/../server"

env_file="${LITTLE_EXPLORER_ENV:-}"
[[ -z "$env_file" && -f .env ]] && env_file=.env
[[ -z "$env_file" ]] && env_file="$HOME/.config/little-explorer/.env"
[[ -f "$env_file" ]] || { echo "no env file at $env_file" >&2; exit 1; }
set -a; source "$env_file"; set +a
: "${LIVEKIT_URL:?}" "${LIVEKIT_API_KEY:?}" "${LIVEKIT_API_SECRET:?}" "${OPENAI_API_KEY:?}"
[[ -n "${PIP_CONTENT_URL:-}" ]] || echo "warning: PIP_CONTENT_URL is not set; Pip will use the books baked into the image" >&2

# Bake the current books into the image as a fallback (server/content is gitignored).
rm -rf content && mkdir -p content && cp -R ../LittleExplorer/Resources/Books content/Books

secrets="$(mktemp)"
trap 'rm -f "$secrets"; rm -rf content' EXIT
chmod 600 "$secrets"
{
  echo "OPENAI_API_KEY=$OPENAI_API_KEY"
  echo "PIP_MODE=${PIP_MODE:-realtime}"
  echo "PIP_REALTIME_MODEL=${PIP_REALTIME_MODEL:-gpt-realtime}"
  echo "PIP_SPEECH=${PIP_SPEECH:-openai}"
  [[ -n "${PIP_CONTENT_URL:-}" ]] && echo "PIP_CONTENT_URL=$PIP_CONTENT_URL"
} > "$secrets"

if [[ -f livekit.toml ]]; then
  lk agent deploy --secrets-file "$secrets" -y .
else
  lk agent create --secrets-file "$secrets" -y .
fi
lk agent status .
