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

- **Repo is seed-only.** Today the repo contains only docs + `.env.example`/`.gitignore` — there is no `apps/`, `docker-compose.yml`, `Makefile`, Python project, tests, or fixtures yet. The app is built milestone-by-milestone per `docs/plan.md`; `make up|test|smoke` do not exist until the harness (M1) is built.
- **Docker daemon is NOT auto-started on VM boot.** Run `sudo service docker start` once at the start of a session before any Docker/Compose work (idempotent). The daemon is configured for this VM with `storage-driver: fuse-overlayfs` and legacy iptables (`/etc/docker/daemon.json`); do not switch it back to `overlay2`.
- **`docker` needs `sudo`** unless your shell has picked up `docker` group membership (added during setup, effective only in a fresh login shell). Using `sudo docker ...` always works.
- **Python toolchain is `uv`** (installed at `~/.local/bin`, on PATH via `~/.profile`/`~/.bashrc`). Prefer `uv sync` / `uv run`. System Python is 3.12.
- **Providers default to `fake`** (`OCR_PROVIDER=fake`, `LLM_PROVIDER=fake`) until milestone M8; no cloud keys are needed for M1–M7. Copy `.env.example` → `.env` for local runs (never commit `.env`).
- **Backing services** (per `docs/design.md`): Postgres, Redis, MinIO (S3-compatible object store), and Paperless-ngx. Compose is the intended way to run them once M1 adds `docker-compose.yml`.
