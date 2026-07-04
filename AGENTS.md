# Agent rules — footpipe-XL

You are building the mailroom document pipeline **in this repository only**.

## Authority (in order)

1. `docs/plan.md` — execute milestones in order
2. `docs/design.md` — architecture and non-goals
3. `docs/api-contract.md` — HTTP control API shapes
4. This file — harness rules

## Rules

- Read `docs/design.md` and `docs/plan.md` before coding.
- Implement milestones in order; do **not** start M(n+1) until M(n) done-when is met.
- Default providers are `fake` (`OCR_PROVIDER=fake`, `LLM_PROVIDER=fake`) until the plan reaches live providers (M8).
- Sole entrypoints once harness exists: `make test`, `make up`, `make smoke`.
- Success for MVP = `make test` and `make smoke` green (fakes).
- Do **not** invent providers outside the interfaces in `docs/design.md`.
- Do **not** add review UI, SSO, email ingest, or accounting integrations in v1.
- Do **not** expand scope beyond v1 non-goals in `docs/design.md`.
- After each milestone, run the milestone’s done-when command and record evidence in the commit message or PR.
- Commit after each green milestone on a branch `agent/m{N}-...`.
- Stop and report if the same milestone fails twice after fix attempts.

## Layout (target)

```text
apps/pipeline/          # API + worker (created in M1+)
config/categories.yaml
docker-compose.yml
Makefile
fixtures/{name}/original.pdf
fixtures/{name}/expected.json
docs/
```

## Human operator

Cloud-agent launch, secrets, and acceptance are documented in `docs/operator-guide.md`. Do not rewrite that guide unless the plan’s M9 requires `docs/ops-setup.md` (scanner/production ops).

## Cursor Cloud specific instructions

Durable, non-obvious notes for future cloud agents. The VM snapshot already has Docker + Compose and `uv` installed; the startup update script only refreshes Python deps once a manifest exists.

- **Docker daemon is NOT auto-started on VM boot.** Run `sudo service docker start` once at the start of a session before any Docker/Compose work (idempotent). The daemon is configured for this VM with `storage-driver: fuse-overlayfs` and legacy iptables (`/etc/docker/daemon.json`); do not switch it back to `overlay2`.
- **`docker` needs `sudo`** unless your shell has picked up `docker` group membership (added during setup, effective only in a fresh login shell). Because of this, pass the override when using the Makefile: `make up DOCKER="sudo docker"` (same for `test`/`smoke`). Plain `make up` works only in a fresh login shell.
- **Entrypoints are `make up|test|smoke`** (see `Makefile`). `make up` builds images and blocks on `--wait` until every service is healthy; the `api` container only reports healthy once `/health` returns 200 with `paperless: ok`, and Paperless takes ~30–120s to boot on first start, so `up` can take a few minutes. `make test` runs the fake-provider unit tests with `--no-deps` (no network). `make smoke` runs the golden end-to-end over `fixtures/`.
- **Python toolchain is `uv`** (installed at `~/.local/bin`, on PATH via `~/.profile`/`~/.bashrc`). For fast local iteration outside Docker: `cd apps/pipeline && uv run --extra dev pytest` / `uv run --extra dev ruff check .`. System Python is 3.12.
- **Providers default to `fake`** (`OCR_PROVIDER=fake`, `LLM_PROVIDER=fake`) until milestone M8; no cloud keys are needed for M1–M7. `FakeOcrProvider` extracts embedded PDF text (fixtures embed real text + `@@DOC category=x@@` markers and `@@SEP@@` separators), so the fake pipeline is deterministic and offline.
- **Paperless** auto-creates the `admin`/`admin` superuser and the pipeline bootstraps an API token from those creds when `PAPERLESS_TOKEN` is empty. Paperless rejects byte-identical PDFs as duplicates and reports it as a task **FAILURE** (not a 400); `commit.archive` treats that as idempotent success and reuses the existing document id, so force-retries never create duplicate archive entries.
- **Smoke reruns:** `pipeline.smoke` salts each uploaded PDF with a per-run id so repeated `make smoke` runs create fresh batches instead of hitting `skipped_duplicate`. It asserts against each fixture's `expected.json` (document count, categories, needs-review ratio, archived-in-Paperless count).
