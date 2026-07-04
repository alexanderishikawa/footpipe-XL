# Document pipeline — agent plan

Authority: this file, then `docs/design.md`, then `AGENTS.md`.  
Execute milestones in order. Do not start M(n+1) until M(n) done-when passes.  
Default `OCR_PROVIDER=fake` and `LLM_PROVIDER=fake` until M8.  
Success for MVP: `make up && make test && make smoke` green.  
Commit after each green milestone on branch `agent/m{N}-...`.  
Stop and report if the same milestone fails twice.

Human operator steps (launch agent, secrets, accept): `docs/operator-guide.md`.

## M1 — Harness

- Compose: api, worker, postgres, redis, minio, paperless
- Makefile: `up`, `test`, `smoke`
- Ensure `.env.example`, `.gitignore`, `AGENTS.md`, `docs/design.md` remain accurate
- **Done when:** `make up` — all services healthy

## M2 — Schema + health

- Alembic models: Batch, Page, Document, Artifact, Job
- `GET /health`
- **Done when:** migrations apply; `/health` returns 200 for db/redis/object_store

## M3 — Ingest

- S3-compatible ObjectStore; poller on `landing/`
- Job `ingest.register`; checksum dedupe → `skipped_duplicate`
- Guardrails `MAX_PAGES_PER_BATCH`, `MAX_PAGES_PER_DAY`
- **Done when:** drop PDF in MinIO `landing/` creates Batch with pages

## M4 — Providers (fake)

- Interfaces: OcrProvider, LlmProvider, ArchiveProvider
- FakeOcrProvider, FakeLlmProvider + unit tests
- **Done when:** `make test` covers fakes with no network

## M5 — OCR + split + enrich

- `ocr.run` (skip if artifacts exist unless force)
- `split.run` (blank/barcode/text continuity; under-split; needs_review)
- `enrich.run` + `config/categories.yaml` + LLM fallback
- **Done when:** batch produces Document rows with title/category/tags via fakes

## M6 — Paperless + finalize + API

- PaperlessArchive; `commit.archive` idempotent
- `batch.finalize` terminal statuses
- `GET /batches/{id}`, `POST /batches/{id}/retry` per `docs/api-contract.md`
- Optional `POST /hooks/landing` with `X-Landing-Secret`
- **Done when:** documents appear in Paperless; retry re-queues dead jobs

## M7 — Golden smoke

- ≥3 fixtures under `fixtures/{name}/original.pdf` + `expected.json`
- `make smoke` asserts document counts / constraints
- **Done when:** `make smoke` green (fakes)

## M8 — Live providers (optional)

- Azure Document Intelligence + OpenAI implementations
- CI default remains fake; `LIVE=1 make smoke` when secrets present
- **Done when:** live smoke passes once with secrets; CI still fake-only

## M9 — Ops docs

- `docs/ops-setup.md`: scanner → landing, separator sheets, backup
- Expand README runbook + non-goals as needed
- **Done when:** operator can follow production ops without reading full design prose

## Non-goals (do not build)

Review UI, email ingest, embeddings/vector split, SSO, QuickBooks, any other product monorepo.

## Task map (reference)

| # | Task | Priority | Depends |
|---|------|----------|---------|
| 1 | Repo skeleton / Compose / Makefile | P0 | — |
| 2 | Postgres + Alembic | P0 | 1 |
| 3 | `GET /health` | P0 | 1, 2 |
| 4 | ObjectStore + poller + `ingest.register` | P0 | 2 |
| 5 | Provider interfaces + fakes | P0 | 1 |
| 6 | `ocr.run` | P0 | 4, 5 |
| 7 | `split.run` | P0 | 6 |
| 8 | `enrich.run` + categories | P0 | 5, 7 |
| 9 | Page guardrails | P0 | 4 |
| 10 | Paperless commit | P0 | 8 |
| 11 | `batch.finalize` | P0 | 10 |
| 12 | Fixtures + `make smoke` | P0 | 10, 11 |
| 13 | Batches API + retry + optional hook | P0 | 11 |
| 14 | Azure + OpenAI providers | P1 | 5, 12 |
| 15 | Ops docs | P1 | 12 |
| 16 | README polish | P1 | 12 |

### Waves

1. **W1** — tasks 1–3 (harness + schema)
2. **W2** — tasks 4–9 (pipeline fakes)
3. **W3** — tasks 10–13 (archive + smoke + API)
4. **W4** — tasks 14–16 (live + ops)
