# Content-aware splitting, parsing, enriching & tagging (v1.2)

**Status:** Approved (autonomous build)
**Date:** 2026-07-04
**Milestone label:** v1.2 — real-scan robustness
**Authority:** `docs/design.md` (§ Split policy, non-goals unchanged). Additive to v1.1 (`001-rich-metadata-paperless.md`).

## Problem

v1 split relies on synthetic separators (`@@SEP@@`, `@@DOC` markers, fully-blank pages).
Real batch scans (the operator's "Doc Scans" bundles) have **none of these**:

- A single scanned PDF contains **many logical documents** (e.g. `0000001.pdf` = four
  credit-card statements from three different people; `0000256.pdf` = seven statements
  from Chase/Capital One/Discover).
- Boundaries are implicit: a statement **first page** (account-summary block), an
  **account-number change**, an **issuer/letterhead change**, or a **"Page 1 of N" reset**.
- Pages arrive **noisy** (rotated, garbled OCR), sometimes **out of order**, with
  **blank duplex backs** used inconsistently as separators.
- Checks OCR to near-garbage (security-pattern background); 1099s, letters also appear.

Result on a real 103-page scan: **1 giant "other" document, needs_review**. Enrichment
downstream is meaningless because the split is wrong.

## Goals

1. **Split without separators** — detect document starts from OCR-text signals with a
   tunable, evaluable scoring model; keep the "under-split when unsure" safety rule.
2. **Structured parsing** — one pure module (`docparse`) extracts per-page features
   (page-x-of-y, account tail, issuer, statement period, dates, amounts, doc-type) and
   per-document fields, shared by split + enrich.
3. **Better enrichment offline** — `FakeLlmProvider` uses `docparse` so the fake pipeline
   produces real categories/originators/entities/dates/tags on genuine scans (no Azure).
4. **Richer tagging** — issuer, doc-type, statement-period, and `entity:` tags.
5. **Backward compatible** — all existing marker/separator/blank fixtures and the 80
   unit tests keep passing; live OpenAI prompt refined but contract unchanged.
6. **Privacy** — real bundles contain PII (real names/accounts); they are **never
   committed**. Committed tests use synthetic PII-free fixtures that model the same
   signal patterns. Real-data evaluation runs locally via an offline harness.

## Non-goals (this design)

- Layout/vision ML or embeddings (research Phase 4; deferred).
- Reordering out-of-order pages (flag via `needs_review`, don't reorder in v1.2).
- Live OCR of image-only bundles locally (First Bundle needs Azure; logic still applies).
- Splitting a document across non-contiguous page ranges (ranges stay contiguous).

## `docparse` module (pure, testable)

```text
PageFeatures(index, text)
  chars, is_blank, is_separator_marker, is_doc_marker(+attrs)
  page_x, page_y            # "Page X of Y"
  account_tail              # normalized last 4-8 of account/"ending"
  issuer                    # chase | american express | citi | ...
  person                    # ALL-CAPS name line near letterhead (best effort)
  statement_period          # (start,end) ISO if a date range present
  best_date                 # best content date (ISO) + confidence
  amounts                   # new_balance, minimum_payment, amount_due (best effort)
  doc_type_hint             # bank | credit_card | tax | check | correspondence | invoice
  is_statement_first_page   # summary block: min_payment & (new_balance|payment_due_date)
  start_signals             # set[str] of fired first-page cues
  is_garbage                # many chars, low word ratio (check backgrounds)
```

`docparse` also exposes `document_fields(pages)` → aggregate a page-range into
`{category, originator, entities, document_date(+conf), account_tail, issuer, title,
summary}` used by the fake LLM and as deterministic fallback for the real LLM.

## Split algorithm (state machine + scoring)

Ordered pages → contiguous, gapless, non-overlapping documents. Blank/separator pages
between documents are **excluded** from ranges (matches v1 behavior & tests).

Per content page compute a **start score**; open a new document when it fires.

**Hard rules (evaluated first):**
| Rule | Effect |
|------|--------|
| `@@DOC` marker | START, conf 0.95 (legacy) |
| `@@SEP@@` / barcode / patch marker | consume as hard separator → next content is START, conf 0.90 |
| fully blank page | soft separator hint (`pending_blank`), page dropped |
| `page_x > 1` | force CONTINUE (overrides all start signals) |
| first content page overall | START |

**Start score (when no hard rule forces the decision):**
| Signal | Weight |
|--------|-------:|
| `page_x == 1` | 1.0 |
| account tail present & differs from open doc | 0.6 |
| statement first-page summary block | 0.6 |
| doc-type start (check / 1099 / invoice / "Dear" letter) | 0.4 |
| issuer present & differs from open doc | 0.3 |
| `pending_blank` (preceded by blank/separator) | 0.3 |
| statement period/date-range + account present | 0.2 |

START when `score >= SPLIT_START_THRESHOLD` (default **0.6**, env-tunable).
Continuation bias: same account as open doc & no `page_x` damps score.

**Confidence:** marker 0.95 · hard-sep 0.90 · `page_1_of` 0.95 · summary-block 0.90 ·
account-change 0.85 · else `clamp(0.6 … 0.97)` from score. A document whose grouped page
count disagrees with an observed `page_y`, or that starts on a weak score, gets
`split_confidence < SPLIT_MIN_CONFIDENCE` → `needs_review=true` (commit still proceeds).

## Enrichment & tagging

- `FakeLlmProvider.enrich` → `docparse.document_fields`: real category (bank/credit-card→
  `bank`, 1099→`tax`, check→`check`, "Dear…"→`correspondence`, invoice→`invoice`),
  originator = issuer, entities = person(s), `document_date` = statement period end /
  letter date, tags = `[category, issuer, doc-type, statement-period?]` + `entity:` tags.
- `OpenAiLlm` prompt: add statement/credit-card guidance and "the text may be one
  document out of a multi-doc scan; describe only this range". Contract unchanged.
- Deterministic fallback (LLM failure / empty OCR) now uses `docparse` instead of a bare
  first-line snippet.

## Evaluation (offline, local only — no PII committed)

`scripts`/harness extracts embedded OCR text from the real Second Bundle (searchable
PDFs), runs `split_pages`, and compares to hand-labeled ground truth
(`scratch/labels.json`, gitignored). Metrics: boundary precision/recall/F1, document
count error, false-merge & false-split counts, enrich category accuracy on labeled docs.
Target: F1 ≥ 0.8 on labeled multi-statement bundles, no regressions on synthetic fixtures.

## Success criteria

- [ ] 80 existing tests + new split/parse/enrich tests green (`uv run --extra dev pytest`).
- [ ] Synthetic multi-statement fixture splits into the correct document count.
- [ ] Real `0000001.pdf` (4 statements) and `0000256.pdf` (7 statements) split within ±1
      of ground truth in the offline harness.
- [ ] Fake enrichment on real statements yields `bank` category + issuer originator +
      person entity + a plausible content date, offline.
- [ ] No PII in committed files.
