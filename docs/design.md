# Automated Document Pipeline (B→C)

**Status:** Approved  
**Date:** 2026-07-04  
**Repository:** footpipe-XL (standalone)  
**Build method:** Cloud agent with written plan + harness (`make up`, `make test`, `make smoke`).

**Operator setup (human):** see [`operator-guide.md`](operator-guide.md) (addendum).

## Intent

Physical documents (mail, checks, forms, invoices, contracts — high variation) are batch-scanned, uploaded to cloud storage, then automatically OCR’d, split/grouped into logical documents, summarized, categorized, tagged, and ingested into a searchable archive.

## Constraints and decisions

| Decision | Choice |
|----------|--------|
| Use case | Small business (invoices, contracts, correspondence) |
| Volume | ~500–2,000 pages/month |
| Human review | Hands-off v1; `needs_review` flags for optional queue later |
| Approach | **B→C**: hybrid MVP (managed OCR/LLM APIs), self-host migration path |
| System of record | Pipeline Postgres (batches, jobs, confidence) + **Paperless-ngx** (archive/search UI) |
| Default providers | Azure Document Intelligence + OpenAI; `fake` in CI |
| Ingest trigger | **Poller primary**; webhook optional with shared secret |

## Goals (v1)

- Scan batch lands in object storage automatically
- Pipeline: OCR → split → enrich → commit to Paperless
- Full-text + metadata search via Paperless
- Hands-off; confidence scores stored for future review UI
- Provider interfaces so Phase C swaps do not rewrite the app
- Agent-buildable: fixtures, fakes, Compose, milestone plan

## Non-goals (v1)

- Perfect handwriting OCR
- Human review UI (schema only)
- Multi-tenant SaaS
- QuickBooks / accounting posting
- Custom trained ML models
- Email ingest, Slack notifications
- Vector DB / embedding-based split (deferred to v1.1)
- PII redaction in summaries

---

## Architecture

```text
Scanner → landing zone (S3/R2) → poller → queue/worker
  → OcrProvider → split → LlmProvider → ArchiveProvider (Paperless)
  → artifacts in object store; state in Postgres
```

### Phase B vs Phase C

| Piece | Phase B (now) | Phase C (later) |
|-------|----------------|-----------------|
| Object storage | S3 or Cloudflare R2 | MinIO |
| OCR | Azure Document Intelligence (default) | OCRmyPDF / Tesseract (or docTR) |
| LLM | OpenAI (default) | Ollama / vLLM |
| Queue | Redis (Docker/managed) | Same, self-hosted |
| App + DB | Docker on VPS | Same images on NAS/hardware |
| Split logic | Application code | Unchanged |

App depends on thin interfaces: `OcrProvider`, `LlmProvider`, `ArchiveProvider`, `ObjectStore` (S3 API).

---

## Domain model (Postgres)

| Entity | Key fields |
|--------|------------|
| `Batch` | `source_uri`, `page_count`, `status` (`landed` → `ocr` → `split` → `enrich` → `commit` → `completed` \| `failed` \| `failed_partial` \| `skipped_duplicate`), `error` |
| `Page` | `batch_id`, `page_index`, `image_uri`, `ocr_uri`, `text` |
| `Document` | `batch_id`, `page_start`, `page_end`, `title`, `summary`, `category`, `tags[]`, `split_confidence`, `enrich_confidence`, `needs_review`, `paperless_id`, `superseded_by` (nullable) |
| `Artifact` | `kind` (`original`, `ocr_json`, `page_image`, `final_pdf`), `uri`, `checksum` |
| `Job` | `type`, `entity_id`, `attempts`, `status` (`queued` \| `running` \| `succeeded` \| `failed` \| `dead`), `last_error` |

Migrations: **Alembic** (or equivalent) required from day one; `make smoke` runs migrations.

---

## Provider interfaces

```text
OcrProvider
  ocr_document(uri) -> OcrResult
  ocr_page(uri) -> PageOcrResult

LlmProvider
  enrich(doc_text, optional_page_uris) -> Enrichment
    # title, summary, category, tags[], confidence

ArchiveProvider
  upsert_document(doc, pdf_uri, metadata) -> external_id

ObjectStore
  put / get / list / sign
```

Implementations:

- OCR: `AzureDocumentIntelligenceOcr`, `TextractOcr` (alt), `FakeOcrProvider`, later `OcrmypdfProvider`
- LLM: `OpenAiLlm`, `AnthropicLlm` (alt), `FakeLlmProvider`, later `OllamaLlm`
- Archive: `PaperlessArchive`
- Store: S3-compatible (`S3ObjectStore` / MinIO)

---

## Pipeline jobs (idempotent)

| Job | Input | Output | Rules |
|-----|--------|--------|-------|
| `ingest.register` | landing URI | `Batch` + page stubs | Duplicate checksum → `skipped_duplicate` |
| `ocr.run` | batch_id | page text + OCR artifacts | **Skip paid OCR** if artifacts exist unless `force=true` |
| `split.run` | batch_id | `Document` rows | No page gaps/overlaps; prefer merge when unsure |
| `enrich.run` | document_id | title/summary/category/tags | Fallback on LLM failure |
| `commit.archive` | document_id | `paperless_id` | Idempotent via `paperless_id` / checksum |
| `batch.finalize` | batch_id | terminal status | `completed` / `failed` / `failed_partial` |

