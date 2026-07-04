"""Unit tests for live provider parsing (no network)."""

from __future__ import annotations

from types import SimpleNamespace

from pipeline.providers.azure_ocr import parse_analyze_result
from pipeline.providers.openai_llm import parse_enrichment


def test_parse_analyze_result_maps_pages_and_confidence():
    result = SimpleNamespace(
        pages=[
            SimpleNamespace(
                page_number=1,
                lines=[SimpleNamespace(content="Hello Invoice")],
                words=[SimpleNamespace(confidence=0.95), SimpleNamespace(confidence=0.85)],
            ),
            SimpleNamespace(
                page_number=2,
                lines=[],
                words=[],
            ),
        ]
    )
    ocr = parse_analyze_result(result, expected_pages=3)
    assert ocr.page_count == 3
    assert ocr.pages[0].text == "Hello Invoice"
    assert 0.89 < ocr.pages[0].confidence < 0.91
    assert ocr.pages[1].text == ""
    assert ocr.pages[1].confidence == 0.5
    assert ocr.pages[2].page_index == 2


def test_parse_enrichment_normalizes_category_and_tags():
    cats = ["invoice", "contract", "other"]
    enr = parse_enrichment(
        {
            "title": "Acme Invoice #42",
            "summary": "Amount due $100",
            "category": "invoice",
            "tags": ["vendor:acme"],
            "confidence": 0.92,
        },
        cats,
    )
    assert enr.category == "invoice"
    assert "invoice" in enr.tags
    assert enr.confidence == 0.92


def test_parse_enrichment_unknown_category_becomes_other():
    enr = parse_enrichment(
        {"title": "X", "summary": "Y", "category": "mystery", "tags": [], "confidence": 2.0},
        ["invoice", "other"],
    )
    assert enr.category == "other"
    assert enr.confidence == 1.0
