"""FakeOcrProvider — offline OCR by extracting embedded PDF text (no network).

Fixture PDFs embed real text per page (see fixtures/gen_fixtures.py), so the
fake provider is fully deterministic and needs no paid API. Pages with no
extractable text are treated as blank (a split signal).
"""

from __future__ import annotations

import io

from pypdf import PdfReader

from .base import OcrResult, PageOcrResult


class FakeOcrProvider:
    name = "fake"

    def ocr_document(self, pdf_bytes: bytes) -> OcrResult:
        reader = PdfReader(io.BytesIO(pdf_bytes))
        pages: list[PageOcrResult] = []
        for idx, page in enumerate(reader.pages):
            try:
                text = page.extract_text() or ""
            except Exception:
                text = ""
            text = text.strip()
            confidence = 0.99 if text else 0.5
            pages.append(PageOcrResult(page_index=idx, text=text, confidence=confidence))
        return OcrResult(pages=pages)

    def page_count(self, pdf_bytes: bytes) -> int:
        return len(PdfReader(io.BytesIO(pdf_bytes)).pages)
