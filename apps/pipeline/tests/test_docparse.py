"""Unit tests for pipeline.docparse (synthetic, PII-free)."""

from pipeline import docparse


def test_normalize_account_tail_requires_four_digits():
    assert docparse.normalize_account_tail("XXXX XXXX XXXX 1234").endswith("1234")
    assert docparse.normalize_account_tail("6-21") is None  # too few digits
    assert docparse.normalize_account_tail("") is None
    assert docparse.normalize_account_tail(None) is None


def test_account_last4_and_same_account():
    assert docparse.account_last4("xxxx1234") == "1234"
    assert docparse.same_account("xxxx xxxx 1234", "acct 001234") is True
    assert docparse.same_account("1234", "5678") is False
    assert docparse.same_account(None, "1234") is False


def test_detect_issuer_word_boundary():
    # "first" must NOT match the "irs" issuer key
    assert docparse.detect_issuer("First National Bank Statement") is None
    assert docparse.detect_issuer("Chase Sapphire Preferred") == "Chase"
    assert docparse.detect_issuer("BANK OF AMERICA statement") == "Bank of America"
    assert docparse.detect_issuer("Internal Revenue Service") == "IRS"


def test_page_x_of_y():
    assert docparse.page_x_of_y("... Page 1 of 5 ...") == (1, 5)
    assert docparse.page_x_of_y("Page 12 of 23") == (12, 23)
    assert docparse.page_x_of_y("no marker here") is None


def test_statement_period_textual_and_numeric():
    assert docparse.statement_period("July 01, 2025 through July 31, 2025") == (
        "2025-07-01",
        "2025-07-31",
    )
    assert docparse.statement_period("Statement Period 01/12/25 - 02/11/25") == (
        "2025-01-12",
        "2025-02-11",
    )
    assert docparse.statement_period("no dates") is None


def test_best_document_date_prefers_period_end():
    d, conf = docparse.best_document_date("Cycle July 01, 2025 through July 31, 2025")
    assert d == "2025-07-31"
    assert conf >= 0.8
    d2, conf2 = docparse.best_document_date("IRS Form 1099-MISC Tax year 2024", "tax")
    assert d2 == "2024-12-31"
    assert conf2 >= 0.8


def test_detect_doc_type():
    assert docparse.detect_doc_type("IRS Form 1099-MISC for 2024") == "tax"
    assert docparse.detect_doc_type("PAY TO THE ORDER OF John\nNET CHECK AMOUNT $5.00") == "check"
    assert docparse.detect_doc_type("INVOICE #1001\nBill To: Foo") == "invoice"
    assert docparse.detect_doc_type("New Balance $10\nMinimum Payment Due $2") == "bank"
    assert docparse.detect_doc_type("This Master Service Agreement is made") == "contract"
    assert docparse.detect_doc_type("Dear Sir,\nthank you.\nSincerely, Bob") == "correspondence"
    assert docparse.detect_doc_type("random unclassifiable words here") is None


def test_cardmember_agreement_is_not_contract():
    # "Cardmember Agreement" inside a statement must stay bank, not contract
    text = "Chase Statement\nNew Balance $10\nsee your Cardmember Agreement for details"
    assert docparse.detect_doc_type(text) == "bank"


def test_detect_person_rejects_section_headers():
    assert docparse.detect_person("PAMELA M CRAIG") == "Pamela M Craig"
    assert docparse.detect_person("PAYMENTS AND OTHER CREDITS") is None
    assert docparse.detect_person("FAST FOOD RESTAURANT") is None
    assert docparse.detect_person("123 MAIN STREET") is None


def test_garbage_page_detected():
    garbage = "OCFVNTRYRYOFEMBNTATCHECROC BECUNTYBYSTEM ZFS SES XCF ZFS " * 4
    feat = docparse.analyze_page(0, garbage)
    assert feat.is_garbage is True
    assert feat.is_blank is False


def test_document_fields_on_synthetic_statement():
    text = (
        "CHASE\n"
        "July 01, 2025 through July 31, 2025\n"
        "Account Number: 000000123456789\n"
        "New Balance $1,234.56\n"
        "Minimum Payment Due $25.00\n"
        "PAMELA M CRAIG\n"
    )
    fields = docparse.document_fields([text], ["bank", "other"])
    assert fields.category == "bank"
    assert fields.issuer == "Chase"
    assert fields.document_date == "2025-07-31"
    assert fields.account_tail and fields.account_tail.endswith("6789")
    assert "bank" in fields.tags and "chase" in fields.tags
    assert fields.originator == "Chase"
    assert "Pamela M Craig" in fields.entities
