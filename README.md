# footpipe-XL

Automated mailroom document pipeline for a small business: batch-scanned mail, invoices, contracts, and forms are landed in object storage, OCR’d, split into logical documents, summarized, categorized, tagged, and archived in **Paperless-ngx** for search.

**Approach:** Hybrid B→C — managed OCR/LLM APIs now (Azure Document Intelligence + OpenAI), self-host later behind the same provider interfaces.

## Authority (read in order)

1. [`docs/plan.md`](docs/plan.md) — agent milestones and done-when checks
2. [`docs/design.md`](docs/design.md) — architecture and non-goals
3. [`AGENTS.md`](AGENTS.md) — harness rules for implementers
4. [`docs/api-contract.md`](docs/api-contract.md) — pipeline control HTTP API
5. [`docs/operator-guide.md`](docs/operator-guide.md) — **you**: cloud agent setup and acceptance

## Status

Seed only. Implementation is milestone-driven (`docs/plan.md`). MVP success:

```bash
make up && make test && make smoke
```

Until the agent builds the harness, those targets do not exist yet.

## Quick links

| Doc | Audience |
|-----|----------|
| [Operator guide](docs/operator-guide.md) | Human: launch cloud agent, accept MVP |
| [Plan](docs/plan.md) | Cloud agent: build order |
| [Design](docs/design.md) | Architecture |
| [API contract](docs/api-contract.md) | Control API shapes |

## Non-goals (v1)

Review UI, email ingest, embeddings-based split, SSO, accounting integrations.
