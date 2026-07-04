# Production ops setup — footpipe-XL

**Audience:** the person running the pipeline in the office (not the cloud build agent).  
**Goal:** scan batches → searchable archive in Paperless, hands-off.

For how the system was built and accepted, see [`operator-guide.md`](operator-guide.md).  
For architecture detail, see [`design.md`](design.md) — you do **not** need that file for day-to-day ops.

---

## What you are running

```text
Scanner  →  object store (landing/)  →  worker poller
  →  OCR  →  split  →  LLM enrich  →  Paperless archive
```

| Service | Role | Default port (Compose) |
|---------|------|------------------------|
| **worker** | Polls `landing/`, runs pipeline jobs | — |
| **api** | Health + batch status + retry | `8080` |
| **postgres** | Pipeline state (batches, jobs, confidence) | internal |
| **redis** | Job queue | internal |
| **minio** (or S3/R2) | PDF originals + OCR artifacts | `9000` (API), `9001` (console) |
| **paperless** | Search UI + long-term archive | `8000` |

---

## 1. Host and stack

### Minimum host

- Small VPS or home NAS with **Docker + Compose**
- ~4 GB RAM recommended (Paperless is the heaviest service)
- Disk: plan for scanned PDFs + Paperless media (grows with volume)

### First-time setup

```bash
git clone <your-remote-url> footpipe-XL
cd footpipe-XL
cp .env.example .env
# Edit .env — see section 2
make up
```

`make up` builds images, starts all services, and waits until health checks pass.

Verify:

```bash
curl -s http://localhost:8080/health | jq .
make test    # unit tests, fake providers, no stack required beyond build
make smoke   # end-to-end golden fixtures (fakes)
```

With live Azure + OpenAI keys in `.env`:

```bash
LIVE=1 make smoke
```

---

## 2. Environment (`.env`)

Copy from `.env.example`. **Never commit `.env`.**

### Production providers (recommended)

```bash
OCR_PROVIDER=azure
AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT=https://<resource>.cognitiveservices.azure.com/
AZURE_DOCUMENT_INTELLIGENCE_KEY=<key>

LLM_PROVIDER=openai
OPENAI_API_KEY=sk-...
OPENAI_MODEL=gpt-4o-mini
```

### Object store

**Dev / single host:** keep Compose MinIO defaults.

**Production:** point at real S3 or Cloudflare R2:

```bash
OBJECT_STORE_ENDPOINT=https://<account>.r2.cloudflarestorage.com
OBJECT_STORE_BUCKET=footpipe
OBJECT_STORE_ACCESS_KEY=...
OBJECT_STORE_SECRET_KEY=...
OBJECT_STORE_REGION=auto
```

### Guardrails (cost + abuse protection)

```bash
MAX_PAGES_PER_BATCH=200
MAX_PAGES_PER_DAY=500
SPLIT_MIN_CONFIDENCE=0.6
```

Batches over these limits are rejected at ingest. Tune for your scan volume.

### Optional webhook

The **poller is primary**. If your scanner can POST when a batch lands:

```bash
LANDING_HOOK_SECRET=<long-random-string>
```

Then `POST /hooks/landing` with header `X-Landing-Secret: <secret>` (see [`api-contract.md`](api-contract.md)).

---

## 3. Scanner → landing zone

The worker watches the object store prefix **`landing/`** every ~2 seconds. When it sees `.../original.pdf`, it enqueues ingest.

### Required layout

```text
landing/{YYYY}/{MM}/{DD}/{batch_id}/original.pdf
```

Examples:

```text
landing/2026/07/04/morning-mail/original.pdf
landing/2026/07/04/scan-job-42/original.pdf
```

- **`batch_id`**: any unique folder name (date, job name, UUID).
- **`original.pdf`**: one multi-page PDF per batch scan job.
- Optional: `manifest.json` in the same folder (not required for v1).

### How to get scans into `landing/`

