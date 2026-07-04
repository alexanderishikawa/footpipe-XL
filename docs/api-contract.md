# Document Pipeline Control API Contract

> Session: document-pipeline-20260704 | Date: 2026-07-04

**Design:** `docs/design.md`  
**Plan:** `docs/plan.md`  
**Scope:** Pipeline control and observability only; search UX is Paperless-ngx.

## Base URL

`/` (service root; bind to Docker internal network / localhost in v1)

## Authentication

- **v1:** No JWT. Service is not publicly exposed.
- Optional landing webhook requires shared secret header (see below).
- Paperless uses its own token (`PAPERLESS_TOKEN`) server-side only.

---

## Endpoints

### GET /health

- **Description:** Liveness and dependency checks
- **Auth:** Public (internal network)
- **Response 200:**
  ```json
  {
    "status": "ok",
    "checks": {
      "database": "ok",
      "redis": "ok",
      "object_store": "ok",
      "paperless": "ok"
    }
  }
  ```
- **Response 503:** One or more checks failed; `status` is `"degraded"` or `"error"`; failed checks named in `checks`
- **Notes:** `paperless` may be `"skip"` during early milestones before archive is wired; MVP smoke requires `paperless` = `"ok"`

### GET /batches/{id}

- **Description:** Batch status, documents, and recent job errors
- **Auth:** Public (internal network)
- **Path params:** `id` (UUID)
- **Response 200:**
  ```json
  {
    "id": "uuid",
    "status": "landed|ocr|split|enrich|commit|completed|failed|failed_partial|skipped_duplicate",
    "source_uri": "s3://bucket/landing/...",
    "page_count": 12,
    "error": null,
    "documents": [
      {
        "id": "uuid",
        "page_start": 0,
        "page_end": 2,
        "title": "string",
        "summary": "string",
        "category": "invoice|contract|bank|tax|correspondence|check|other",
        "tags": ["string"],
        "split_confidence": 0.92,
        "enrich_confidence": 0.88,
        "needs_review": false,
        "paperless_id": 123
      }
    ],
    "jobs": [
      {
        "id": "uuid",
        "type": "ingest.register|ocr.run|split.run|enrich.run|commit.archive|batch.finalize",
        "status": "queued|running|succeeded|failed|dead",
        "attempts": 1,
        "last_error": null
      }
    ],
    "created_at": "2026-07-04T00:00:00Z",
    "updated_at": "2026-07-04T00:00:00Z"
  }
  ```
- **Errors:** 404

### POST /batches/{id}/retry

- **Description:** Re-queue dead/failed jobs for a batch
- **Auth:** Public (internal network)
- **Path params:** `id` (UUID)
- **Request body:**
  ```json
  {
    "force": false
  }
  ```
- **Semantics:**
  - `force: false` (default): retry from first dead/failed stage; **do not** re-call paid OCR if OCR artifacts exist
  - `force: true`: allow re-OCR (paid path)
- **Response 202:**
  ```json
  {
    "id": "uuid",
    "status": "ocr",
    "requeued_jobs": ["ocr.run"]
  }
  ```
- **Errors:** 404, 409 (batch already running and not dead/failed)

### POST /hooks/landing

- **Description:** Optional notify that a landing prefix is ready (primary ingest is **poller**)
- **Auth:** Required header `X-Landing-Secret: {LANDING_HOOK_SECRET}`
- **Request body:**
  ```json
  {
    "prefix": "landing/2026/07/04/{batch_id}/"
  }
  ```
- **Response 202:**
  ```json
  {
    "batch_id": "uuid",
    "status": "landed"
  }
  ```
- **Errors:** 401 (missing/invalid secret), 404 (prefix empty/missing), 409 (duplicate checksum → may return existing batch with `skipped_duplicate`)

---

## Non-HTTP integration

### Landing poller

- Watches object store prefix `landing/`
- On stable object set (or manifest present), enqueues `ingest.register`
- Required for v1; webhook is optional

### Job types (worker)

| type | input | output |
|------|--------|--------|
| `ingest.register` | landing URI | Batch + Page stubs |
| `ocr.run` | batch_id | page text + OCR artifacts (skip if present unless force) |
| `split.run` | batch_id | Document rows |
| `enrich.run` | document_id | title, summary, category, tags |
| `commit.archive` | document_id | paperless_id |
| `batch.finalize` | batch_id | terminal status |

---

## Data models

### Batch

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| id | UUID | auto | Primary key |
| source_uri | string | yes | Landing prefix URI |
| page_count | int | yes | Pages detected |
| status | string | yes | Lifecycle status |
| error | string | no | Terminal error message |
| created_at | datetime | auto | ISO 8601 |
| updated_at | datetime | auto | ISO 8601 |

### Document

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| id | UUID | auto | Primary key |
| batch_id | UUID | yes | Parent batch |
| page_start | int | yes | Inclusive |
| page_end | int | yes | Inclusive |
| title | string | no | From enrich |
| summary | string | no | From enrich |
| category | string | no | Taxonomy value |
| tags | string[] | no | From enrich |
| split_confidence | float | yes | 0–1 |
| enrich_confidence | float | no | 0–1 |
| needs_review | bool | yes | Default false |
| paperless_id | int | no | External archive id |
| superseded_by | UUID | no | Re-split lineage |

### Artifact

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| kind | string | yes | `original` \| `ocr_json` \| `page_image` \| `final_pdf` |
| uri | string | yes | Object store URI |
| checksum | string | yes | Content hash for dedupe |

---

## Error responses

```json
{
  "detail": "string"
}
```

| Code | When |
|------|------|
| 401 | Landing hook secret invalid |
| 404 | Batch or landing prefix not found |
| 409 | Conflict (in-progress retry, duplicate handling) |
| 422 | Validation error |
| 503 | Health dependencies down |

---

## Config (env)

See `.env.example` at repo root.