### Split policy (v1)

Signals: **blank page**, **barcode separator**, **text/layout continuity**.  
When unsure: **keep pages together** (under-split > over-split).  
`split_confidence < SPLIT_MIN_CONFIDENCE` → still commit, set `needs_review=true`.

Embeddings / vector similarity: **v1.1**, not MVP.

---

## HTTP API (pipeline control)

See [`api-contract.md`](api-contract.md) for full request/response schemas.

| Method | Path | Notes |
|--------|------|-------|
| — | poller on `landing/` | **Primary** ingest trigger |
| `POST` | `/hooks/landing` | Optional; requires `LANDING_HOOK_SECRET` |
| `GET` | `/batches/{id}` | Status, docs, errors |
| `POST` | `/batches/{id}/retry` | Re-queue; `force=true` re-OCR |
| `GET` | `/health` | DB + store + queue |

Search UX: Paperless. This API is observability and control.

---

## Integration points

### Landing layout

```text
landing/{date}/{batch_id}/original.pdf
landing/{date}/{batch_id}/manifest.json   # optional
```

Scanner paths: vendor cloud → S3/R2, or NAS watch-folder sync, or SFTP. Same layout.

### Paperless

- Post PDF + title, tags, custom fields (`summary`, `batch_id`)
- Category → tag or custom field
- Internal Docker network; token auth
- Pipeline DB remains source of truth for jobs/confidence

### Category taxonomy (config YAML)

`invoice`, `contract`, `bank`, `tax`, `correspondence`, `check`, `other`

### Guardrails

```text
MAX_PAGES_PER_BATCH=200
MAX_PAGES_PER_DAY=500
SPLIT_MIN_CONFIDENCE=0.6
OCR_PROVIDER=azure|textract|fake
LLM_PROVIDER=openai|anthropic|fake
```

---

## Error handling

Principles:

1. Never overwrite originals
2. Fail jobs; prefer partial progress over all-or-nothing where safe
3. Idempotent retries
4. Hands-off: low confidence flags, does not block commit
5. Persist errors on `Job` / `Batch`

| Area | Behavior |
|------|----------|
| Incomplete landing | Grace period, then `incomplete_landing` |
| OCR 429/5xx | Backoff, max 3 attempts → `dead` |
| LLM failure | Fallback title/category `other` / OCR snippet |
| Paperless down | Retry commit; batch not completed until archived or dead |
| Partial archive | `failed_partial`; retry missing only |
| Poison jobs | `dead`; queue continues |

---

## Agent harness

| Piece | Purpose |
|-------|---------|
| Milestone plan (`docs/plan.md`) | Ordered tasks with done-when criteria |
| `docker-compose.yml` | api, worker, postgres, redis, paperless, minio |
| `.env.example` | All secret names |
| Fixtures | `fixtures/{name}/original.pdf` + **`expected.json`** |
| Contract tests | Fake providers; no paid APIs in default CI |
| Optional `LIVE=1` | Real Azure/OpenAI smoke |
| `make test` / `make up` / `make smoke` | Sole agent entrypoints |
| Alembic migrations | Applied on smoke |

### Fixture `expected.json` (required)

```json
{
  "documents": 3,
  "categories_any_of": ["invoice", "correspondence", "other"],
  "max_needs_review_ratio": 1.0
}
```

`make smoke` asserts expected document counts and constraints.

### Agent success definition

`make up && make smoke` processes fixtures end-to-end into Paperless (fakes or live keys).

---

## Ops checklist (human, post-agent)

1. Configure scanner upload to `landing/`
2. Set categories YAML
3. **Recommend** barcode or blank separator sheets between documents
4. Restrict Paperless access to operators
5. Backup **Postgres + object store + Paperless media together**
6. Use Paperless to search; `GET /batches/{id}` when failures occur

Production runbook: [`ops-setup.md`](ops-setup.md). Cloud-agent launch: [`operator-guide.md`](operator-guide.md).

---

## Effort and cost (order of magnitude)

| Item | Estimate |
|------|----------|
| Agent-led MVP (strong plan/harness) | ~4–8 weeks calendar |
| Phase B monthly (infra + OCR/LLM @ 500–2k pages) | ~$40–200/mo |
| Phase C monthly | Hosting ~$10–50/mo; API spend near zero if fully local |

---

## Blind review amendments (applied)

1. Poller-primary ingest; webhook requires secret
2. Mandatory OCR skip when artifacts exist
3. Golden `expected.json` per fixture
4. Standalone repo mandate
5. Default providers: Azure + OpenAI
6. v1 split without vector DB
7. Page count guardrails
8. Alembic from day one

## Deferred (Tier 2/3)

- Coupled backup script as automated deliverable
- Review UI for `needs_review`
- Embedding-based split (v1.1)
- Email ingest, notifications
- Token rotation runbook
- Deskew pre-processing, MICR for checks
- `make jobs-dead` CLI
