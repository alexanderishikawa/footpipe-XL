"""FakeLlmProvider — deterministic enrichment from OCR text (no network).

Delegates the heavy lifting to :mod:`pipeline.docparse` so the offline/fake
pipeline produces realistic categories, originators, entities, dates and tags on
genuine scanned statements — not just synthetic fixtures. Honors explicit
fixture markers when present:

- ``@@DOC category=<x>@@`` — optional ``date=YYYY-MM-DD`` on the same marker
- ``@@ORIGINATOR <name>@@``
- ``@@ENTITY <name>@@`` (repeatable)
"""

from __future__ import annotations

import re

from .. import docparse
from ..config import get_settings
from .base import Enrichment, normalize_enrichment_tags

_MARKER_KV = re.compile(r"(\w+)=([^\s@]+)")


class FakeLlmProvider:
    name = "fake"

    def enrich(self, doc_text: str, categories: list[str]) -> Enrichment:
        text = (doc_text or "").strip()
        fields = docparse.document_fields([text], categories)

        marker_attrs: dict[str, str] = {}
        m = docparse.DOC_MARKER_ATTRS.search(text)
        if m:
            marker_attrs = {
                mm.group(1).lower(): mm.group(2) for mm in _MARKER_KV.finditer(m.group(1))
            }
        marker_cat = marker_attrs.get("category", "").lower()

        if marker_cat and marker_cat in categories:
            confidence = 0.97
        elif fields.category != "other":
            confidence = 0.8
        else:
            confidence = 0.55

        settings = get_settings()
        normalized = normalize_enrichment_tags(
            category=fields.category,
            tags=fields.tags,
            entities=fields.entities,
            max_tags=settings.max_enrichment_tags,
        )

        return Enrichment(
            title=fields.title,
            summary=fields.summary,
            category=fields.category,
            tags=normalized.tags,
            confidence=confidence,
            document_date=fields.document_date,
            document_date_confidence=fields.document_date_confidence,
            originator=fields.originator,
            originator_confidence=fields.originator_confidence,
            entities=fields.entities,
            tags_overflow=normalized.needs_review_overflow,
        )
