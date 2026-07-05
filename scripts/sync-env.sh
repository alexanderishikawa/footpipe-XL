#!/usr/bin/env bash
# Merge Cursor Cloud Secrets (process env) and optional .env.local into .env.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
exec python3 "$ROOT/scripts/sync_env.py" "$@"
