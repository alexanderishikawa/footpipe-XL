"""Thin provider interfaces (docs/design.md § Provider interfaces).

Phase C swaps must not rewrite the app, so the pipeline depends only on these
Protocols, never on concrete SDKs.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

_MAX_TAG_LEN = 64
_ENTITY_PREFIX = "entity:"


@dataclass
class PageOcrResult:
    page_index: int
    text: str
    confidence: float = 1.0


@dataclass
class OcrResult:
    pages: list[PageOcrResult] = field(default_factory=list)

    @property
    def page_count(self) -> int:
        return len(self.pages)


@dataclass
class Enrichment:
    title: str
    summary: str
    category: str
    tags: list[str] = field(default_factory=list)
    confidence: float = 1.0
    # v1.1 — content-derived metadata (synced to Paperless in later tasks)
    document_date: str | None = None
    document_date_confidence: float = 0.0
    originator: str | None = None
    originator_confidence: float = 0.0
    entities: list[str] = field(default_factory=list)
    tags_overflow: bool = False


@dataclass(frozen=True)
class TagNormalizationResult:
    """Normalized Paperless tag list plus overflow guard for needs_review."""

    tags: list[str]
    needs_review_overflow: bool


def _slugify_entity_name(name: str) -> str:
    """Lowercase hyphenated slug for entity: tags (max length respects tag cap)."""
    s = name.strip().lower()
    s = re.sub(r"[^\w\s-]", "", s, flags=re.UNICODE)
    s = re.sub(r"[-\s]+", "-", s).strip("-")
    max_slug = _MAX_TAG_LEN - len(_ENTITY_PREFIX)
    if not s:
        return "unknown"
    return s[:max_slug]


def normalize_tag(tag: str) -> str:
    """Lowercase, trim, and cap a single tag string."""
    return tag.strip().lower()[:_MAX_TAG_LEN]


def entity_tag(entity: str) -> str:
    """Build an entity-prefixed tag from a display name."""
    return f"{_ENTITY_PREFIX}{_slugify_entity_name(entity)}"


def normalize_enrichment_tags(
    *,
    category: str,
    tags: list[str],
    entities: list[str],
    max_tags: int = 20,
) -> TagNormalizationResult:
    """Normalize LLM tags + entities for Paperless; cap at max_tags.

    Rules: lowercase/trim/dedupe; always include category; entity names become
  entity:{slug} tags; overflow sets needs_review_overflow.
    """
    cat = normalize_tag(category) or "other"
    seen: set[str] = set()
    ordered: list[str] = []

    def _add(tag: str) -> None:
        t = normalize_tag(tag)
        if not t or t in seen:
            return
        seen.add(t)
        ordered.append(t)

    _add(cat)
    for raw in tags:
        if raw.strip().lower().startswith(_ENTITY_PREFIX):
            _add(raw)
        else:
            _add(raw)
    for ent in entities:
        _add(entity_tag(ent))

    overflow = len(ordered) > max_tags
    if overflow:
        ordered = ordered[:max_tags]
    return TagNormalizationResult(tags=ordered, needs_review_overflow=overflow)


@runtime_checkable
class OcrProvider(Protocol):
    name: str

    def ocr_document(self, pdf_bytes: bytes) -> OcrResult: ...


@runtime_checkable
class LlmProvider(Protocol):
    name: str

    def enrich(self, doc_text: str, categories: list[str]) -> Enrichment: ...


@runtime_checkable
class ArchiveProvider(Protocol):
    name: str

    def upsert_document(
        self, title: str, pdf_bytes: bytes, metadata: dict
    ) -> int: ...

    def sync_metadata(
        self, paperless_id: int, enrichment_or_metadata: Enrichment | dict[str, Any]
    ) -> dict[str, Any]: ...

    def health(self) -> bool: ...
