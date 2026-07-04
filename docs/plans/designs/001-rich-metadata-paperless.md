# Rich metadata in Paperless (Approach 2)

**Status:** Approved (brainstorm)  
**Date:** 2026-07-04  
**Milestone label:** v1.1 — metadata  
**Authority:** `docs/design.md` (non-goals unchanged); supersedes no v1 contracts except additive API fields.

## Problem

Operators want documents in Paperless to be **browsable by type, sender, entities, and content-derived dates**, not only full-text search on titles. Today:

- `enrich.run` populates `Document.title`, `summary`, `category`, `tags[]` in Postgres.
- `commit.archive` passes `category`, `tags`, `summary`, and `created` in a `metadata` dict.
- `PaperlessArchive.upsert_document` sends only **`title`** and optional **`created`** to `POST /api/documents/post_document/` — **tags and category are dropped**.

Live runs (Azure OCR + OpenAI) therefore produce enrichment in Postgres that never appears in Paperless.

## Goals

1. **Paperless reflects enrichment** — tags, document type (from category), correspondent (from originator), and `created` date when confidently extracted from content.
2. **Structured enrich output** — extend LLM contract with `document_date`, `originator`, `entities[]`; persist on `Document` for pipeline/API use.
3. **Idempotent archive** — metadata sync must not break duplicate-PDF handling or force re-upload.
4. **Fake-provider parity** — `FakeLlmProvider` and smoke fixtures remain deterministic offline.

## Non-goals (this design)

- Structured search API / SQL queries over amounts (Option A — deferred).
- Split-without-separators improvements (Option B).
- Rotation/deskew preprocess.
- Paperless custom fields (deferred to v1.2; JSON column holds extras until then).
- PII redaction in summaries (`docs/design.md` v1 non-goal).
- Human review UI (schema flags only).

---

## Architecture overview

### Components

```text
┌─────────────┐     ┌──────────────┐     ┌─────────────────┐     ┌──────────────────┐
│  enrich.run │────▶│  Enrichment  │────▶│ Document (PG)   │────▶│ commit.archive   │
│  LlmProvider│     │  (extended)  │     │ + metadata_json │     │                  │
└─────────────┘     └──────────────┘     └─────────────────┘     └────────┬─────────┘
                                                                            │
                    ┌───────────────────────────────────────────────────────┘
                    ▼
         ┌──────────────────────┐     ┌─────────────────────────────┐
         │ PaperlessArchive     │────▶│ Paperless-ngx REST          │
         │  post_document       │     │  documents, tags,             │
         │  sync_metadata (new) │     │  correspondents, doc types    │
         └──────────────────────┘     └─────────────────────────────┘
```

### Data flow (happy path)

1. **`enrich.run`** — Concatenate page text → `LlmProvider.enrich()` → map to `Document` columns + `metadata_json`.
2. **`commit.archive`** — Extract page-range PDF → `post_document` (title, optional created) → poll task → obtain `paperless_id`.
3. **`PaperlessArchive.sync_metadata(paperless_id, enrichment)`** — PATCH document with resolved tag IDs, correspondent ID, document type ID, and `created` (if `document_date` confidence ≥ threshold).
4. **Persist** — `doc.paperless_id` unchanged; `metadata_json` records what was pushed and Paperless entity IDs for debugging.

### Phasing within Approach 2

| Phase | Deliverable | Rationale |
|-------|-------------|-----------|
| **0 — Wire** | Pass existing `tags` + `category` through `post_document` or immediate PATCH | Fixes production gap with no LLM/schema change |
| **1 — Schema** | Extended `Enrichment`, DB migration, prompt update | Originators, entities, content dates |
| **2 — Sync** | `sync_metadata` + Paperless bootstrap helpers | Get-or-create tags/correspondents/types |

Phases 0→1→2 ship in one milestone branch but land as ordered commits with tests per phase.

---

## Key interfaces and contracts

### Extended `Enrichment` (`providers/base.py`)

```python
@dataclass
class Enrichment:
    title: str
    summary: str
    category: str                    # one of categories.yaml
    tags: list[str] = field(default_factory=list)
    confidence: float = 1.0

    # v1.1 additions
    document_date: str | None = None       # ISO date YYYY-MM-DD from content
    document_date_confidence: float = 0.0    # 0–1; sync only if >= METADATA_DATE_MIN_CONF
    originator: str | None = None            # sender org/person (display name)
    originator_confidence: float = 0.0
    entities: list[str] = field(default_factory=list)  # people/orgs mentioned (normalized strings)
```

**Tag normalization rules (LLM post-process):**

- Lowercase, trim, max 64 chars, dedupe.
- Always include `category` as a tag if not present.
- Entity tags prefixed optional: `entity:john-doe` vs bare `john doe` — **use bare normalized names** for Paperless UX; store canonical list in `entities[]`.
- Cap tags at **20**; overflow → `needs_review` on document.