Pick one path that matches your scanner:

| Method | Summary |
|--------|---------|
| **Scanner vendor cloud → S3/R2** | Configure the scanner or its app to upload PDFs into `landing/.../original.pdf` on your bucket. |
| **NAS watch folder → `rclone sync`** | Scanner saves to a local folder; cron/`systemd` syncs to `s3:footpipe/landing/...`. |
| **SFTP → sync** | Same as above with `lftp`/`rsync` over SFTP to a bucket mount. |
| **MinIO on same host** | Upload via MinIO console (`:9001`) or `mc cp` into bucket `footpipe/landing/...`. |

**Rule:** one PDF per batch folder. Do not upload loose pages without a folder.

### Manual test (no scanner)

```bash
# With MinIO console or mc:
mc cp my-scan.pdf local/footpipe/landing/2026/07/04/test-batch/original.pdf
```

Watch worker logs: `make logs` (or `docker compose logs -f worker`).

---

## 4. Separator sheets (strongly recommended)

The splitter groups pages into logical documents using:

1. **Blank pages** — feed a blank sheet between documents.
2. **Barcode / text separators** — the pipeline recognizes any of:
   - `@@SEP@@`
   - `*** SEPARATOR ***`
   - `BARCODE:SEP`
3. **Text continuity** — consecutive pages with continuing text stay together unless a separator breaks them.

**Recommendation:** use **blank separator sheets** or a **barcode sheet** printed once and reused between stacks. This is more reliable than hoping the LLM/OCR infers document boundaries from content alone.

Without separators, the pipeline **prefers under-splitting** (one long document) over chopping a real document in half.

---

## 5. Categories

Edit `config/categories.yaml` before go-live:

```yaml
categories:
  - invoice
  - contract
  - bank
  - tax
  - correspondence
  - check
  - other
```

The LLM must pick exactly one category per document. Unknown values fall back to `other`.

Restart the stack after changes: `make down && make up`.

---

## 6. Paperless access

- UI: **http://&lt;host&gt;:8000**
- Default Compose admin: `admin` / `admin` — **change this before exposing the host to your network.**
- Search and day-to-day document lookup happen in Paperless.
- Pipeline API (`:8080`) is for **status and retry**, not search.

Restrict Paperless to trusted operators (VPN, firewall, or reverse proxy with auth). v1 has no SSO.

---

## 7. Day-to-day operations

### Start / stop

```bash
make up      # start stack
make down    # stop and remove volumes (destructive — see backups)
make logs    # tail all service logs
make ps      # service status
```

### Check health

```bash
curl -s http://localhost:8080/health
```

All checks should be `"ok"`. If `paperless` fails, wait for its startup period (~2 min on first boot).

### Find a batch after scanning

1. Note the landing folder / `batch_id` you used when uploading.
2. Query the API (replace UUID after first batch appears in logs or Paperless metadata):

```bash
curl -s http://localhost:8080/batches/<batch-uuid> | jq .
```

Response includes `status`, `documents`, `jobs`, and errors.

### Retry a failed batch

```bash
curl -s -X POST http://localhost:8080/batches/<batch-uuid>/retry \
  -H 'Content-Type: application/json' \
  -d '{"force": false}'
```

- `force: false` — retry dead/failed jobs only; **does not** re-run paid OCR if artifacts exist.
- `force: true` — re-OCR the batch (uses Azure credits again).

### `needs_review` documents

Low split or enrich confidence sets `needs_review=true` on a document. v1 still archives to Paperless. There is **no review UI** yet — use Paperless tags/titles and plan manual spot-checks.

---

## 8. Backup and restore

Back up **all three** together; restoring only one leaves broken references.

| Component | Compose volume | Contents |
|-----------|----------------|----------|
| Postgres | `postgres-data` | Batches, jobs, document metadata, confidence |
| Object store | `minio-data` | Original PDFs, OCR JSON, page images |
| Paperless | `paperless-data`, `paperless-media` | Archive index + stored files |

