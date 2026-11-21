#!/usr/bin/env bash
# Run OpenJarvis with API keys loaded from ./.env and config from ./jarvis.toml
#   ./jarvis.sh ask "hello"      ./jarvis.sh chat      ./jarvis.sh serve
set -euo pipefail
cd "$(dirname "$0")"
if [ ! -f .env ]; then
  echo "No .env found — run: cp env.example .env  and add your key." >&2
  exit 1
fi
export OPENJARVIS_CONFIG="${OPENJARVIS_CONFIG:-$PWD/jarvis.toml}"

# Android/Termux (or anywhere without uv): use the plain venv the
# installer created, so uv never tries to replace Termux's prebuilt packages.
if [ -n "${TERMUX_VERSION:-}" ] || ! command -v uv >/dev/null 2>&1; then
  set -a; . ./.env; set +a
  exec ./.venv/bin/jarvis "$@"
fi
exec uv run --env-file .env jarvis "$@"
