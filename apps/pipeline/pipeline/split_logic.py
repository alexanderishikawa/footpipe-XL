"""Pure document-split policy (docs/design.md § Split policy).

Signals: blank page, barcode separator, and text/layout continuity via an
explicit document-start marker. When unsure we keep pages together
(under-split > over-split). No DB or network here so it is trivially testable.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_MARKER = re.compile(r"@@DOC\b", re.IGNORECASE)
_BARCODE = re.compile(r"(@@SEP@@|\*\*\*\s*SEPARATOR\s*\*\*\*|BARCODE:SEP)", re.IGNORECASE)


@dataclass
class SplitDoc:
    page_start: int
    page_end: int
    confidence: float


def _is_blank(text: str) -> bool:
    return not text or not text.strip()


def _is_separator(text: str) -> bool:
    return _is_blank(text) or bool(_BARCODE.search(text))


def split_pages(pages: list[tuple[int, str]]) -> list[SplitDoc]:
    """Group (page_index, text) pairs into logical documents.

    `pages` must be ordered by page_index. Returns non-overlapping, gapless
    documents covering all non-separator pages.
    """
    docs: list[SplitDoc] = []
    start: int | None = None
    end: int | None = None
    # Confidence for the currently-open doc's *start* boundary.
    start_conf = 0.75

    def close() -> None:
        nonlocal start, end
        if start is not None and end is not None:
            docs.append(SplitDoc(page_start=start, page_end=end, confidence=round(start_conf, 2)))
        start, end = None, None

    for idx, text in pages:
        if _is_separator(text):
            # Separator ends the current document and is not part of any doc.
            close()
            # Next content page begins a new doc with a strong (separator) signal.
            start_conf = 0.9
            continue

        if _MARKER.search(text):
            if start is not None:
                # Explicit new-document marker breaks continuity.
                close()
            start = idx
            end = idx
            start_conf = 0.95
            continue

        if start is None:
            # First content page of a new document (no explicit signal yet).
            start = idx
            end = idx
            # start_conf retains value set by a preceding separator, else default.
            if start_conf not in (0.9, 0.95):
                start_conf = 0.75
        else:
            # Text continuity — extend current document.
            end = idx

    close()
    return docs
