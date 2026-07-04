"""Provider selection driven by env (OCR_PROVIDER / LLM_PROVIDER).

Live providers (azure/openai) are M8; only `fake` is wired for the MVP, and
unknown values raise rather than silently degrading.
"""

from __future__ import annotations

from ..config import get_settings
from .base import ArchiveProvider, LlmProvider, OcrProvider
from .fake_llm import FakeLlmProvider
from .fake_ocr import FakeOcrProvider
from .paperless import PaperlessArchive


def get_ocr_provider() -> OcrProvider:
    name = get_settings().ocr_provider.lower()
    if name == "fake":
        return FakeOcrProvider()
    raise ValueError(
        f"OCR_PROVIDER='{name}' not available in MVP (only 'fake'); live providers are M8"
    )


def get_llm_provider() -> LlmProvider:
    name = get_settings().llm_provider.lower()
    if name == "fake":
        return FakeLlmProvider()
    raise ValueError(
        f"LLM_PROVIDER='{name}' not available in MVP (only 'fake'); live providers are M8"
    )


def get_archive_provider() -> ArchiveProvider:
    return PaperlessArchive()
