"""Generate golden fixtures: original.pdf + expected.json per case.

Each page embeds real text (so FakeOcrProvider extracts it) including an
explicit `@@DOC category=<x>@@` marker on the first page of each logical
document, plus a `@@SEP@@` barcode separator to exercise that split signal.

Run: python fixtures/gen_fixtures.py [output_dir]
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas

# name -> (pages, expected). Each page is a list of text lines.
FIXTURES: dict[str, tuple[list[list[str]], dict]] = {
    "invoices-3": (
        [
            ["@@DOC category=invoice@@", "ACME Supplies Invoice #1001", "Bill To: Foo LLC", "Amount Due: $420.00"],
            ["@@DOC category=invoice@@", "Globex Invoice #2002", "Bill To: Foo LLC", "Amount Due: $88.50"],
            ["@@DOC category=invoice@@", "Initech Invoice #3003", "Bill To: Foo LLC", "Amount Due: $1,299.00"],
        ],
        {"documents": 3, "categories_any_of": ["invoice"], "max_needs_review_ratio": 1.0},
    ),
    "mixed-mail": (
        [
            ["@@DOC category=invoice@@", "Wayne Enterprises Invoice #7777", "Amount Due: $5,000.00"],
            ["(continued) line items and remittance details for invoice #7777"],
            ["@@DOC category=contract@@", "Master Service Agreement", "This agreement and terms and conditions..."],
            ["@@DOC category=correspondence@@", "Dear Customer,", "Thank you for your business.", "Sincerely, Support"],
        ],
        {
            "documents": 3,
            "categories_any_of": ["invoice", "contract", "correspondence"],
            "max_needs_review_ratio": 1.0,
        },
    ),
    "separated-docs": (
        [
            ["@@DOC category=bank@@", "First National Bank Statement", "Account Balance: $12,345.67"],
            ["@@SEP@@", "*** SEPARATOR ***"],
            ["IRS Form 1099-MISC", "Tax year 2025 miscellaneous income statement"],
        ],
        {"documents": 2, "categories_any_of": ["bank", "tax"], "max_needs_review_ratio": 1.0},
    ),
}


def write_pdf(path: Path, pages: list[list[str]]) -> None:
    c = canvas.Canvas(str(path), pagesize=letter)
    width, height = letter
    for page in pages:
        y = height - 72
        c.setFont("Helvetica", 14)
        for line in page:
            c.drawString(72, y, line)
            y -= 22
        c.showPage()
    c.save()


def main(out_dir: str) -> None:
    base = Path(out_dir)
    for name, (pages, expected) in FIXTURES.items():
        d = base / name
        d.mkdir(parents=True, exist_ok=True)
        write_pdf(d / "original.pdf", pages)
        (d / "expected.json").write_text(json.dumps(expected, indent=2) + "\n", encoding="utf-8")
        print(f"wrote {d}/original.pdf ({len(pages)} pages) + expected.json")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else str(Path(__file__).parent))
