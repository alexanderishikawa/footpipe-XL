"""Unit tests for live provider parsing (no network)."""

from __future__ import annotations

from types import SimpleNamespace

from pipeline.providers.azure_ocr import iter_page_chunks, parse_analyze_result
from pipeline.providers.openai_llm import _build_prompt, parse_enrichment


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


def test_parse_analyze_result_applies_page_offset():
    result = SimpleNamespace(
        pages=[SimpleNamespace(page_number=1, lines=[SimpleNamespace(content="A")], words=[])]
    )
    ocr = parse_analyze_result(result, expected_pages=1, page_offset=10)
    assert ocr.pages[0].page_index == 10
    assert ocr.pages[0].text == "A"


def test_iter_page_chunks_respects_page_limit():
    import io

    from reportlab.lib.pagesizes import letter
    from reportlab.pdfgen import canvas

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=letter)
    for _ in range(5):
        c.drawString(72, 700, "x")
        c.showPage()
    c.save()
    pdf = buf.getvalue()
    chunks = list(iter_page_chunks(5, max_pages=2, pdf_bytes=pdf, max_bytes=10_000_000))
    assert len(chunks) == 3
    assert chunks[0][0:2] == (0, 1)
    assert chunks[1][0:2] == (2, 3)
    assert chunks[2][0:2] == (4, 4)


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


def test_parse_enrichment_v11_fields_and_tag_normalization():
    cats = ["invoice", "other"]
    enr = parse_enrichment(
        {
            "title": "March Invoice",
            "summary": "Services rendered",
            "category": "invoice",
            "tags": ["vendor:acme"],
            "confidence": 0.88,
            "document_date": "2024-03-15",
            "document_date_confidence": 0.91,
            "originator": "Acme Corp",
            "originator_confidence": 0.85,
            "entities": ["Jane Doe", "Foo LLC"],
        },
        cats,
    )
    assert enr.document_date == "2024-03-15"
    assert enr.document_date_confidence == 0.91
    assert enr.originator == "Acme Corp"
    assert enr.originator_confidence == 0.85
    assert enr.entities == ["Jane Doe", "Foo LLC"]
    assert enr.tags[0] == "invoice"
    assert "entity:jane-doe" in enr.tags
    assert "entity:foo-llc" in enr.tags


def test_parse_enrichment_safe_defaults_for_missing_v11_fields():
    enr = parse_enrichment(
        {"title": "T", "summary": "S", "category": "other", "tags": [], "confidence": 0.5},
        ["other"],
    )
    assert enr.document_date is None
    assert enr.document_date_confidence == 0.0
    assert enr.originator is None
    assert enr.originator_confidence == 0.0
    assert enr.entities == []


def test_parse_enrichment_rejects_invalid_document_date():
    enr = parse_enrichment(
        {
            "title": "T",
            "summary": "S",
            "category": "other",
            "tags": [],
            "confidence": 0.5,
            "document_date": "not-a-date",
            "document_date_confidence": 0.9,
        },
        ["other"],
    )
    assert enr.document_date is None
    assert enr.document_date_confidence == 0.0


def test_build_prompt_includes_content_date_and_originator_guidance():
    system, _ = _build_prompt("sample", ["invoice", "other"])
    lowered = system.lower()
    assert "document_date" in lowered
    assert "scan" in lowered
    assert "originator" in lowered
    assert "recipient" in lowered
    assert "entities" in lowered
