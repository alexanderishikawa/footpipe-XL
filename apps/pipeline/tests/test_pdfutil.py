import io

from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas

from pipeline.pdfutil import extract_pages, page_count


def _pdf(n: int) -> bytes:
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=letter)
    for i in range(n):
        c.drawString(72, 700, f"page {i}")
        c.showPage()
    c.save()
    return buf.getvalue()


def test_page_count():
    assert page_count(_pdf(5)) == 5


def test_extract_pages_inclusive_range():
    out = extract_pages(_pdf(5), 1, 3)
    assert page_count(out) == 3


def test_extract_single_page_with_metadata():
    out = extract_pages(_pdf(3), 0, 0, metadata={"/Keywords": "batch:xyz"})
    assert page_count(out) == 1