### `Document` model (Alembic migration)

| Column | Type | Notes |
|--------|------|-------|
| `document_date` | `DATE NULL` | Content-derived; nullable |
| `originator` | `VARCHAR(256) NULL` | Display name |
| `entities` | `ARRAY(String)` | Default `[]` |
| `metadata_json` | `JSONB NULL` | Audit: raw LLM payload snippet, sync status, Paperless IDs |

Existing `tags[]`, `category`, `enrich_confidence`, `needs_review` unchanged.

### `ArchiveProvider` extension

```python
class ArchiveProvider(Protocol):
    def upsert_document(
        self, title: str, pdf_bytes: bytes, metadata: dict
    ) -> int: ...

    def sync_metadata(self, paperless_id: int, enrichment: Enrichment) -> None: ...
```

`PaperlessArchive` implements `sync_metadata`; `FakeArchiveProvider` (tests) no-ops or records calls.

### Paperless REST mapping

| Enrichment field | Paperless field | API |
|------------------|-----------------|-----|
| `category` | `document_type` | GET/POST `/api/document_types/` by name; map yaml slug → display name |
| `tags[]` | `tags` | GET/POST `/api/tags/`; PATCH document with tag id list |
| `originator` | `correspondent` | GET/POST `/api/correspondents/` by name (if confidence ≥ threshold) |
| `document_date` | `created` | PATCH `/api/documents/{id}/` ISO date (if date confidence ≥ threshold) |
| `title` | `title` | Already on upload; optional PATCH if enrich improved title post-upload |
| `summary` | — | **Not stored in Paperless v1.1**; remains Postgres + future custom field |

**Category → document type display names** (`config/categories.yaml` additive):

```yaml
categories:
  - slug: invoice
    paperless_type: Invoice
  - slug: bank
    paperless_type: Bank Statement
  # ...
```

### LLM JSON contract (`openai_llm.py` / `fake_llm.py`)

Required JSON keys from model:

```json
{
  "title": "string",
  "summary": "string",
  "category": "invoice|contract|...",
  "tags": ["string"],
  "confidence": 0.0,
  "document_date": "YYYY-MM-DD|null",
  "document_date_confidence": 0.0,
  "originator": "string|null",
  "originator_confidence": 0.0,
  "entities": ["string"]
}
```

System prompt additions:

- Prefer **document date** printed on the form (statement period end, letter date), not scan date.
- `originator` = issuer/sender (bank name, IRS, vendor), not recipient.
- `entities` = people and organizations **mentioned** (account holders, payees), max 10.
- If uncertain, null + low confidence; never invent account numbers.

### API contract (`GET /batches/{id}`) — additive

Per document object, add optional fields (backward compatible):

```json
{
  "document_date": "2024-03-15",
  "originator": "Chase Bank",
  "entities": ["John Dinglebarre"],
  "metadata_synced": true
}
```

---

## Integration with existing jobs

### `enrich.run` (`jobs.py`)

After successful `enrich()`:

```text
doc.title, summary, category, tags       ← existing
doc.document_date                        ← parse enrichment.document_date
doc.originator                           ← enrichment.originator if confidence ok
doc.entities                           ← enrichment.entities
doc.metadata_json                      ← { "enrich": { ... }, "sync": null }
doc.needs_review                       ← true if enrich_confidence < threshold
                                       OR tag count overflow OR date/originator conflict
```

On LLM failure: existing fallback; set `metadata_json.enrich.error`.

### `commit.archive` (`jobs.py`)

```text
1. upsert_document(title, pdf, { created?: upload fallback })
2. sync_metadata(paperless_id, enrichment_from_doc)
3. metadata_json.sync = { ok, tag_ids, correspondent_id, document_type_id, errors? }
4. on sync failure: log + job retry (transient) OR mark needs_review (permanent)
```

**Idempotency:** `sync_metadata` is safe to re-run (PATCH is upsert-style). Duplicate PDF path still returns existing `paperless_id`; sync runs on that id.

### Configuration (`config.py` / `.env.example`)

| Env | Default | Purpose |
|-----|---------|---------|
| `METADATA_DATE_MIN_CONF` | `0.7` | Min confidence to set Paperless `created` |
| `METADATA_ORIGINATOR_MIN_CONF` | `0.6` | Min confidence to set correspondent |
| `PAPERLESS_BOOTSTRAP_TYPES` | `true` | Auto-create document types from categories.yaml |

### Paperless bootstrap (ops)

One-time or on startup (worker):

- Ensure document types exist for each `categories.yaml` entry.
- No pre-seeding tags/correspondents (created on demand).

Document in `docs/ops-setup.md` § Paperless metadata (short addendum).

---

