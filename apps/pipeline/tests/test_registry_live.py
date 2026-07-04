"""Registry wiring for live providers (mocked SDK clients)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from pipeline.providers.azure_ocr import AzureDocumentIntelligenceOcr
from pipeline.providers.openai_llm import OpenAiLlm
from pipeline.providers.registry import get_llm_provider, get_ocr_provider


def test_registry_rejects_unknown_ocr():
    with patch("pipeline.providers.registry.get_settings") as gs:
        gs.return_value.ocr_provider = "textract"
        with pytest.raises(ValueError, match="not supported"):
            get_ocr_provider()


def test_registry_rejects_unknown_llm():
    with patch("pipeline.providers.registry.get_settings") as gs:
        gs.return_value.llm_provider = "anthropic"
        with pytest.raises(ValueError, match="not supported"):
            get_llm_provider()


@patch("pipeline.providers.azure_ocr.DocumentIntelligenceClient")
@patch("pipeline.providers.azure_ocr.get_settings")
def test_azure_ocr_provider_calls_sdk(mock_settings, mock_client_cls):
    mock_settings.return_value.azure_document_intelligence_endpoint = "https://x.cognitiveservices.azure.com"
    mock_settings.return_value.azure_document_intelligence_key = "key"
    mock_settings.return_value.azure_ocr_chunk_pages = 25
    mock_settings.return_value.azure_ocr_chunk_max_bytes = 3_500_000
    poller = MagicMock()
    poller.result.return_value = type(
        "R",
        (),
        {
            "pages": [
                type(
                    "P",
                    (),
                    {
                        "page_number": 1,
                        "lines": [type("L", (), {"content": "Invoice"})()],
                        "words": [type("W", (), {"confidence": 0.9})()],
                    },
                )()
            ]
        },
    )()
    mock_client_cls.return_value.begin_analyze_document.return_value = poller

    from reportlab.lib.pagesizes import letter
    from reportlab.pdfgen import canvas
    import io

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=letter)
    c.drawString(72, 700, "Invoice")
    c.showPage()
    c.save()

    result = AzureDocumentIntelligenceOcr().ocr_document(buf.getvalue())
    assert result.pages[0].text == "Invoice"
    mock_client_cls.return_value.begin_analyze_document.assert_called_once()


@patch("pipeline.providers.openai_llm.OpenAI")
@patch("pipeline.providers.openai_llm.get_settings")
def test_openai_llm_provider_calls_sdk(mock_settings, mock_openai_cls):
    mock_settings.return_value.openai_api_key = "sk-test"
    mock_settings.return_value.openai_model = "gpt-4o-mini"
    choice = MagicMock()
    choice.message.content = (
        '{"title":"Bill","summary":"Due soon","category":"invoice","tags":["invoice"],"confidence":0.8}'
    )
    mock_openai_cls.return_value.chat.completions.create.return_value.choices = [choice]

    enr = OpenAiLlm().enrich("Invoice amount due $50", ["invoice", "other"])
    assert enr.category == "invoice"
    assert enr.title == "Bill"
    mock_openai_cls.return_value.chat.completions.create.assert_called_once()
