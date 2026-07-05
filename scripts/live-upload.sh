#!/usr/bin/env bash
# Upload one PDF bundle and wait for live (azure + openai) processing.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

PDF="${1:?Usage: ./scripts/live-upload.sh /path/to/bundle.pdf [batch-name]}"
BATCH_NAME="${2:-}"
DOCKER="${DOCKER:-sudo docker}"

if [[ ! -f "$PDF" ]]; then
  echo "error: file not found: $PDF" >&2
  exit 1
fi

"$ROOT/scripts/sync-env.sh"
if ! "$ROOT/scripts/sync-env.sh" --check-live; then
  exit 1
fi

echo "== restarting api/worker with live providers =="
$DOCKER compose up -d --force-recreate api worker
$DOCKER compose up -d --wait --wait-timeout 120 api worker

if [[ -n "$BATCH_NAME" ]]; then
  UPLOAD=$(curl -sf -X POST http://localhost:8080/upload \
    -F "file=@${PDF}" \
    -F "batch_id=${BATCH_NAME}")
else
  UPLOAD=$(curl -sf -X POST http://localhost:8080/upload -F "file=@${PDF}")
fi

LANDING=$(echo "$UPLOAD" | rg -o 'landing/[^<]+' | head -1 || true)
if [[ -z "$LANDING" ]]; then
  echo "upload failed:" >&2
  echo "$UPLOAD" >&2
  exit 1
fi

echo "uploaded → $LANDING"
echo "waiting for worker (live OCR + LLM can take several minutes on large PDFs)..."

BATCH_ID=""
for _ in $(seq 1 120); do
  ROW=$($DOCKER compose exec -T postgres psql -U pipeline -d pipeline -t -A -F'|' -c \
    "SELECT b.id, b.status, b.page_count, COUNT(d.id)::int
     FROM batch b LEFT JOIN document d ON d.batch_id=b.id
     WHERE b.source_uri LIKE '%${LANDING%/original.pdf}%'
     GROUP BY b.id ORDER BY b.created_at DESC LIMIT 1;" 2>/dev/null | tr -d ' \n')
  if [[ -n "$ROW" ]]; then
    BATCH_ID=$(echo "$ROW" | cut -d'|' -f1)
    STATUS=$(echo "$ROW" | cut -d'|' -f2)
    DOCS=$(echo "$ROW" | cut -d'|' -f4)
    echo "  status=$STATUS documents=$DOCS"
    if [[ "$STATUS" == "completed" || "$STATUS" == "failed" || "$STATUS" == "failed_partial" ]]; then
      break
    fi
  fi
  sleep 5
done

if [[ -z "$BATCH_ID" ]]; then
  echo "error: batch not found — check: make logs DOCKER=\"$DOCKER\"" >&2
  exit 1
fi

echo ""
echo "== batch result =="
echo "  id:     $BATCH_ID"
echo "  api:    http://localhost:8080/batches/$BATCH_ID"
echo "  paperless: http://localhost:8000"
echo ""
curl -sf "http://localhost:8080/batches/$BATCH_ID" | python3 -c "
import json, sys
b = json.load(sys.stdin)
print(f\"status: {b['status']}  pages: {b['page_count']}  docs: {len(b['documents'])}\")
if b.get('error'):
    print('error:', b['error'])
for i, d in enumerate(b['documents'], 1):
    rev = ' [needs review]' if d.get('needs_review') else ''
    print(f\"  {i}. {d.get('title') or 'untitled'} — {d.get('category')} — conf {d.get('enrich_confidence', 0):.2f}{rev}\")
"
