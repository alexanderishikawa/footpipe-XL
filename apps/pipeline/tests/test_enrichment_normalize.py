"""Unit tests for Enrichment tag normalization (v1.1 task-3)."""

from __future__ import annotations

from pipeline.config import load_category_entries, paperless_type_for_slug
from pipeline.providers.base import (
    entity_tag,
    normalize_enrichment_tags,
    normalize_tag,
)


def test_enrichment_dataclass_has_v11_fields():
    from pipeline.providers.base import Enrichment

    enr = Enrichment(
        title="T",
        summary="S",
        category="invoice",
        document_date="2024-03-15",
        document_date_confidence=0.9,
        originator="Acme Corp",
        originator_confidence=0.85,
        entities=["Jane Doe"],
    )
    assert enr.document_date == "2024-03-15"
    assert enr.document_date_confidence == 0.9
    assert enr.originator == "Acme Corp"
    assert enr.originator_confidence == 0.85
    assert enr.entities == ["Jane Doe"]


def test_entity_tag_prefix_and_slugify():
    assert entity_tag("John Dinglebarre") == "entity:john-dinglebarre"
    assert entity_tag("  ACME Corp.  ") == "entity:acme-corp"
    assert entity_tag("").startswith("entity:")


def test_normalize_tag_lowercase_trim_and_cap():
    assert normalize_tag("  Vendor:ACME  ") == "vendor:acme"
    assert len(normalize_tag("x" * 100)) == 64


def test_normalize_includes_category_and_entity_tags():
    result = normalize_enrichment_tags(
        category="invoice",
        tags=["vendor:acme", "paid"],
        entities=["John Dinglebarre"],
    )
    assert result.needs_review_overflow is False
    assert result.tags[0] == "invoice"
    assert "vendor:acme" in result.tags
    assert "paid" in result.tags
    assert "entity:john-dinglebarre" in result.tags


def test_normalize_dedupes_category_and_entities():
    result = normalize_enrichment_tags(
        category="bank",
        tags=["bank", "statement"],
        entities=["Chase Bank"],
    )
    assert result.tags.count("bank") == 1
    assert "entity:chase-bank" in result.tags


def test_normalize_overflow_sets_needs_review_flag():
    many = [f"tag-{i}" for i in range(25)]
    result = normalize_enrichment_tags(
        category="other",
        tags=many,
        entities=[],
        max_tags=20,
    )
    assert result.needs_review_overflow is True
    assert len(result.tags) == 20
    assert result.tags[0] == "other"


def test_categories_yaml_has_paperless_type_per_slug():
    entries = load_category_entries()
    assert len(entries) >= 7
    slugs = {e.slug for e in entries}
    assert "invoice" in slugs
    assert "other" in slugs
    for entry in entries:
        assert entry.paperless_type
    assert paperless_type_for_slug("invoice") == "Invoice"
    assert paperless_type_for_slug("bank") == "Bank Statement"
    assert paperless_type_for_slug("missing") is None
