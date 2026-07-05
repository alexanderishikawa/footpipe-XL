"""Content-aware document split policy (docs/plans/designs/002-*).

Groups an ordered page stream into logical documents *without* relying on
physical separators. Signals (weighted): "Page 1 of N" resets, account-number
changes, issuer/letterhead changes, statement first-page summary blocks,
doc-type starts (checks/1099s/letters), and blank/separator hints. Legacy
synthetic markers (``@@DOC``, ``@@SEP@@``) still work so existing fixtures pass.

Rules of the road (unchanged invariants):
- Documents are contiguous, gapless, non-overlapping over *content* pages.
- Blank and separator pages between documents are excluded from ranges.
- When unsure, keep pages together (under-split > over-split).
- Pure: no DB, no network — trivially unit-testable.
"""
from __future__ import annotations

from dataclasses import dataclass

from . import docparse
from .config import get_settings

# Start-score weights (see design doc). Tunable via evaluation.
_W_PAGE_1_OF = 1.0
_W_ACCOUNT_CHANGE = 0.6
_W_SUMMARY_BLOCK = 0.6
_W_STMT_HEADER = 0.5
_W_DOCTYPE_START = 0.4
_W_ISSUER_CHANGE = 0.3
_W_PENDING_BLANK = 0.3
_W_PERIOD_WITH_ACCT = 0.2
_DEFAULT_START_THRESHOLD = 0.6

_DOCTYPE_START_TYPES = frozenset({"check", "tax", "invoice", "correspondence", "contract"})


@dataclass
class SplitDoc:
    page_start: int
    page_end: int
    confidence: float


@dataclass
class _OpenDoc:
    start: int
    last_content: int
    account_tail: str | None
    issuer: str | None
    page_y: int | None
    n_content: int
    confidence: float


def _plausible_page_y(feat: docparse.PageFeatures) -> bool:
    """A trustworthy 'of N' total (guards OCR noise like 'Page 2 of 1')."""
    return (
        feat.page_y is not None
        and 2 <= feat.page_y <= 60
        and (feat.page_x is None or feat.page_x <= feat.page_y + 1)
    )


def _score_start(
    feat: docparse.PageFeatures, cur: _OpenDoc, pending_blank: bool
) -> tuple[float, str]:
    """Return (start_score, reason) for a content page given the open document."""
    score = 0.0
    reason = "weak"

    if feat.page_x == 1:
        return _W_PAGE_1_OF, "page_1_of"

    same_acct = docparse.same_account(feat.account_tail, cur.account_tail)
    account_change = bool(
        feat.account_tail and cur.account_tail and not same_acct
    )

    if account_change:
        score += _W_ACCOUNT_CHANGE
        reason = "account_change"
    if feat.is_statement_first_page:
        score += _W_SUMMARY_BLOCK
        if reason == "weak":
            reason = "summary_block"
    if feat.is_statement_header:
        score += _W_STMT_HEADER
        if reason == "weak":
            reason = "statement_header"
    if feat.doc_type in _DOCTYPE_START_TYPES and (pending_blank or account_change):
        score += _W_DOCTYPE_START
        if reason == "weak":
            reason = f"doctype_{feat.doc_type}"
    if feat.issuer and cur.issuer and feat.issuer != cur.issuer:
        score += _W_ISSUER_CHANGE
    if pending_blank:
        score += _W_PENDING_BLANK
    if feat.period and feat.account_tail:
        score += _W_PERIOD_WITH_ACCT

    # Continuation bias: same account, no reset marker -> damp weak signals so
    # ordinary continuation pages of one statement do not start a new document.
    if same_acct and feat.page_x is None:
        score *= 0.35

    return score, reason


