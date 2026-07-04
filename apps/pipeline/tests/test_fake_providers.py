import io

from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas

from pipeline.categories import _DEFAULT
from pipeline.providers.fake_llm import FakeLlmProvider
from pipeline.providers.fake_ocr import FakeOcrProvider


def _pdf(pages: list[list[str]]) -> bytes:
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=letter)
    _, height = letter
    for page in pages:
        y = height - 72
        c.setFont("Helvetica", 14)
        for line in page:
            c.drawString(72, y, line)
            y -= 22
        c.showPage()
    c.save()
    return buf.getvalue()


def test_fake_ocr_extracts_embedded_text():
    pdf = _pdf([["Hello Invoice"], ["Second Page"]])
    result = FakeOcrProvider().ocr_document(pdf)
    assert result.page_count == 2
    assert "Hello Invoice" in result.pages[0].text
    assert "Second Page" in result.pages[1].text
    assert result.pages[0].confidence > 0.9


def test_fake_llm_uses_explicit_marker():
    enr = FakeLlmProvider().enrich("@@DOC category=contract@@\nMaster Agreement", _DEFAULT)
    assert enr.category == "contract"
    assert enr.title == "Master Agreement"
    assert enr.confidence >= 0.9


def test_fake_llm_keyword_fallback():
    enr = FakeLlmProvider().enrich("First National Bank\nAccount Balance: $10", _DEFAULT)
    assert enr.category == "bank"


def test_fake_llm_defaults_to_other():
    enr = FakeLlmProvider().enrich("random unclassifiable text", _DEFAULT)
    assert enr.category == "other"


def test_fake_llm_emits_metadata_from_doc_marker():
    text = (
        "@@DOC category=invoice date=2024-03-15@@\n"
        "@@ORIGINATOR ACME Supplies@@\n"
        "@@ENTITY Foo LLC@@\n"
        "ACME Supplies Invoice #1001\n"
        "Bill To: Foo LLC"
    )
    enr = FakeLlmProvider().enrich(text, _DEFAULT)
    assert enr.category == "invoice"
    assert enr.document_date == "2024-03-15"
    assert enr.document_date_confidence >= 0.9
    assert enr.originator == "ACME Supplies"
    assert enr.originator_confidence >= 0.9
    assert enr.entities == ["Foo LLC"]
    assert "entity:foo-llc" in enr.tags


def test_fake_llm_heuristic_metadata_for_bank_fixture():
    text = "@@DOC category=bank@@\nFirst National Bank Statement\nAccount Balance: $12,345.67"
    enr = FakeLlmProvider().enrich(text, _DEFAULT)
    assert enr.category == "bank"
    assert enr.originator == "First National Bank"
    assert enr.originator_confidence >= 0.8


def test_fake_llm_heuristic_tax_year_date():
    text = "IRS Form 1099-MISC\nTax year 2025 miscellaneous income statement"
    enr = FakeLlmProvider().enrich(text, _DEFAULT)
    assert enr.category == "tax"
    assert enr.originator == "IRS"
    assert enr.document_date == "2025-12-31"
    assert enr.document_date_confidence >= 0.8
