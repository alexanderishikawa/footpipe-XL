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
