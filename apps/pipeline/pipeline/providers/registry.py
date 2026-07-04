"""Provider selection driven by env (OCR_PROVIDER / LLM_PROVIDER).

`fake` is the CI/default. Live providers (`azure`, `openai`) require secrets;
unknown values raise rather than silently degrading.
"""

from __future__ import annotations

from ..config import get_settings
from .azure_ocr import AzureDocumentIntelligenceOcr
from .base import ArchiveProvider, LlmProvider, OcrProvider
from .fake_llm import FakeLlmProvider
from .fake_ocr import FakeOcrProvider
from .openai_llm import OpenAiLlm
from .paperless import PaperlessArchive


def get_ocr_provider() -> OcrProvider:
    name = get_settings().ocr_provider.lower()
    if name == "fake":
        return FakeOcrProvider()
    if name == "azure":
        return AzureDocumentIntelligenceOcr()
    raise ValueError(f"OCR_PROVIDER='{name}' not supported (use fake|azure)")


def get_llm_provider() -> LlmProvider:
    name = get_settings().llm_provider.lower()
    if name == "fake":
        return FakeLlmProvider()
    if name == "openai":
        return OpenAiLlm()
    raise ValueError(f"LLM_PROVIDER='{name}' not supported (use fake|openai)")


def get_archive_provider() -> ArchiveProvider:
    return PaperlessArchive()
