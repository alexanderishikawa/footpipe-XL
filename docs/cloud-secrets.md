# Cloud secrets — lock in live provider keys

Use this once so Azure/OpenAI keys survive every Cloud Agent session without re-pasting them.

## Recommended: Cursor Cloud Agents Secrets

1. Open **[Cursor → Cloud Agents → your environment → Secrets](https://cursor.com/dashboard/cloud-agents)**.
2. Add **environment variables** with these **exact names** (copy/paste the left column):

| Variable | Example / notes |
|----------|-----------------|
| `OCR_PROVIDER` | `azure` |
| `LLM_PROVIDER` | `openai` |
| `AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT` | `https://<resource>.cognitiveservices.azure.com/` |
| `AZURE_DOCUMENT_INTELLIGENCE_KEY` | Azure portal → Keys and Endpoint |
| `OPENAI_API_KEY` | `sk-...` |

3. After adding or changing secrets, **update the environment VM** (dashboard → **Start Setup Agent → Update Existing Env**) so injection picks up new names.
4. In the agent workspace, run:

```bash
make env-sync
make env-check    # shows set/missing — never prints values
```

`make up` and `make smoke` run `env-sync` automatically.

## Alternative: `.env.local` on the VM

If you prefer a file on the machine (e.g. saved in an environment snapshot):

```bash
cp .env.local.example .env.local
# edit .env.local — never commit it
make env-sync
```

`.env.local` is gitignored. Cursor **Secrets** remain the safer default.

## How it works

- Docker Compose reads **`.env`** at the repo root.
- Cursor injects secrets as process environment variables (not always visible in `printenv` in every shell).
- `scripts/sync_env.py` merges, in order: existing `.env` → `.env.local` → injected env vars, then writes `.env`.
- Secret values are **never** printed or committed; pre-commit hooks block accidental secret commits.

## Verify live stack

```bash
sudo service docker start
make up DOCKER="sudo docker"
curl -s localhost:8080/health
```

Upload a PDF at http://localhost:8080/upload, then `GET /batches/{id}`.

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| `make env-check` shows keys **missing** | Add the three Azure/OpenAI names above to Secrets; update the environment VM |
| Keys in Secrets but still missing | Names must match exactly (case-sensitive) |
| `OCR_PROVIDER=fake` after sync | Set `OCR_PROVIDER=azure` and `LLM_PROVIDER=openai` in Secrets |
| Works once, lost next session | Run `make env-sync` after each agent start, or keep keys in Secrets + `.cursor/environment.json` install hook |
