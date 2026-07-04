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

<!-- OMA:START — managed by oh-my-agent. Do not edit this block manually. -->

# oh-my-agent

## Architecture

- **SSOT**: `.agents/` directory (do not modify directly)
- **Response language**: Follows `language` in `.agents/oma-config.yaml`
- **Skills**: `.agents/skills/` (domain specialists)
- **Workflows**: `.agents/workflows/` (multi-step orchestration)
- **Subagents**: `@agent-name` (defined in `.cursor/agents/`)

## Per-Agent Dispatch

1. Resolve `target_vendor_for_agent` from `.agents/oma-config.yaml`.
2. If `target_vendor_for_agent === current_runtime_vendor`, use the runtime's native subagent path.
3. If vendors differ, or native subagents are unavailable, use `oma agent:spawn` for that agent only.

## Code Search

Prefer **serena MCP** tools over native find/grep when locating code — they are symbol-aware and faster on large repos. Fall back to native Read / Glob / Grep only when serena is unavailable or for plain file content reads.

| Task | Preferred tool |
|------|----------------|
| Locate a symbol definition (class / function / variable) | `find_symbol` |
| Find references / callers of a symbol | `find_referencing_symbols` |
| Outline a file's top-level symbols | `get_symbols_overview` |
| Pattern or regex search across the codebase | `search_for_pattern` |
| Find a file by name | `find_file` |
| List directory contents | `list_dir` |

## Workflows

Execute by naming the workflow in your prompt. Keywords are auto-detected via hooks.

| Workflow | File | Description |
|----------|------|-------------|
| orchestrate | `orchestrate.md` | Parallel subagents + Review Loop |
| work | `work.md` | Step-by-step with remediation loop |
| ultrawork | `ultrawork.md` | 5-Phase Gate Loop (11 reviews) |
| ralph | `ralph.md` | Persistent loop wrapping ultrawork with an independent judge |
| plan | `plan.md` | PM task breakdown |
| brainstorm | `brainstorm.md` | Design-first ideation |
| architecture | `architecture.md` | Architecture diagnosis, comparison, ADR |
| design | `design.md` | Design system + DESIGN.md with anti-pattern enforcement |
| review | `review.md` | QA audit |
| debug | `debug.md` | Root cause + minimal fix |
| deepsec | `deepsec.md` | Drive `oma-deepsec` end-to-end (setup / scan / pr-review / matchers / triage) |
| scm | `scm.md` | SCM + Git operations + Conventional Commits |
| docs | `docs.md` | Documentation drift verify + sync |
| recap | `recap.md` | Daily / period AI conversation recap |
| deepinit | `deepinit.md` | Project harness init (AGENTS.md / ARCHITECTURE.md / docs/) |
| convert | `convert.md` | File format conversion by category: documents→Markdown (oma-pdf/oma-hwp), image/video/audio transcode (ffmpeg) |
| video | `video.md` | Brief → script → assets → render-spec → Remotion (oma-video) |
| schedule | `schedule.md` | Register & manage time-based agent jobs via `oma schedule:*` |

(`tools` and `stack-set` are slash-invoked utilities, and `schedule` is a slash-invoked workflow (`oma schedule:*` time-based jobs); all are intentionally excluded from keyword detection.)

To execute: read and follow `.agents/workflows/{name}.md` step by step.

## Auto-Detection

Hooks: `UserPromptSubmit` / `beforeSubmitPrompt` (keyword detection)
Keywords defined in `.agents/hooks/core/triggers.json` (multi-language).
Persistent workflows (orchestrate, ultrawork, work, ralph) block termination until complete.
Deactivate: say "workflow done".

## Rules

1. **Do not modify `.agents/` files** (SSOT protection).
2. Workflows execute via keyword detection or explicit naming, never self-initiated.
3. Response language follows `.agents/oma-config.yaml`

## Project Rules

Read the relevant file from `.agents/rules/` when working on matching code.

| Rule | File | Scope |
|------|------|-------|
| backend | `.agents/rules/backend.md` | on request |
| commit | `.agents/rules/commit.md` | on request |
| database | `.agents/rules/database.md` | **/*.{sql,prisma} |
| debug | `.agents/rules/debug.md` | on request |
| design | `.agents/rules/design.md` | on request |
| dev-workflow | `.agents/rules/dev-workflow.md` | on request |
| frontend | `.agents/rules/frontend.md` | **/*.{tsx,jsx,css,scss} |
| i18n-arb | `.agents/rules/i18n-arb.md` | **/*.arb |
| i18n-guide | `.agents/rules/i18n-guide.md` | always |
| infrastructure | `.agents/rules/infrastructure.md` | **/*.{tf,tfvars,hcl} |
| market | `.agents/rules/market.md` | on request |
| mobile | `.agents/rules/mobile.md` | **/*.{dart,swift,kt} |
| quality | `.agents/rules/quality.md` | on request |

<!-- OMA:END -->