### Example backup (host with Compose)

```bash
# List volume names on your host (prefix depends on project folder name)
docker volume ls | grep -E 'postgres-data|minio-data|paperless'

# Stop writers for a consistent snapshot (brief downtime)
docker compose stop worker api

BACKUP_DIR=./backups/$(date +%F)
mkdir -p "$BACKUP_DIR"

# Replace VOLUME_* with names from `docker volume ls`
docker run --rm -v VOLUME_postgres-data:/data:ro -v "$BACKUP_DIR":/backup \
  alpine tar czf /backup/postgres.tgz -C /data .

docker run --rm -v VOLUME_minio-data:/data:ro -v "$BACKUP_DIR":/backup \
  alpine tar czf /backup/minio.tgz -C /data .

docker run --rm \
  -v VOLUME_paperless-data:/pdata:ro \
  -v VOLUME_paperless-media:/pmedia:ro \
  -v "$BACKUP_DIR":/backup \
  alpine sh -c 'tar czf /backup/paperless-data.tgz -C /pdata . && tar czf /backup/paperless-media.tgz -C /pmedia .'

docker compose start worker api
```

On a default clone named `footpipe-XL`, volumes are often `footpipe-xl_postgres-data`, etc. Run `docker volume ls` to confirm.

**If using external S3/R2:** enable bucket versioning or lifecycle replication; still back up Postgres and Paperless volumes.

### Restore drill

Test restores on a non-production host quarterly. Document your exact volume names and paths.

---

## 9. Security checklist

- [ ] Change Paperless admin password
- [ ] Do not expose `:8080` or `:9001` to the public internet without a firewall
- [ ] Store API keys in a secret manager or host env, not in git
- [ ] Rotate Azure/OpenAI keys if ever leaked
- [ ] Keep `MAX_PAGES_PER_*` guardrails enabled

---

## 10. Troubleshooting

| Symptom | What to check |
|---------|----------------|
| Scan uploaded but nothing happens | Worker logs; path must end with `/original.pdf` under `landing/`; `make logs` |
| Batch `skipped_duplicate` | Same PDF checksum already ingested; change file or metadata |
| Batch `failed` / jobs `dead` | `GET /batches/{id}` → `jobs[].last_error`; then `POST .../retry` |
| `failed_partial` | Some documents archived, some not; retry without force |
| High Azure/OpenAI bill | Confirm guardrails; check for retry loops; set `OCR_PROVIDER=fake` only on dev |
| Paperless empty but batch `completed` | Paperless health; token; `paperless` check in `/health` |
| Wrong document boundaries | Add blank or barcode separator sheets; avoid over-stuffing one batch |

### Useful log commands

```bash
docker compose logs -f worker --tail=200
docker compose logs -f api --tail=100
docker compose logs -f paperless --tail=100
```

---

## 11. Go-live checklist

- [ ] `make up` — all services healthy
- [ ] `make test` green
- [ ] `LIVE=1 make smoke` green (once, with real keys)
- [ ] `.env` has production providers and object store
- [ ] Scanner or sync job lands PDFs at `landing/.../original.pdf`
- [ ] Separator sheets in use
- [ ] `config/categories.yaml` reviewed
- [ ] Paperless password changed; access restricted
- [ ] Backup job scheduled
- [ ] Operator knows `GET /batches/{id}` and retry endpoint

---

## Quick reference

| Task | Command / URL |
|------|----------------|
| Start stack | `make up` |
| Health | `curl localhost:8080/health` |
| Search documents | Paperless `http://localhost:8000` |
| Batch status | `GET /batches/{uuid}` |
| Retry | `POST /batches/{uuid}/retry` |
| Live smoke test | `LIVE=1 make smoke` |
| Categories | `config/categories.yaml` |
| Landing path | `landing/{date}/{batch_id}/original.pdf` |
