# Operator guide — cloud agent setup (addendum)

This is the **human** path to put a capable agent on a cloud machine with a plan and harness so it can build footpipe-XL. The agent implements code; you own accounts, secrets, repo, and go/no-go.

Architecture and product rules live in [`design.md`](design.md). Milestone order lives in [`plan.md`](plan.md).

## What you need beforehand

| Item | Why |
|------|-----|
| This repo (`footpipe-XL`) on GitHub/GitLab (private recommended) | Agent workspace |
| Cursor account with **Cloud Agents** (or equivalent: Codespaces + agent) | Runs the builder in the cloud with Docker |
| Optional later: Azure + OpenAI accounts | Live OCR/LLM; **not** required until M8 (fakes for M1–M7) |
| Seed files (already present) | `AGENTS.md`, `docs/design.md`, `docs/plan.md`, `.env.example`, `.gitignore` |

You do **not** need the office scanner until after `make smoke` is green.

## Phase 0 — Seed (done)

This repository is already seeded with:

```text
README.md
AGENTS.md
docs/design.md
docs/plan.md
docs/api-contract.md
docs/operator-guide.md   # this file
.env.example
.gitignore
```

If you have not yet: `git init`, commit, create a remote, and push.

```bash
cd /path/to/footpipe-XL
git init
git add .
git commit -m "chore: seed design, plan, and operator guide"
git remote add origin <your-remote-url>
git push -u origin main
```

## Phase 1 — Cloud environment (10–20 min)

**Preferred (Cursor):**

1. Open **this repo** in Cursor (File → Open Folder on `footpipe-XL`, or open from GitHub).
2. Ensure **Cloud Agents / Background Agents** are enabled for your Cursor plan.
3. Confirm the agent VM will have **Docker** (Compose is mandatory). If the default image lacks Docker, use a template that includes it, or instruct the agent to install Docker before M1.
4. Connect the repo’s **Git remote** so the agent can push branches/PRs.

**Alternative:** GitHub Codespaces with a `devcontainer.json` that installs Docker-in-Docker, then run the same agent prompt inside that codespace.

## Phase 2 — Secrets (5–15 min; live keys optional)

1. Copy `.env.example` → `.env` **only on the cloud agent environment** (or platform secret store), never commit it.
2. **Milestones M1–M7 (fakes only)** — minimum:

   ```text
   OCR_PROVIDER=fake
   LLM_PROVIDER=fake
   DATABASE_URL=...          # usually set by Compose
   REDIS_URL=...
   OBJECT_STORE_ENDPOINT=... # MinIO in Compose
   PAPERLESS_URL=...
   PAPERLESS_TOKEN=...       # agent may bootstrap Paperless and write token to .env
   ```

3. **Milestone M8 (live APIs)** — add when you are ready to spend:

   ```text
   OCR_PROVIDER=azure
   AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT=...
   AZURE_DOCUMENT_INTELLIGENCE_KEY=...
   LLM_PROVIDER=openai
   OPENAI_API_KEY=...
   ```

4. In Cursor Cloud Agents, prefer the product’s **Secrets / env** UI so keys are not pasted into chat logs.

## Phase 3 — Launch the agent (the prompt)

Start a **Cloud Agent** (or long-running agent session) on **this repo** with:

```text
You are building the mailroom document pipeline in THIS repo only (footpipe-XL).

Authority (in order):
1. docs/plan.md — execute milestones in order
2. docs/design.md — architecture and non-goals
3. docs/api-contract.md — HTTP control API
4. AGENTS.md — harness rules

Rules:
- Do not expand scope beyond v1 non-goals.
- Use OCR_PROVIDER=fake and LLM_PROVIDER=fake until the plan reaches M8 (live providers).
- After each milestone, run the milestone’s done-when command and paste evidence.
- Stop and report if make smoke fails after two fix attempts on the same milestone.
- Commit after each green milestone on a branch `agent/m{N}-...`.

Start at milestone M1. When MVP milestones are green, summarize how to run:
make up && make smoke
```

## Phase 4 — While the agent works (your job)

| Do | Don’t |
|----|--------|
| Check milestone commits / PR | Rewrite architecture mid-flight unless blocked |
| Answer blocking questions (Azure region, etc.) | Paste production PII into fixtures |
| Re-run agent with “continue from M3” if it stops | Ask it to “also add a nice React dashboard” |
| Verify `make smoke` yourself when it claims done | Merge to `main` without smoke evidence |

If the agent stalls: paste the failing command output and say **continue from milestone N only**.

## Phase 5 — Accept the MVP (your go/no-go)

On the cloud environment (or a machine with Docker):

```bash
cp .env.example .env   # if needed; set fake providers
make up
make test
make smoke
```

**Accept when:**

- All Compose services healthy
- Fixture batches appear in Paperless with expected document counts (`expected.json`)
- `GET /health` and `GET /batches/{id}` work
- README documents how to run and where logs live

Then merge the agent branch to `main` and tag `v0.1.0-mvp`.

## Phase 6 — Live providers (optional)

1. Put Azure + OpenAI secrets in the environment.
2. Launch agent (or continue) with: *Execute plan M8; keep fakes as CI default.*
3. Run `LIVE=1 make smoke` once; confirm spend is sane (guardrails `MAX_PAGES_PER_*`).
4. Keep CI on fakes so PRs stay free.

## Phase 7 — Deploy for real scanning (you, not the build agent)

1. Provision a small VPS (or home NAS) and run the same Compose stack.
2. Point real S3/R2 (or keep MinIO) and set `landing/` sync from the scanner.
3. Follow the ops checklist in `docs/design.md` (separator sheets, Paperless access, backups). After M9, prefer `docs/ops-setup.md`.
4. Do **not** leave the build agent as the production host unless you intentionally harden it.

## Checklist (print-friendly)

- [x] Standalone repo seeded (`footpipe-XL`)
- [ ] Remote created and seed committed/pushed
- [ ] Cloud agent environment with Docker
- [ ] Secrets for fakes/Compose (live keys deferred)
- [ ] Agent launched with plan-authority prompt
- [ ] Each milestone committed with evidence
- [ ] You ran `make up && make test && make smoke` and accepted
- [ ] Tag `v0.1.0-mvp`
- [ ] Live keys only when ready; CI stays on fakes
- [ ] Production host + scanner → `landing/` + ops checklist

## Failure modes

| Symptom | What to do |
|---------|------------|
| Agent builds in the wrong repo | Stop; open **footpipe-XL** only |
| Agent skips to React UI | Point at non-goals; reset to current milestone |
| `make smoke` fails on Paperless | Check Compose logs; agent fixes commit path only |
| Unexpected API bills | Set providers back to `fake`; verify guardrails; rotate keys if leaked to logs |
| Agent loop on same bug | You fix the one blocker or narrow the milestone; don’t let it thrash |
