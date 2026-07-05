# Local evaluation corpus (not in repo)

Real scanned PDFs for manual and live-provider testing. **Do not commit** these paths or copy files into `fixtures/` without redaction and an explicit decision.

## Locations (operator machine)

| Path | Notes |
|------|--------|
| `C:\Users\Mehdi\OneDrive\Documents\Doc Scans\` | Parent trove |
| `C:\Users\Mehdi\OneDrive\Documents\Doc Scans\First Bundle\` | ~34 PDFs, Jul 2025 scans — **image-only, no text layer** (needs live Azure OCR) |
| `C:\Users\Mehdi\OneDrive\Documents\Doc Scans\Second Bundle\` | ~500 PDFs — **searchable (embedded OCR text)**; usable offline |

## Usage

- **CI / `make smoke`:** continues to use committed `fixtures/` with fake providers only.
- **Live eval:** upload via `http://localhost:8080/upload` or drop into MinIO `landing/` when stack is up (`make up`).

## Offline split/enrich eval (no PII committed)

The Second Bundle carries an embedded OCR text layer, so split/parse/enrich logic
can be evaluated locally with **no Azure and no PII in the repo**. Tooling lives in
`scratch/` (gitignored — it contains real names/accounts and must never be committed):

1. `python scratch/extract_pages.py --all` — system Python + PyMuPDF dumps per-page
   text to `scratch/pagetext/*.json` (decoupled from pipeline deps).
2. From `apps/pipeline`: `uv run python ../../scratch/eval_split.py <name...>` (or
   `--all --quiet`) runs the real `pipeline.split_logic.split_pages` +
   `pipeline.docparse.document_fields` and reports per-document ranges, categories,
   issuer, date, account tail, and (against `scratch/labels.json`) document-count
   error and boundary precision/recall/F1.

Ground-truth labels live in `scratch/labels.json` (local only). Committed regression
coverage uses **synthetic, PII-free** fixtures in `tests/test_docparse.py` and
`tests/test_split_content.py` that model the same signal patterns
(see `docs/plans/designs/002-content-aware-splitting.md`).
