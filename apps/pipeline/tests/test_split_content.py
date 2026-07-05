"""Content-aware split tests on synthetic, PII-free multi-document bundles.

These model the real-scan signal patterns (statement first pages, account
changes, blank separators, reverse-ordered 'Page X of Y') without any real PII.
"""

from pipeline.split_logic import split_pages


def _ranges(pages):
    return [(d.page_start, d.page_end) for d in split_pages(pages)]


def test_page_one_of_n_resets_split_two_statements():
    pages = [
        (0, "ACME BANK\nAccount Number: XXXX XXXX 1111\nNew Balance $100.00\n"
             "Minimum Payment Due $25.00\nPayment Due Date 01/15/25\nPage 1 of 2"),
        (1, "ACME BANK\nAccount Number 1111\ntransaction detail\nPage 2 of 2"),
        (2, "BETA CARD\nAccount Number: XXXX XXXX 2222\nNew Balance $200.00\n"
             "Minimum Payment Due $50.00\nPayment Due Date 02/15/25\nPage 1 of 2"),
        (3, "BETA CARD\nAccount 2222\ntransactions\nPage 2 of 2"),
    ]
    assert _ranges(pages) == [(0, 1), (2, 3)]


def test_account_change_splits_without_page_markers():
    pages = [
        (0, "Account Number: 5555\nStatement of account\nbalance summary"),
        (1, "continued detail for the same account 5555"),
        (2, "Account Number: 6666\nNew Balance $5.00\nMinimum Payment Due $1.00"),
    ]
    assert _ranges(pages) == [(0, 1), (2, 2)]


def test_blank_then_summary_block_starts_new_doc():
    pages = [
        (0, "Some bank account balance narrative page one"),
        (1, "   "),  # blank separator
        (2, "OMEGA BANK\nAccount Number: 7777\nNew Balance $9.00\n"
             "Minimum Payment Due $1.00\nPayment Due Date 03/01/25"),
    ]
    # blank page dropped; second doc starts at page 2
    assert _ranges(pages) == [(0, 0), (2, 2)]


def test_page_y_change_splits_reverse_ordered_scan():
    pages = [
        (0, "alpha document Page 2 of 4"),
        (1, "alpha document Page 3 of 4"),
        (2, "beta document Page 2 of 6"),   # denominator 4 -> 6 => new document
        (3, "beta document Page 3 of 6"),
    ]
    assert _ranges(pages) == [(0, 1), (2, 3)]


def test_same_account_continuation_not_oversplit():
    pages = [
        (0, "CARD\nAccount Number: 8888\nNew Balance $10.00\n"
             "Minimum Payment Due $2.00\nPayment Due Date 01/01/25\nPage 1 of 3"),
        (1, "CARD Account 8888 New Balance Minimum Payment Due Page 2 of 3"),
        (2, "CARD Account 8888 more detail Page 3 of 3"),
    ]
    # summary words on page 2 must NOT start a new doc (page 2 of 3, same account)
    assert _ranges(pages) == [(0, 2)]


def test_checks_separated_by_blank():
    pages = [
        (0, "PAY TO THE ORDER OF John Q\nCHECK NUMBER 1001\n"
            "NET CHECK AMOUNT $500.00\nVOID WITHOUT"),
        (1, "   "),
        (2, "PAY TO THE ORDER OF Jane R\nCHECK NUMBER 1002\n"
            "NET CHECK AMOUNT $250.00\nVOID WITHOUT"),
    ]
    assert _ranges(pages) == [(0, 0), (2, 2)]


def test_single_statement_stays_one_doc():
    pages = [
        (0, "GAMMA CU\nStatement Period 05/01/25 - 05/31/25\nAccount Number: 4321\nPage 1 of 3"),
        (1, "GAMMA CU Account 4321 detail Page 2 of 3"),
        (2, "GAMMA CU Account 4321 detail Page 3 of 3"),
    ]
    assert _ranges(pages) == [(0, 2)]


def test_low_confidence_flags_are_below_threshold():
    # a weak, sign-less multi-page scan stays one doc (under-split preference)
    pages = [(0, "page one text"), (1, "page two text"), (2, "page three text")]
    docs = split_pages(pages)
    assert len(docs) == 1
