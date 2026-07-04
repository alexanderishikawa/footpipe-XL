"""FakeLlmProvider — deterministic enrichment from OCR text (no network).

Derives title/summary/category/tags from document text using the configured
category taxonomy plus lightweight keyword heuristics. Honors an explicit
`@@DOC category=<x>@@` marker embedded by fixtures when present.
"""

from __future__ import annotations

import re

from .base import Enrichment

_MARKER = re.compile(r"@@DOC\s+category=([a-z_]+)@@", re.IGNORECASE)

# keyword -> category, scanned in order (first match wins), so more specific
# indicators are listed before generic ones (e.g. tax before the generic
# bank "statement").
_KEYWORDS: dict[str, str] = {
    "pay to the order": "check",
    "check no": "check",
    "irs": "tax",
    "form 1099": "tax",
    "1099": "tax",
    "tax year": "tax",
    "amount due": "invoice",
    "bill to": "invoice",
    "invoice": "invoice",
    "master service agreement": "contract",
    "terms and conditions": "contract",
    "agreement": "contract",
    "contract": "contract",
    "account balance": "bank",
    "bank statement": "bank",
    "statement": "bank",
    "dear": "correspondence",
    "sincerely": "correspondence",
}


class FakeLlmProvider:
    name = "fake"

    def enrich(self, doc_text: str, categories: list[str]) -> Enrichment:
        text = (doc_text or "").strip()
        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]

        category = "other"
        confidence = 0.55
        marker = _MARKER.search(text)
        if marker and marker.group(1).lower() in categories:
            category = marker.group(1).lower()
            confidence = 0.97
        else:
            lowered = text.lower()
            for kw, cat in _KEYWORDS.items():
                if cat in categories and re.search(rf"\b{re.escape(kw)}\b", lowered):
                    category = cat
                    confidence = 0.8
                    break

        # Title: first meaningful, non-marker line.
        title = "Untitled document"
        for ln in lines:
            if _MARKER.search(ln):
                continue
            title = ln[:120]
            break

        summary = " ".join(lines[:4])[:400] if lines else "(no extractable text)"
        tags = sorted({category, *(t for t in ("scanned",))})
        return Enrichment(
            title=title, summary=summary, category=category, tags=tags, confidence=confidence
        )
