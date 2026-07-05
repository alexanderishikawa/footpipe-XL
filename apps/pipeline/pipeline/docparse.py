"""Pure OCR-text parsing: per-page features + per-document field extraction.

Shared by :mod:`split_logic` (boundary signals) and the fake/real LLM providers
(deterministic enrichment). No DB, no network, no PDF rendering — everything is
derived from OCR text so it is trivially unit-testable and offline-friendly.

The heuristics are tuned against real mailroom scans (bank/credit-card
statements, 1099 tax forms, letters, checks) whose OCR is noisy, occasionally
rotated, and sometimes out of order.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date

# --- legacy synthetic markers (fixtures) -------------------------------------
DOC_MARKER = re.compile(r"@@DOC\b", re.IGNORECASE)
DOC_MARKER_ATTRS = re.compile(r"@@DOC\s+([^@]+)@@", re.IGNORECASE)
SEPARATOR_MARKER = re.compile(
    r"(@@SEP@@|\*\*\*\s*SEPARATOR\s*\*\*\*|BARCODE:SEP|PATCH\s*[- ]?T)", re.IGNORECASE
)
ORIGINATOR_MARKER = re.compile(r"@@ORIGINATOR\s+(.+?)@@", re.IGNORECASE)
ENTITY_MARKER = re.compile(r"@@ENTITY\s+(.+?)@@", re.IGNORECASE)

# --- first-page / boundary signals -------------------------------------------
_MIN_PAYMENT = re.compile(r"minimum\s+payment\s+due", re.I)
_PAYMENT_DUE_DATE = re.compile(r"payment\s+due\s+date", re.I)
_NEW_BALANCE = re.compile(r"new\s+balance", re.I)
_STMT_PERIOD = re.compile(r"statement\s+period", re.I)
_OPENING_CLOSING = re.compile(r"(opening|closing)\s+date", re.I)
_ACCT_LABEL = re.compile(r"account\s+(?:number|ending)\s*[:.\s]", re.I)
_AMOUNT_DUE = re.compile(r"amount\s+due", re.I)

_PAGE_X_OF_Y = re.compile(r"page\s+(\d{1,3})\s+of\s+(\d{1,3})", re.I)

# account tail: after "Account Number/Ending" grab a masked/real trailing group
_ACCT_TAIL = re.compile(
    r"account\s+(?:number|ending)\s*[:.\s]*\s*([x\d][x\d\s*-]{2,})",
    re.I,
)
_CARD_ENDING = re.compile(r"\bending\s+(?:in\s+)?([x\d][\dx-]{2,})", re.I)

# document-date patterns
_MONTHS = {
    m: i
    for i, m in enumerate(
        [
            "jan", "feb", "mar", "apr", "may", "jun",
            "jul", "aug", "sep", "oct", "nov", "dec",
        ],
        start=1,
    )
}
_DATE_RANGE_TEXTUAL = re.compile(
    r"([A-Za-z]{3,9})\s+(\d{1,2}),?\s*(\d{4})\s*(?:through|thru|to|[-–])\s*"
    r"([A-Za-z]{3,9})\s+(\d{1,2}),?\s*(\d{4})",
    re.I,
)
_DATE_RANGE_NUMERIC = re.compile(
    r"(\d{1,2})/(\d{1,2})/(\d{2,4})\s*[-–]\s*(\d{1,2})/(\d{1,2})/(\d{2,4})"
)
_CLOSING_DATE = re.compile(r"closing\s+date\s*[:.]?\s*(\d{1,2})/(\d{1,2})/(\d{2,4})", re.I)
_TEXTUAL_DATE = re.compile(r"\b([A-Za-z]{3,9})\s+(\d{1,2}),?\s+(\d{4})\b")
_NUMERIC_DATE = re.compile(r"\b(\d{1,2})/(\d{1,2})/(\d{2,4})\b")
_TAX_YEAR = re.compile(r"(?:tax\s+year|for\s+calendar\s+year)\s+(\d{4})", re.I)

# amounts
_NEW_BALANCE_AMT = re.compile(r"new\s+balance[^\d\-]{0,20}\$?\s*([\d,]+\.\d{2})", re.I)
_MIN_PAYMENT_AMT = re.compile(r"minimum\s+payment\s+due[^\d\-]{0,20}\$?\s*([\d,]+\.\d{2})", re.I)
_AMOUNT_DUE_AMT = re.compile(r"amount\s+due[^\d\-]{0,20}\$?\s*([\d,]+\.\d{2})", re.I)

# doc-type cues
_CHECK_CUES = re.compile(
    r"(?:pay\s+)?to\s+the\s+order\s+of|net\s+check\s+amount|check\s+amount\b|"
    r"void\s+without|hold\s+at\s+angle|\bvoid\s+after\b",
    re.I,
)
_CHECK_GARBAGE = re.compile(r"secur[a-z]*[ ]?t?y[ ]?sy?stem|autocheck|microprint", re.I)
_TAX_CUES = re.compile(
    r"\b1099(?:-[A-Z]{1,4})?\b|\bw-?2\b|\b1098\b|\b1040\b|internal\s+revenue|"
    r"\bschedule\s+[A-K]\b|taxpayer",
    re.I,
)
_INVOICE_CUES = re.compile(r"\binvoice\b|\bbill\s+to\b|\bremit\s+to\b|\bpurchase\s+order\b", re.I)
_LETTER_CUES = re.compile(r"^\s*dear\b|\bsincerely\b|\bregards\b", re.I | re.M)
# Tightened: bare "agreement"/"contract" appear inside statements ("Cardmember
# Agreement"), so require multi-word contract phrasing.
_CONTRACT_CUES = re.compile(
    r"master\s+service\s+agreement|terms\s+and\s+conditions|\bwitnesseth\b|"
    r"in\s+witness\s+whereof|this\s+agreement\s+is\s+(?:made|entered)",
    re.I,
)
_STATEMENT_CUES = re.compile(
    r"\bstatement\b|account\s+summary|account\s+activity|account\s+balance|"
    r"\bbank\b|credit\s+union|minimum\s+payment|new\s+balance",
    re.I,
)
# generic originator fallbacks when no known issuer letterhead is present
_ORG_BANK = re.compile(r"^(.{2,60}?\b(?:bank|credit\s+union)\b)(?:\s+statement)?\s*$", re.I | re.M)
_ORG_INVOICE = re.compile(r"^(.{2,60}?)\s+invoice\b", re.I | re.M)
_ORG_SINCERELY = re.compile(r"sincerely,?\s*(.+)", re.I)

# issuer / originator letterheads (lowercase contains-match)
ISSUERS: dict[str, str] = {
    "american express": "American Express",
    "amex": "American Express",
    "chase": "Chase",
    "jpmorgan": "JPMorgan Chase",
    "citibank": "Citi",
    "citi": "Citi",
    "capital one": "Capital One",
    "wells fargo": "Wells Fargo",
    "bank of america": "Bank of America",
    "discover": "Discover",
    "synchrony": "Synchrony",
    "barclays": "Barclays",
    "u.s. bank": "U.S. Bank",
    "us bank": "U.S. Bank",
    "usaa": "USAA",
    "navy federal": "Navy Federal",
    "pnc": "PNC",
    "truist": "Truist",
    "fidelity": "Fidelity",
    "charles schwab": "Charles Schwab",
    "schwab": "Charles Schwab",
    "vanguard": "Vanguard",
    "internal revenue": "IRS",
    "irs": "IRS",
    "paypal": "PayPal",
    "venmo": "Venmo",
}

# uppercase words that disqualify an all-caps line from being a person name
_NON_NAME_TOKENS = frozenset(
    {
        "BANK", "STATEMENT", "ACCOUNT", "PAYMENT", "CHASE", "AMERICAN", "EXPRESS",
        "CITI", "CAPITAL", "ONE", "WELLS", "FARGO", "DISCOVER", "BARCLAYS",
        "VISA", "MASTERCARD", "REWARDS", "SUMMARY", "BALANCE", "CREDIT", "CARD",
        "SERVICE", "CUSTOMER", "INTEREST", "CHARGES", "TOTAL", "DATE", "PAGE",
        "PO", "BOX", "STREET", "AVE", "AVENUE", "ROAD", "DRIVE", "SUITE", "APT",
        "USA", "LLC", "INC", "CORP", "COMPANY", "DEPARTMENT", "NOTICE", "IMPORTANT",
        "PLEASE", "THANK", "YOU", "DEAR", "NEW", "MINIMUM", "DUE", "SKYMILES",
        "PLATINUM", "SAPPHIRE", "PREFERRED", "FREEDOM", "UNLIMITED", "ADVANTAGE",
        "AVIATOR", "DELTA", "UNITED", "ULTIMATE",
        # transaction/section headers that OCR renders in caps
        "RESTAURANT", "FOOD", "FAST", "PAYMENTS", "CREDITS", "TRANSFERS",
        "ORDER", "COMMISSION", "DETAIL", "DETAILS", "ACTIVITY", "PROMOTIONS",
        "ONLINE", "PHONE", "BENEFIT", "BENEFITS", "CARDMEMBER", "ENJOY",
        "FURTHER", "SOCIAL", "SECURITY", "QUALIFIED", "INTERMEDIARY",
        "DRILLING", "HORIZONTAL", "MERCHANDISE", "DIGITAL", "SERVICES",
        "PURCHASE", "PURCHASES", "CASH", "ADVANCES", "FEES", "REWARDS",
        "MESSAGES", "WARNING", "INFORMATION", "SUMMARY", "STORE", "MARKET",
        "GAS", "PHARMACY", "TRAVEL", "AIRLINES", "HOTEL", "REPORT", "RETURN",
        "WITH", "YOUR", "THE", "AND", "FOR", "OF", "TO", "IN", "ON",
    }
)
# first token >=2 chars; middle/last tokens may be single-letter initials
_NAME_LINE = re.compile(r"^[A-Z][A-Za-z.'-]+(?:\s+[A-Z][A-Za-z.'&-]*){1,3}$")

_MEANINGFUL = re.compile(r"[A-Za-z0-9]")
_WORDISH = re.compile(r"\b[A-Za-z]{3,}\b")
_VOWEL = re.compile(r"[aeiou]", re.I)


def _iso(y: int, m: int, d: int) -> str | None:
    try:
        return date(y, m, d).isoformat()
    except ValueError:
        return None


def _year4(raw: str) -> int:
    y = int(raw)
    return 2000 + y if y < 100 else y


def normalize_account_tail(raw: str | None) -> str | None:
    """Collapse whitespace/masking noise; keep the last 8 identifying chars.

    Requires >= 4 digits: short OCR fragments (e.g. "6-21") are unreliable
    boundary signals and caused false splits, so we drop them.
    """
    if not raw:
        return None
    s = re.sub(r"[\s]", "", raw).strip("-*.:")
    s = re.sub(r"[^0-9xX-]", "", s)
    if len(re.sub(r"[^0-9]", "", s)) < 4:
        return None
    return s[-8:].lower()


def account_last4(tail: str | None) -> str | None:
    """Last 4 digits of an account tail — the stable part across OCR noise."""
    if not tail:
        return None
    digits = re.sub(r"[^0-9]", "", tail)
    return digits[-4:] if len(digits) >= 4 else None


def same_account(a: str | None, b: str | None) -> bool:
    """True if two account tails plausibly refer to the same account."""
    la, lb = account_last4(a), account_last4(b)
    if la and lb:
        return la == lb
    return False


_ISSUER_PATTERNS = tuple(
    (re.compile(r"\b" + re.escape(k) + r"\b", re.I), v)
    for k, v in sorted(ISSUERS.items(), key=lambda kv: len(kv[0]), reverse=True)
)


def detect_issuer(text: str) -> str | None:
    # Word-boundary match (longest key first) so "first" does not match "irs"
    # and "bank of america" wins over shorter generic keys.
    for rx, name in _ISSUER_PATTERNS:
        if rx.search(text):
            return name
    return None


def detect_person(text: str) -> str | None:
    for ln in text.splitlines():
        s = ln.strip().strip(".,")
        if not (6 <= len(s) <= 40):
            continue
        if not _NAME_LINE.match(s):
            continue
        toks = s.split()
        if any(t.strip(".'-&") in _NON_NAME_TOKENS for t in toks):
            continue
        if any(ch.isdigit() for ch in s):
            continue
        return " ".join(w.capitalize() for w in toks)
    return None


def page_x_of_y(text: str) -> tuple[int, int] | None:
    m = _PAGE_X_OF_Y.search(text)
    if not m:
        return None
    return int(m.group(1)), int(m.group(2))


def _account_tail(text: str) -> str | None:
    m = _ACCT_TAIL.search(text) or _CARD_ENDING.search(text)
    return normalize_account_tail(m.group(1)) if m else None


def statement_period(text: str) -> tuple[str, str] | None:
    m = _DATE_RANGE_TEXTUAL.search(text)
    if m:
        m1 = _MONTHS.get(m.group(1)[:3].lower())
        m2 = _MONTHS.get(m.group(4)[:3].lower())
        if m1 and m2:
            a = _iso(int(m.group(3)), m1, int(m.group(2)))
            b = _iso(int(m.group(6)), m2, int(m.group(5)))
            if a and b:
                return a, b
    m = _DATE_RANGE_NUMERIC.search(text)
    if m:
        a = _iso(_year4(m.group(3)), int(m.group(1)), int(m.group(2)))
        b = _iso(_year4(m.group(6)), int(m.group(4)), int(m.group(5)))
        if a and b:
            return a, b
    return None


def best_document_date(text: str, doc_type: str | None = None) -> tuple[str | None, float]:
    """Prefer a statement period end / closing date; fall back to any printed date."""
    period = statement_period(text)
    if period:
        return period[1], 0.85
    m = _CLOSING_DATE.search(text)
    if m:
        iso = _iso(_year4(m.group(3)), int(m.group(1)), int(m.group(2)))
        if iso:
            return iso, 0.85
    if doc_type == "tax":
        ty = _TAX_YEAR.search(text)
        if ty:
            return f"{ty.group(1)}-12-31", 0.8
    m = _TEXTUAL_DATE.search(text)
    if m:
        mm = _MONTHS.get(m.group(1)[:3].lower())
        if mm:
            iso = _iso(int(m.group(3)), mm, int(m.group(2)))
            if iso:
                return iso, 0.6
    m = _NUMERIC_DATE.search(text)
    if m:
        iso = _iso(_year4(m.group(3)), int(m.group(1)), int(m.group(2)))
        if iso:
            return iso, 0.55
    return None, 0.0


def word_ratio(text: str) -> float:
    """Fraction of tokens that look like normal words (garbage/OCR-noise detector).

    A "normal" token has 2-15 letters and at least one vowel; long consonant
    runs (check security backgrounds OCR'd to noise) score low.
    """
    toks = text.split()
    if not toks:
        return 0.0

    def normal(tok: str) -> bool:
        letters = re.sub(r"[^A-Za-z]", "", tok)
        return 2 <= len(letters) <= 15 and bool(_VOWEL.search(letters))

    return sum(1 for t in toks if normal(t)) / len(toks)


def detect_doc_type(text: str) -> str | None:
    """Best-effort single-document type hint from content cues.

    Order matters: specific document kinds (tax, check, invoice) win over the
    broad "statement" cues, which win over the tightened contract/letter cues.
    """
    if _TAX_CUES.search(text):
        return "tax"
    if _CHECK_CUES.search(text) or _CHECK_GARBAGE.search(text):
        return "check"
    if _INVOICE_CUES.search(text):
        return "invoice"
    if _STATEMENT_CUES.search(text) or _MIN_PAYMENT.search(text) or _NEW_BALANCE.search(text):
        return "bank"
    if _CONTRACT_CUES.search(text):
        return "contract"
    if _LETTER_CUES.search(text):
        return "correspondence"
    return None


@dataclass
class PageFeatures:
    index: int
    chars: int
    is_blank: bool
    is_separator_marker: bool
    is_doc_marker: bool
    doc_marker_attrs: dict[str, str]
    page_x: int | None
    page_y: int | None
    account_tail: str | None
    issuer: str | None
    person: str | None
    period: tuple[str, str] | None
    best_date: str | None
    best_date_conf: float
    doc_type: str | None
    is_statement_first_page: bool
    is_statement_header: bool
    is_garbage: bool
    start_signals: set[str] = field(default_factory=set)


def _meaningful_chars(text: str) -> int:
    return len(_MEANINGFUL.findall(text))


def _doc_marker_attrs(text: str) -> dict[str, str]:
    m = DOC_MARKER_ATTRS.search(text)
    if not m:
        return {}
    return {mm.group(1).lower(): mm.group(2) for mm in re.finditer(r"(\w+)=([^\s@]+)", m.group(1))}


def analyze_page(index: int, text: str, *, blank_max_meaningful: int = 40) -> PageFeatures:
    text = text or ""
    meaningful = _meaningful_chars(text)
    wordish = len(_WORDISH.findall(text))
    is_sep = bool(SEPARATOR_MARKER.search(text))
    # Blank = a page with essentially no words (empty duplex backs, cover sheets).
    # Guard by meaningful count so a short-but-real page is not dropped, and a
    # garbage-but-full check page (few real words, many chars) is NOT blank.
    is_blank = (not is_sep) and wordish < 2 and meaningful < blank_max_meaningful
    pxy = page_x_of_y(text)
    acct = _account_tail(text)
    issuer = detect_issuer(text)
    period = statement_period(text)
    doc_type = detect_doc_type(text)
    best_date, best_conf = best_document_date(text, doc_type)
    ratio = word_ratio(text)
    is_garbage = (meaningful >= blank_max_meaningful) and ratio < 0.35 and not is_sep

    is_first = bool(
        _MIN_PAYMENT.search(text)
        and (_NEW_BALANCE.search(text) or _PAYMENT_DUE_DATE.search(text))
    )
    # A statement header: account label + a period/opening-closing date. Marks a
    # likely document start even when no account was seen yet (weak-run blobs).
    is_header = bool(
        _ACCT_LABEL.search(text)
        and (period or _OPENING_CLOSING.search(text) or _STMT_PERIOD.search(text))
    )

    signals: set[str] = set()
    if pxy and pxy[0] == 1:
        signals.add("page_1_of")
    if is_first:
        signals.add("summary_block")
    if _STMT_PERIOD.search(text) or period:
        signals.add("statement_period")
    if _OPENING_CLOSING.search(text):
        signals.add("opening_closing")
    if _ACCT_LABEL.search(text):
        signals.add("account_label")
    if _AMOUNT_DUE.search(text):
        signals.add("amount_due")
    if _TAX_CUES.search(text):
        signals.add("tax")
    if _INVOICE_CUES.search(text):
        signals.add("invoice")
    if _LETTER_CUES.search(text):
        signals.add("letter")
    if _CHECK_CUES.search(text) or _CHECK_GARBAGE.search(text):
        signals.add("check")

    return PageFeatures(
        index=index,
        chars=meaningful,
        is_blank=is_blank,
        is_separator_marker=is_sep,
        is_doc_marker=bool(DOC_MARKER.search(text)),
        doc_marker_attrs=_doc_marker_attrs(text),
        page_x=pxy[0] if pxy else None,
        page_y=pxy[1] if pxy else None,
        account_tail=acct,
        issuer=issuer,
        person=detect_person(text),
        period=period,
        best_date=best_date,
        best_date_conf=best_conf,
        doc_type=doc_type,
        is_statement_first_page=is_first,
        is_statement_header=is_header,
        is_garbage=is_garbage,
        start_signals=signals,
    )


# --- per-document aggregation (enrichment) -----------------------------------
@dataclass
class DocumentFields:
    category: str
    title: str
    summary: str
    originator: str | None
    originator_confidence: float
    entities: list[str]
    document_date: str | None
    document_date_confidence: float
    account_tail: str | None
    issuer: str | None
    period: tuple[str, str] | None
    doc_type: str | None
    tags: list[str]


_DOC_TYPE_TO_CATEGORY = {
    "bank": "bank",
    "credit_card": "bank",
    "tax": "tax",
    "check": "check",
    "invoice": "invoice",
    "contract": "contract",
    "correspondence": "correspondence",
}


def _first_content_line(text: str) -> str:
    for ln in text.splitlines():
        s = ln.strip()
        if not s:
            continue
        if DOC_MARKER.search(s) or SEPARATOR_MARKER.search(s):
            continue
        if _MEANINGFUL.search(s):
            return s
    return ""


def document_fields(pages_text: list[str], categories: list[str] | None = None) -> DocumentFields:
    """Aggregate a page-range's OCR text into enrichment fields (deterministic)."""
    joined = "\n".join(pages_text)
    feats = [analyze_page(i, t) for i, t in enumerate(pages_text)]

    # explicit fixture markers win (keeps synthetic fixtures deterministic)
    marker_attrs: dict[str, str] = {}
    for f in feats:
        if f.doc_marker_attrs:
            marker_attrs = f.doc_marker_attrs
            break

    issuer = next((f.issuer for f in feats if f.issuer), None)
    account_tail = next((f.account_tail for f in feats if f.account_tail), None)
    period = next((f.period for f in feats if f.period), None)
    doc_type = next((f.doc_type for f in feats if f.doc_type), None)

    category = "other"
    if marker_attrs.get("category"):
        category = marker_attrs["category"].lower()
    elif doc_type:
        category = _DOC_TYPE_TO_CATEGORY.get(doc_type, "other")
    if categories and category not in categories:
        category = "other"

    # document date: prefer per-page best (period end / closing) with highest conf
    best_date, best_conf = None, 0.0
    for f in feats:
        if f.best_date and f.best_date_conf > best_conf:
            best_date, best_conf = f.best_date, f.best_date_conf
    if marker_attrs.get("date") and re.fullmatch(r"\d{4}-\d{2}-\d{2}", marker_attrs["date"]):
        best_date, best_conf = marker_attrs["date"], 0.97

    first_line = _first_content_line(joined)

    # originator: explicit marker → known issuer → generic "X Bank"/vendor → IRS
    originator, originator_conf = None, 0.0
    om = ORIGINATOR_MARKER.search(joined)
    if om:
        originator, originator_conf = om.group(1).strip()[:256], 0.97
    elif issuer:
        originator, originator_conf = issuer, 0.85
    else:
        gb = _ORG_BANK.search(joined)
        gi = _ORG_INVOICE.match(first_line)
        gs = _ORG_SINCERELY.search(joined)
        if gb:
            originator, originator_conf = gb.group(1).strip()[:256], 0.8
        elif category == "invoice" and gi:
            originator, originator_conf = gi.group(1).strip()[:256], 0.8
        elif category == "tax":
            originator, originator_conf = "IRS", 0.7
        elif category == "correspondence" and gs:
            originator, originator_conf = gs.group(1).strip()[:256], 0.75

    # entities: explicit markers → detected person names
    entities: list[str] = [m.group(1).strip()[:256] for m in ENTITY_MARKER.finditer(joined)]
    if not entities:
        seen: set[str] = set()
        for f in feats:
            if f.person and f.person.lower() not in seen:
                seen.add(f.person.lower())
                entities.append(f.person)
            if len(entities) >= 5:
                break

    title = _build_title(category, issuer, period, first_line)
    summary = _build_summary(joined, category, issuer, account_tail, period)

    tags = [category]
    if issuer:
        tags.append(issuer.lower())
    if doc_type and doc_type != category:
        tags.append(doc_type)
    if period:
        tags.append(f"period:{period[0]}/{period[1]}")
    tags.append("scanned")

    return DocumentFields(
        category=category,
        title=title[:120],
        summary=summary[:400],
        originator=originator,
        originator_confidence=originator_conf,
        entities=entities,
        document_date=best_date,
        document_date_confidence=best_conf,
        account_tail=account_tail,
        issuer=issuer,
        period=period,
        doc_type=doc_type,
        tags=tags,
    )


def _build_title(
    category: str, issuer: str | None, period: tuple[str, str] | None, first_line: str
) -> str:
    # A structured title is only better than the raw first line when we actually
    # recognized an issuer or a statement period; otherwise keep the first line
    # (also preserves legacy marker-fixture titles).
    if issuer or period:
        parts: list[str] = []
        if issuer:
            parts.append(issuer)
        label = {
            "bank": "Statement",
            "tax": "Tax Document",
            "check": "Check",
            "invoice": "Invoice",
            "contract": "Agreement",
            "correspondence": "Letter",
        }.get(category)
        if label:
            parts.append(label)
        if period:
            parts.append(period[1])
        if parts:
            return " — ".join(parts) if len(parts) > 1 else parts[0]
    return first_line[:120] if first_line else "Untitled document"


def _build_summary(
    joined: str,
    category: str,
    issuer: str | None,
    account_tail: str | None,
    period: tuple[str, str] | None,
) -> str:
    bits: list[str] = []
    if issuer:
        bits.append(issuer)
    if category != "other":
        bits.append(category)
    if account_tail:
        bits.append(f"account …{account_tail[-4:]}")
    if period:
        bits.append(f"{period[0]} to {period[1]}")
    for pat, label in ((_NEW_BALANCE_AMT, "new balance $"), (_AMOUNT_DUE_AMT, "amount due $")):
        m = pat.search(joined)
        if m:
            bits.append(f"{label}{m.group(1)}")
            break
    if bits:
        return "; ".join(bits)
    lines = [ln.strip() for ln in joined.splitlines() if ln.strip()]
    return " ".join(lines[:3])[:400] if lines else "(no extractable text)"
