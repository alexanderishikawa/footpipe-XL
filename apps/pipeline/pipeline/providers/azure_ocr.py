"""Azure Document Intelligence OCR (prebuilt-read model)."""

from __future__ import annotations

import io
import logging
from typing import Any

from azure.ai.documentintelligence import DocumentIntelligenceClient
from azure.core.credentials import AzureKeyCredential
from pypdf import PdfReader

from ..config import get_settings
from .base import OcrResult, PageOcrResult

log = logging.getLogger(__name__)

_MODEL_ID = "prebuilt-read"


def _page_text_and_confidence(page: Any) -> tuple[str, float]:
    lines = getattr(page, "lines", None) or []
    text = "\n".join(line.content for line in lines if getattr(line, "content", None)).strip()
    words = getattr(page, "words", None) or []
    confidences = [w.confidence for w in words if getattr(w, "confidence", None) is not None]
    if confidences:
        confidence = sum(confidences) / len(confidences)
    elif text:
        confidence = 0.9
    else:
        confidence = 0.5
    return text, confidence


def parse_analyze_result(result: Any, *, expected_pages: int) -> OcrResult:
    """Map Azure AnalyzeResult to pipeline OcrResult (0-based page_index)."""
    by_index: dict[int, PageOcrResult] = {}
    for page in getattr(result, "pages", None) or []:
        page_number = getattr(page, "page_number", None)
        if page_number is None:
            continue
        idx = int(page_number) - 1
        text, confidence = _page_text_and_confidence(page)
        by_index[idx] = PageOcrResult(page_index=idx, text=text, confidence=confidence)

    pages: list[PageOcrResult] = []
    for idx in range(expected_pages):
        if idx in by_index:
            pages.append(by_index[idx])
        else:
            pages.append(PageOcrResult(page_index=idx, text="", confidence=0.5))
    return OcrResult(pages=pages)


class AzureDocumentIntelligenceOcr:
    name = "azure"

    def __init__(self) -> None:
        settings = get_settings()
        if not settings.azure_document_intelligence_endpoint:
            raise ValueError("AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT is required for OCR_PROVIDER=azure")
        if not settings.azure_document_intelligence_key:
            raise ValueError("AZURE_DOCUMENT_INTELLIGENCE_KEY is required for OCR_PROVIDER=azure")
        self._client = DocumentIntelligenceClient(
            endpoint=settings.azure_document_intelligence_endpoint.rstrip("/"),
            credential=AzureKeyCredential(settings.azure_document_intelligence_key),
        )

    def ocr_document(self, pdf_bytes: bytes) -> OcrResult:
        expected_pages = len(PdfReader(io.BytesIO(pdf_bytes)).pages)
        log.info("azure OCR: analyzing %d-page PDF with %s", expected_pages, _MODEL_ID)
        poller = self._client.begin_analyze_document(
            _MODEL_ID,
            body=io.BytesIO(pdf_bytes),
            content_type="application/pdf",
        )
        result = poller.result()
        return parse_analyze_result(result, expected_pages=expected_pages)
