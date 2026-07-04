from pipeline.split_logic import split_pages


def test_marker_splits_into_documents():
    pages = [
        (0, "@@DOC category=invoice@@ ACME invoice"),
        (1, "line items continued"),
        (2, "@@DOC category=contract@@ agreement"),
    ]
    docs = split_pages(pages)
    assert [(d.page_start, d.page_end) for d in docs] == [(0, 1), (2, 2)]
    assert all(d.confidence >= 0.6 for d in docs)


def test_blank_separator_is_dropped():
    pages = [
        (0, "@@DOC category=bank@@ statement"),
        (1, "   "),  # blank page
        (2, "IRS Form 1099"),
    ]
    docs = split_pages(pages)
    assert [(d.page_start, d.page_end) for d in docs] == [(0, 0), (2, 2)]


def test_barcode_separator_is_dropped():
    pages = [
        (0, "@@DOC category=bank@@ statement"),
        (1, "@@SEP@@"),
        (2, "next document text"),
    ]
    docs = split_pages(pages)
    assert [(d.page_start, d.page_end) for d in docs] == [(0, 0), (2, 2)]
    # doc started right after a separator carries the separator-signal confidence
    assert docs[1].confidence == 0.9


def test_no_signals_prefers_under_split():
    pages = [(0, "page one"), (1, "page two"), (2, "page three")]
    docs = split_pages(pages)
    assert len(docs) == 1
    assert (docs[0].page_start, docs[0].page_end) == (0, 2)


def test_leading_separator_produces_no_empty_doc():
    pages = [(0, "@@SEP@@"), (1, "real content")]
    docs = split_pages(pages)
    assert [(d.page_start, d.page_end) for d in docs] == [(1, 1)]
