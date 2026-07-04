"""Small PDF helpers built on pypdf (page count + page-range extraction)."""

from __future__ import annotations

import io

from pypdf import PdfReader, PdfWriter


def page_count(pdf_bytes: bytes) -> int:
    return len(PdfReader(io.BytesIO(pdf_bytes)).pages)


def extract_pages(
    pdf_bytes: bytes, start: int, end: int, metadata: dict[str, str] | None = None
) -> bytes:
    """Return a new PDF containing pages [start, end] inclusive (0-indexed).

    Optional `metadata` (keys must start with '/') is written as document info,
    used to stamp batch provenance so archived PDFs are uniquely identifiable.
    """
    reader = PdfReader(io.BytesIO(pdf_bytes))
    writer = PdfWriter()
    for i in range(start, end + 1):
        writer.add_page(reader.pages[i])
    if metadata:
        writer.add_metadata(metadata)
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()