## Edge cases and error handling

| Case | Behavior |
|------|----------|
| Paperless tag API 400 / rate limit | Retry with backoff (3 attempts); then `needs_review`, `metadata_json.sync.partial` |
| Correspondent name > 128 chars | Truncate + hash suffix |
| `document_date` invalid ISO | Ignore date; log warning |
| `document_date` in future | Reject for Paperless `created`; keep in Postgres with flag |
| Category unknown | `other` + tag `other` |
| Duplicate PDF (checksum) | Existing id returned; **still run sync_metadata** (fixes metadata on re-enrich) |
| OpenAI 429 / failure | Existing title fallback; skip sync of originator/date; tags = `["other"]` |
| Empty OCR text | `needs_review`; minimal tags |
| Concurrent tag create race | Catch 400, re-GET by name |
| Force retry batch | Re-enrich overwrites Postgres; sync_metadata updates Paperless in place |

**Job failure policy:** `sync_metadata` failure does **not** fail the batch if `paperless_id` is set — batch completes with `needs_review` on affected documents (partial success aligns with `failed_partial` semantics for other stages).

---

## Testing strategy (design-level)

| Layer | Coverage |
|-------|----------|
| Unit | `parse_enrichment` with new fields; tag normalization; date validation |
| Unit | `PaperlessArchive.sync_metadata` with httpx mock (tag create, PATCH) |
| Unit | `FakeLlmProvider` emits deterministic dates/originators for fixtures |
| Integration | Extend smoke `expected.json` optional: `tags_any_of`, `paperless_has_tag` (HTTP check) |
| Contract | API schema tests for new document fields |

No live Paperless in default CI; optional `LIVE=1` metadata smoke.

---

## Blind review (Step 5)

Independent lenses — issues consolidated after separate critique.

### Lenses used

1. **Backend engineer** — API shape, idempotency, migrations  
2. **Security / privacy** — PII in tags, correspondent leakage  
3. **QA** — testability, smoke assertions  
4. **Operator / end-user** — Paperless UI browse experience  
5. **DevOps** — bootstrap, Paperless upgrades  
6. **CTO / scope** — alignment with B→C and deferred search  

### Tier 1 (must resolve — resolved in this doc)

| Issue | Lens | Resolution |
|-------|------|------------|
| Sync after duplicate upload skipped | Backend | **Always call `sync_metadata`** after resolving `paperless_id`, including duplicate path |
| Tag explosion / bad LLM tags | Security | Cap 20 tags; normalize; `needs_review` on overflow |
| Future date sets wrong archive timeline | QA | Reject future `document_date` for Paperless `created` |
| Category slug mismatch with Paperless types | Ops | Explicit `paperless_type` in `categories.yaml` |
| No audit trail when sync fails silently | Backend | `metadata_json.sync` required on every commit |

### Tier 2 (should resolve or defer explicitly)

| Issue | Resolution |
|-------|------------|
| Summary not visible in Paperless | Deferred v1.2 custom field; documented |
| Entity disambiguation (common names) | Defer; store raw strings; search API later |
| Correspondent dedup ("Chase" vs "JPMorgan Chase") | v1.1: exact name match only; fuzzy merge manual in Paperless |
| Rate limits on tag POST storm | Batch tag resolution cache per worker process |

### Tier 3 (nice-to-have)

| Issue | Defer to |
|-------|----------|
| Bi-directional sync (Paperless edit → Postgres) | v2 |
| Custom fields for amounts/account numbers | v1.2 extract.run |
| Automatic correspondent merge suggestions | v2 |

### Suppressed compromise check

No reviewer objections were overridden by majority; Tier 1 items are incorporated above.

---

## Open questions (for `/plan` or user)

1. **Paperless `created` semantics** — Should content `document_date` map to Paperless `created` (archive date) or `document_date` custom field when we add custom fields in v1.2? *Current design: `created` for v1.1 browse/sort.*
2. **Entity tag format** — Bare names vs `entity:` prefix? *Design chooses bare names; confirm operator preference.*
3. **Re-sync on manual Paperless edits** — Out of scope; confirm no expectation of two-way sync in v1.1.

---

## Success criteria

- [ ] After `make smoke`, Paperless documents show **category tag + at least one content tag** from enrichment.
- [ ] Live batch: bank/tax documents show **correspondent** when originator confidence ≥ threshold.
- [ ] `GET /batches/{id}` returns `document_date`, `originator`, `entities` when present.
- [ ] Re-run `commit.archive` on same doc updates Paperless metadata without duplicate PDF.
- [ ] CI remains fake-only green; no new cloud keys required.

---

## Transition

Design approved (brainstorm Approach 2). Run **`/plan`** to decompose into implementation tasks (migration → wire → enrich prompt → sync_metadata → ops doc → smoke).
