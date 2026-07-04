"""Thin provider interfaces (docs/design.md § Provider interfaces).

Phase C swaps must not rewrite the app, so the pipeline depends only on these
Protocols, never on concrete SDKs.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable


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

    def health(self) -> bool: ...
