#!/usr/bin/env bash
# Run OpenJarvis with API keys loaded from ./.env
#   ./jarvis.sh ask "hello"      ./jarvis.sh chat      ./jarvis.sh doctor
set -euo pipefail
cd "$(dirname "$0")"
if [ ! -f .env ]; then
  echo "No .env found — run: cp env.example .env  and add your key." >&2
  exit 1
fi
export OPENJARVIS_CONFIG="${OPENJARVIS_CONFIG:-$PWD/jarvis.toml}"
exec uv run --env-file .env jarvis "$@"
