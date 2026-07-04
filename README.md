# footpipe-XL

Automated mailroom document pipeline for a small business: batch-scanned mail, invoices, contracts, and forms are landed in object storage, OCR’d, split into logical documents, summarized, categorized, tagged, and archived in **Paperless-ngx** for search.

**Approach:** Hybrid B→C — managed OCR/LLM APIs now (Azure Document Intelligence + OpenAI), self-host later behind the same provider interfaces.

## Authority (read in order)

1. [`docs/plan.md`](docs/plan.md) — agent milestones and done-when checks
2. [`docs/design.md`](docs/design.md) — architecture and non-goals
3. [`AGENTS.md`](AGENTS.md) — harness rules for implementers
4. [`docs/api-contract.md`](docs/api-contract.md) — pipeline control HTTP API
5. [`docs/operator-guide.md`](docs/operator-guide.md) — cloud agent setup and MVP acceptance
6. [`docs/ops-setup.md`](docs/ops-setup.md) — **production**: scanner, landing, backups

## Status

MVP (M1–M7) and live providers (M8) are implemented. Default providers are `fake` (no cloud keys).

```bash
make up && make test && make smoke
```

**Live providers:** set Azure + OpenAI secrets in `.env`, then:

```bash
LIVE=1 make smoke
```

For production deployment (scanner → landing, separators, backups), follow [`docs/ops-setup.md`](docs/ops-setup.md).

## Runbook (local / Compose)

### Prerequisites

- Docker with Compose v2
- Copy `.env.example` → `.env` (never commit `.env`)

### Commands

| Command | Purpose |
|---------|---------|
| `make up` | Build and start full stack; wait until healthy |
| `make down` | Stop stack and **remove volumes** (data loss) |
| `make test` | Unit tests with fake providers (no network) |
| `make smoke` | End-to-end golden fixtures (fakes) |
| `LIVE=1 make smoke` | Same, with Azure OCR + OpenAI LLM |
| `make logs` | Follow service logs |
| `make ps` | Service status |

### Service URLs (default Compose)

| Service | URL |
|---------|-----|
| Pipeline API | http://localhost:8080 |
| Health | http://localhost:8080/health |
| Paperless | http://localhost:8000 |
| MinIO API / console | http://localhost:9000 / http://localhost:9001 |

Default Paperless login: `admin` / `admin` — change before production.

### Typical workflow

1. `make up`
2. Drop a PDF at `landing/{date}/{batch_id}/original.pdf` in the object store (or scan via your configured upload path — see ops setup).
3. Watch the worker process the batch: `make logs`
4. Search results in Paperless; inspect failures via `GET /batches/{id}`.

### Environment variables

See [`.env.example`](.env.example). Key production settings:

- `OCR_PROVIDER` / `LLM_PROVIDER` — `fake` (default) or `azure` / `openai`
- `AZURE_DOCUMENT_INTELLIGENCE_*`, `OPENAI_API_KEY` — live provider secrets
- `MAX_PAGES_PER_BATCH`, `MAX_PAGES_PER_DAY` — cost guardrails

## Quick links

| Doc | Audience |
|-----|----------|
| [Ops setup](docs/ops-setup.md) | Human: production scanner, landing, backup |
| [Epson ES-580W setup](docs/scanner-epson-es580w.md) | Scan to network folder → landing watcher |
| [Operator guide](docs/operator-guide.md) | Human: launch cloud agent, accept MVP |
| [Plan](docs/plan.md) | Cloud agent: build order |
| [Design](docs/design.md) | Architecture |
| [API contract](docs/api-contract.md) | Control API shapes |

## Non-goals (v1)

Review UI, email ingest, embeddings-based split, SSO, accounting integrations (QuickBooks), multi-tenant SaaS, PII redaction in summaries.
