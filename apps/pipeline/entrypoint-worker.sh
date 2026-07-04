#!/usr/bin/env bash
set -euo pipefail

echo "[worker] starting job processor + landing poller"
exec python -m pipeline.worker
