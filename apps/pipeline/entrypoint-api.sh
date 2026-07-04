#!/usr/bin/env bash
set -euo pipefail

echo "[api] waiting for database + applying migrations..."
for i in $(seq 1 60); do
  if alembic upgrade head; then
    echo "[api] migrations applied"
    break
  fi
  echo "[api] db not ready ($i/60), retrying..."
  sleep 2
done

echo "[api] starting uvicorn on ${API_HOST:-0.0.0.0}:${API_PORT:-8080}"
exec uvicorn pipeline.api:app --host "${API_HOST:-0.0.0.0}" --port "${API_PORT:-8080}"