def _confidence_for(reason: str, score: float, threshold: float) -> float:
    fixed = {
        "marker": 0.95,
        "separator": 0.90,
        "page_1_of": 0.95,
        "summary_block": 0.90,
        "account_change": 0.85,
        "first_doc": 0.75,
    }
    if reason in fixed:
        return fixed[reason]
    # derive from score for the remaining (doctype/issuer/blank) reasons
    return round(max(0.6, min(0.97, 0.55 + 0.25 * score)), 2)


def split_pages(pages: list[tuple[int, str]]) -> list[SplitDoc]:
    """Group ``(page_index, text)`` pairs into logical documents.

    ``pages`` must be ordered by ``page_index``. Returns non-overlapping,
    contiguous documents covering all content pages; blank/separator pages
    between documents are excluded.
    """
    settings = get_settings()
    threshold = getattr(settings, "split_start_threshold", _DEFAULT_START_THRESHOLD)
    min_conf = settings.split_min_confidence

    docs: list[SplitDoc] = []
    cur: _OpenDoc | None = None
    pending_blank = False       # a plain blank page since the last content page
    pending_separator = False   # an explicit @@SEP@@/barcode: forces next start

    def close() -> None:
        nonlocal cur
        if cur is not None:
            conf = cur.confidence
            # page_y disagreement lowers confidence (possible mis-group)
            if cur.page_y and cur.n_content != cur.page_y:
                conf = min(conf, min_conf - 0.05)
            docs.append(SplitDoc(cur.start, cur.last_content, round(conf, 2)))
        cur = None

    def open_doc(feat: docparse.PageFeatures, reason: str, score: float) -> None:
        nonlocal cur
        conf = _confidence_for(reason, score, threshold)
        cur = _OpenDoc(
            start=feat.index,
            last_content=feat.index,
            account_tail=feat.account_tail,
            issuer=feat.issuer,
            page_y=feat.page_y,
            n_content=1,
            confidence=conf,
        )

    for idx, text in pages:
        feat = docparse.analyze_page(idx, text)

        if feat.is_separator_marker:
            close()
            pending_separator = True
            pending_blank = False
            continue
        if feat.is_blank:
            pending_blank = True
            continue

        # --- decide START vs CONTINUE -------------------------------------
        if cur is None:
            if feat.is_doc_marker:
                reason = "marker"
            elif pending_separator:
                reason = "separator"
            else:
                reason = "first_doc"
            open_doc(feat, reason, 1.0)
        elif feat.is_doc_marker:
            close()
            open_doc(feat, "marker", 1.0)
        elif pending_separator:
            close()
            open_doc(feat, "separator", 1.0)
        elif feat.page_x is not None and feat.page_x > 1:
            # Explicit continuation — but a changed 'of N' total or a new
            # account/issuer means a *new* document (handles reverse-ordered
            # scans where every page reads "Page k of N", k>1).
            account_change = bool(
                feat.account_tail
                and cur.account_tail
                and not docparse.same_account(feat.account_tail, cur.account_tail)
            )
            page_y_change = (
                _plausible_page_y(feat)
                and cur.page_y is not None
                and 2 <= cur.page_y <= 60
                and feat.page_y != cur.page_y
            )
            if page_y_change:
                close()
                open_doc(feat, "page_y_change", 0.8)
            elif account_change:
                close()
                open_doc(feat, "account_change", _W_ACCOUNT_CHANGE)
            else:
                _extend(cur, feat)
        else:
            score, reason = _score_start(feat, cur, pending_blank)
            if score >= threshold:
                close()
                open_doc(feat, reason, score)
            else:
                _extend(cur, feat)

        pending_separator = False
        pending_blank = False

    close()
    return docs


def _extend(cur: _OpenDoc, feat: docparse.PageFeatures) -> None:
    cur.last_content = feat.index
    cur.n_content += 1
    if cur.account_tail is None and feat.account_tail:
        cur.account_tail = feat.account_tail
    if cur.issuer is None and feat.issuer:
        cur.issuer = feat.issuer
    if cur.page_y is None and feat.page_y:
        cur.page_y = feat.page_y
