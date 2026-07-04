# Local evaluation corpus (not in repo)

Real scanned PDFs for manual and live-provider testing. **Do not commit** these paths or copy files into `fixtures/` without redaction and an explicit decision.

## Locations (operator machine)

| Path | Notes |
|------|--------|
| `C:\Users\Mehdi\OneDrive\Documents\Doc Scans\` | Parent trove |
| `C:\Users\Mehdi\OneDrive\Documents\Doc Scans\First Bundle\` | First batch (~34 PDFs, Jul 2025 scans) |

## Usage

- **CI / `make smoke`:** continues to use committed `fixtures/` with fake providers only.
- **Live eval:** upload via `http://localhost:8080/upload` or drop into MinIO `landing/` when stack is up (`make up`).
- **Future:** split/enrich/metadata work (see `docs/plans/designs/001-rich-metadata-paperless.md`) can use a small labeled subset from this trove; keep ground-truth notes local until redacted fixtures exist.
