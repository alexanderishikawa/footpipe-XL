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

### Paperless metadata sync (v1.1)

After each document is archived, the worker pushes enrichment (tags, document type, correspondent, content date) to Paperless. Tune confidence gates in `.env`:

```bash
# Min confidence to sync content date → Paperless "Content Date" custom field
METADATA_DATE_MIN_CONF=0.7

# Min confidence to sync originator → Paperless correspondent
METADATA_ORIGINATOR_MIN_CONF=0.6

# Auto-create document types from config/categories.yaml on first sync (default: true)
PAPERLESS_BOOTSTRAP_TYPES=true

# Name of the date custom field created in Paperless (default: Content Date)
PAPERLESS_CONTENT_DATE_FIELD_NAME=Content Date
```

Restart the stack after changing these values: `make down && make up`.

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
| **Epson ES-580W (recommended)** | Scan to SMB share on footpipe host + [`landing-watch.py`](../scripts/landing-watch.py). Full steps: [`scanner-epson-es580w.md`](scanner-epson-es580w.md). |
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

## 5. Categories and Paperless document types

Edit `config/categories.yaml` before go-live. Each entry maps a pipeline **slug** (what the LLM picks) to a **Paperless document type** display name:

```yaml
categories:
  - slug: invoice
    paperless_type: Invoice
  - slug: contract
    paperless_type: Contract
  - slug: bank
    paperless_type: Bank Statement
  - slug: tax
    paperless_type: Tax Document
  - slug: correspondence
    paperless_type: Correspondence
  - slug: check
    paperless_type: Check
  - slug: other
    paperless_type: Other
```

- The LLM must pick exactly one **slug** per document. Unknown values fall back to `other`.
- With `PAPERLESS_BOOTSTRAP_TYPES=true` (default), the worker creates any missing document types in Paperless on the first metadata sync — no manual Paperless setup required.
- If you rename a `paperless_type`, restart the stack; existing Paperless types are not renamed automatically.

Restart the stack after changes: `make down && make up`.

---

## 6. Paperless access

- UI: **http://&lt;host&gt;:8000**
- Default Compose admin: `admin` / `admin` — **change this before exposing the host to your network.**
- Search and day-to-day document lookup happen in Paperless.
- Pipeline API (`:8080`) is for **status and retry**, not search.

Restrict Paperless to trusted operators (VPN, firewall, or reverse proxy with auth). v1 has no SSO.

### Paperless metadata bootstrap (v1.1)

**You do not need to pre-create tags, correspondents, or custom fields in Paperless.** The worker bootstraps metadata on the first `commit.archive` that syncs to Paperless:

| What | When | How |
|------|------|-----|
| **Document types** | First sync (if `PAPERLESS_BOOTSTRAP_TYPES=true`) | One type per `paperless_type` in `config/categories.yaml` |
| **Content Date** custom field | First sync | Date field named `PAPERLESS_CONTENT_DATE_FIELD_NAME` (default `Content Date`) |
| **Tags** | Each document | Created on demand from enrichment (category + LLM tags + entities) |
| **Correspondents** | Each document | Created on demand when originator confidence ≥ `METADATA_ORIGINATOR_MIN_CONF` |

**Verify bootstrap worked:** process a test batch, then in Paperless check **Settings → Document types** (you should see Invoice, Bank Statement, etc.) and **Settings → Custom fields** (Content Date). Open an archived document — it should show a document type, tags, and optionally correspondent + Content Date.

**One-way sync:** pipeline → Paperless only. Edits you make in the Paperless UI (tags, correspondent, type) are **not** written back to Postgres. To refresh metadata from the pipeline, re-run archive: `POST /batches/{id}/retry` with `force: false` (re-syncs without re-uploading the PDF).

**Tag convention:** enrichment tags are lowercase. People and organizations mentioned in the document appear as `entity:{slug}` tags (e.g. `entity:chase-bank`, `entity:john-dinglebarre`). The category slug is always included as a tag. Max 20 tags per document; overflow sets `needs_review`.

**Deeper design:** [`docs/plans/designs/001-rich-metadata-paperless.md`](plans/designs/001-rich-metadata-paperless.md). **Live eval PDFs:** [`docs/eval-corpus.md`](eval-corpus.md).

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

Response includes `status`, `documents`, `jobs`, and errors. Per document (v1.1): `document_date`, `originator`, `entities`, and `metadata_synced` (true when enrichment was fully pushed to Paperless).

### Retry a failed batch

```bash
curl -s -X POST http://localhost:8080/batches/<batch-uuid>/retry \
  -H 'Content-Type: application/json' \
  -d '{"force": false}'
```

- `force: false` — retry dead/failed jobs only; **does not** re-run paid OCR if artifacts exist.
- `force: true` — re-OCR the batch (uses Azure credits again).

### `needs_review` documents

`needs_review=true` flags documents that need a human spot-check. v1 still archives to Paperless. There is **no review UI** yet — filter in the API response or spot-check in Paperless.

Common reasons:

| Cause | What happened |
|-------|----------------|
| Low split confidence | Page boundaries uncertain (`SPLIT_MIN_CONFIDENCE`) |
| Low enrich confidence | LLM unsure about category/title |
| Tag overflow | More than 20 tags after normalization |
| Low date/originator confidence | Value kept in Postgres but not synced to Paperless |
| Future content date | Date rejected for Paperless Content Date field |
| Metadata sync failure | `metadata_synced=false` — partial or failed Paperless PATCH |
| LLM / OCR failure | Fallback title and `tags: ["other"]` |

When `metadata_synced=false` but `paperless_id` is set, the PDF is archived but tags/type/correspondent/date may be incomplete. Retry the batch (`force: false`) to re-run `sync_metadata` without re-uploading.

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
| `metadata_synced=false` | `GET /batches/{id}` → check `needs_review`; retry batch (`force: false`); worker logs for `sync_metadata`; confirm document types exist (bootstrap) and Paperless API token is valid |
| Tags missing in Paperless | Sync may have failed partially — retry; check `metadata_synced`; tag create races usually self-heal on retry |
| No correspondent / Content Date | Originators/dates below `METADATA_*_MIN_CONF` are skipped by design; lower thresholds in `.env` if too strict |
| Document type missing | Set `PAPERLESS_BOOTSTRAP_TYPES=true` and restart; or create types manually matching `paperless_type` names in `categories.yaml` |

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
- [ ] `config/categories.yaml` reviewed (`slug` + `paperless_type` pairs)
- [ ] `METADATA_*_MIN_CONF` and `PAPERLESS_BOOTSTRAP_TYPES` set as intended
- [ ] Test batch shows document types + tags in Paperless (`metadata_synced=true` in API)
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
| Categories / doc types | `config/categories.yaml` |
| Metadata env vars | `METADATA_DATE_MIN_CONF`, `METADATA_ORIGINATOR_MIN_CONF`, `PAPERLESS_BOOTSTRAP_TYPES` |
| Metadata design | `docs/plans/designs/001-rich-metadata-paperless.md` |
| Live eval PDFs | `docs/eval-corpus.md` |
| Landing path | `landing/{date}/{batch_id}/original.pdf` |
