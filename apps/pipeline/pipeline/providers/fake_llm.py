"""FakeLlmProvider — deterministic enrichment from OCR text (no network).

Derives title/summary/category/tags from document text using the configured
category taxonomy plus lightweight keyword heuristics. Honors explicit fixture
markers when present:

- ``@@DOC category=<x>@@`` — optional ``date=YYYY-MM-DD`` on the same marker
- ``@@ORIGINATOR <name>@@``
- ``@@ENTITY <name>@@`` (repeatable)
"""

from __future__ import annotations

import re

from ..config import get_settings
from .base import Enrichment, normalize_enrichment_tags

_DOC_MARKER = re.compile(r"@@DOC\s+([^@]+)@@", re.IGNORECASE)
_ORIGINATOR_MARKER = re.compile(r"@@ORIGINATOR\s+(.+?)@@", re.IGNORECASE)
_ENTITY_MARKER = re.compile(r"@@ENTITY\s+(.+?)@@", re.IGNORECASE)
_BILL_TO = re.compile(r"bill\s+to:\s*(.+)", re.IGNORECASE)
_TAX_YEAR = re.compile(r"tax\s+year\s+(\d{4})", re.IGNORECASE)
_SINCERELY = re.compile(r"sincerely,?\s*(.+)", re.IGNORECASE)
_INVOICE_VENDOR = re.compile(r"^(.+?)\s+invoice\b", re.IGNORECASE)
_BANK_NAME = re.compile(r"^(.+?\bbank\b)(?:\s+statement)?$", re.IGNORECASE)

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


def _parse_doc_attrs(blob: str) -> dict[str, str]:
    attrs: dict[str, str] = {}
    for match in re.finditer(r"(\w+)=([^\s]+)", blob):
        attrs[match.group(1).lower()] = match.group(2)
    return attrs


def _first_content_line(lines: list[str]) -> str:
    for ln in lines:
        if _DOC_MARKER.search(ln) or _ORIGINATOR_MARKER.search(ln) or _ENTITY_MARKER.search(ln):
            continue
        if ln.strip():
            return ln.strip()
    return ""


def _heuristic_originator(category: str, text: str, first_line: str) -> tuple[str | None, float]:
    originator = _ORIGINATOR_MARKER.search(text)
    if originator:
        return originator.group(1).strip()[:256], 0.97

    if category == "tax" and re.search(r"\birs\b", text, re.IGNORECASE):
        return "IRS", 0.85
    if category == "invoice":
        m = _INVOICE_VENDOR.match(first_line)
        if m:
            return m.group(1).strip()[:256], 0.8
    if category == "bank":
        m = _BANK_NAME.match(first_line)
        if m:
            return m.group(1).strip()[:256], 0.8
    if category == "correspondence":
        m = _SINCERELY.search(text)
        if m:
            return m.group(1).strip()[:256], 0.75
    return None, 0.0


def _heuristic_entities(text: str) -> list[str]:
    entities = [m.group(1).strip()[:256] for m in _ENTITY_MARKER.finditer(text)]
    if entities:
        return entities[:10]
    bill_to = _BILL_TO.search(text)
    if bill_to:
        return [bill_to.group(1).strip()[:256]]
    return []


def _heuristic_document_date(category: str, text: str, doc_attrs: dict[str, str]) -> tuple[str | None, float]:
    if date := doc_attrs.get("date"):
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", date):
            return date, 0.97
    if category == "tax":
        m = _TAX_YEAR.search(text)
        if m:
            return f"{m.group(1)}-12-31", 0.8
    return None, 0.0


class FakeLlmProvider:
    name = "fake"

    def enrich(self, doc_text: str, categories: list[str]) -> Enrichment:
        text = (doc_text or "").strip()
        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]

        category = "other"
        confidence = 0.55
        doc_attrs: dict[str, str] = {}
        doc_match = _DOC_MARKER.search(text)
        if doc_match:
            doc_attrs = _parse_doc_attrs(doc_match.group(1))
            slug = doc_attrs.get("category", "").lower()
            if slug in categories:
                category = slug
                confidence = 0.97
        if category == "other":
            lowered = text.lower()
            for kw, cat in _KEYWORDS.items():
                if cat in categories and re.search(rf"\b{re.escape(kw)}\b", lowered):
                    category = cat
                    confidence = 0.8
                    break

        first_line = _first_content_line(lines)
        title = first_line[:120] if first_line else "Untitled document"

        summary = " ".join(lines[:4])[:400] if lines else "(no extractable text)"

        document_date, document_date_confidence = _heuristic_document_date(category, text, doc_attrs)
        originator, originator_confidence = _heuristic_originator(category, text, first_line)
        entities = _heuristic_entities(text)

        raw_tags = [category, "scanned"]
        settings = get_settings()
        normalized = normalize_enrichment_tags(
            category=category,
            tags=raw_tags,
            entities=entities,
            max_tags=settings.max_enrichment_tags,
        )

        return Enrichment(
            title=title,
            summary=summary,
            category=category,
            tags=normalized.tags,
            confidence=confidence,
            document_date=document_date,
            document_date_confidence=document_date_confidence,
            originator=originator,
            originator_confidence=originator_confidence,
            entities=entities,
            tags_overflow=normalized.needs_review_overflow,
        )
