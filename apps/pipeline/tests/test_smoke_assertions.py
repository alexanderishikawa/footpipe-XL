"""Unit tests for smoke tag/metadata assertion helpers (no Docker)."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from pipeline.providers.paperless import PaperlessError
from pipeline.schemas import metadata_synced_from_json
from pipeline.smoke import doc_has_tags_any_of, resolve_tags_for_assertion


def test_doc_has_tags_any_of_match():
    assert doc_has_tags_any_of(["invoice", "scanned"], ["invoice", "other"]) is True
    assert doc_has_tags_any_of(["scanned"], ["invoice", "scanned"]) is True


def test_doc_has_tags_any_of_no_match():
    assert doc_has_tags_any_of(["contract"], ["invoice", "scanned"]) is False


def test_doc_has_tags_any_of_empty_allowed_passes():
    assert doc_has_tags_any_of(["invoice"], []) is True
    assert doc_has_tags_any_of([], []) is True


def test_resolve_tags_prefers_paperless():
    paperless = MagicMock()
    paperless.get_document_tags.return_value = ["bank", "scanned"]
    tags, source = resolve_tags_for_assertion(paperless, 10, ["tax"])
    assert tags == ["bank", "scanned"]
    assert source == "paperless"
    paperless.get_document_tags.assert_called_once_with(10)


def test_resolve_tags_falls_back_to_postgres_on_paperless_error():
    paperless = MagicMock()
    paperless.get_document_tags.side_effect = PaperlessError("offline")
    tags, source = resolve_tags_for_assertion(paperless, 10, ["tax", "scanned"])
    assert tags == ["tax", "scanned"]
    assert source == "postgres"


def test_resolve_tags_falls_back_when_no_paperless_id():
    paperless = MagicMock()
    tags, source = resolve_tags_for_assertion(paperless, None, ["invoice"])
    assert tags == ["invoice"]
    assert source == "postgres"
    paperless.get_document_tags.assert_not_called()


@pytest.mark.parametrize(
    ("metadata_json", "expected"),
    [
        ({"sync": {"ok": True}}, True),
        ({"sync": {"ok": False}}, False),
        ({"sync": {"partial": True}}, False),
        ({}, False),
    ],
)
def test_metadata_synced_from_json_smoke_cases(metadata_json, expected):
    assert metadata_synced_from_json(metadata_json) is expected
