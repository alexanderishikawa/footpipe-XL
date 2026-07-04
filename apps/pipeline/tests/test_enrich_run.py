"""Unit tests for enrich.run field mapping (v1.1 task-5)."""

from __future__ import annotations

import uuid
from datetime import date
from unittest.mock import MagicMock, patch

from pipeline.config import Settings
from pipeline.jobs import (
    _apply_enrichment_failure,
    _apply_enrichment_to_document,
    enrich_run,
)
from pipeline.models import Batch, Document, Page
from pipeline.providers.base import Enrichment
from pipeline.queue import JobMessage


def _settings(**overrides) -> Settings:
    return Settings(**overrides)


def _doc(**kwargs) -> Document:
    return Document(
        batch_id=uuid.uuid4(),
        page_start=0,
        page_end=0,
        tags=[],
        **kwargs,
    )


def test_apply_enrichment_persists_extended_fields():
    doc = _doc()
    enr = Enrichment(
        title="ACME Invoice",
        summary="Invoice summary",
        category="invoice",
        tags=["invoice", "paid"],
        confidence=0.92,
        document_date="2024-03-15",
        document_date_confidence=0.9,
        originator="ACME Supplies",
        originator_confidence=0.85,
        entities=["Foo LLC"],
    )
    _apply_enrichment_to_document(doc, enr, settings=_settings(), doc_text="invoice body")

    assert doc.title == "ACME Invoice"
    assert doc.document_date == date(2024, 3, 15)
    assert doc.originator == "ACME Supplies"
    assert doc.entities == ["Foo LLC"]
    assert doc.metadata_json is not None
    assert doc.metadata_json["sync"] is None
    enrich = doc.metadata_json["enrich"]
    assert enrich["document_date"] == "2024-03-15"
    assert enrich["document_date_sync_eligible"] is True
    assert enrich["originator_sync_eligible"] is True
    assert enrich["entities"] == ["Foo LLC"]
    assert enrich["tag_overflow"] is False
    assert doc.needs_review is False


def test_apply_enrichment_skips_originator_below_threshold():
    doc = _doc()
    enr = Enrichment(
        title="T",
        summary="S",
        category="other",
        confidence=0.9,
        originator="Maybe Sender",
        originator_confidence=0.4,
    )
    _apply_enrichment_to_document(doc, enr, settings=_settings(), doc_text="text")

    assert doc.originator is None
    assert doc.metadata_json["enrich"]["originator_sync_eligible"] is False
    assert doc.needs_review is True


def test_apply_enrichment_future_date_flags_review_and_blocks_sync():
    doc = _doc()
    enr = Enrichment(
        title="Future",
        summary="S",
        category="invoice",
        tags=["invoice"],
        confidence=0.95,
        document_date="2099-06-01",
        document_date_confidence=0.99,
    )
    _apply_enrichment_to_document(doc, enr, settings=_settings(), doc_text="invoice")

    assert doc.document_date == date(2099, 6, 1)
    enrich = doc.metadata_json["enrich"]
    assert enrich["document_date_rejected_future"] is True
    assert enrich["document_date_sync_eligible"] is False
    assert doc.needs_review is True


def test_apply_enrichment_low_date_confidence_needs_review():
    doc = _doc()
    enr = Enrichment(
        title="T",
        summary="S",
        category="bank",
        confidence=0.9,
        document_date="2020-01-01",
        document_date_confidence=0.5,
    )
    _apply_enrichment_to_document(
        doc, enr, settings=_settings(metadata_date_min_conf=0.7), doc_text="bank"
    )

    assert doc.document_date == date(2020, 1, 1)
    assert doc.metadata_json["enrich"]["document_date_sync_eligible"] is False
    assert doc.needs_review is True


def test_apply_enrichment_tag_overflow_needs_review():
    doc = _doc()
    enr = Enrichment(
        title="T",
        summary="S",
        category="other",
        confidence=0.9,
        tags=["other"] + [f"tag-{i}" for i in range(19)],
        tags_overflow=True,
    )
    _apply_enrichment_to_document(doc, enr, settings=_settings(), doc_text="text")

    assert doc.metadata_json["enrich"]["tag_overflow"] is True
    assert doc.needs_review is True


def test_apply_enrichment_preserves_existing_sync_metadata():
    doc = _doc(metadata_json={"sync": {"ok": True, "tag_ids": [1]}})
    enr = Enrichment(title="T", summary="S", category="other", confidence=0.9)
    _apply_enrichment_to_document(doc, enr, settings=_settings(), doc_text="text")

    assert doc.metadata_json["sync"] == {"ok": True, "tag_ids": [1]}
    assert "enrich" in doc.metadata_json


def test_apply_enrichment_failure_sets_error_metadata():
    doc = _doc()
    _apply_enrichment_failure(doc, doc_text="line one\nline two")

    assert doc.category == "other"
    assert doc.tags == ["other"]
    assert doc.document_date is None
    assert doc.originator is None
    assert doc.entities == []
    assert doc.metadata_json["enrich"] == {"error": "llm_failure"}
    assert doc.metadata_json["sync"] is None
    assert doc.needs_review is True


def test_enrich_run_maps_fake_llm_via_session():
    batch_id = uuid.uuid4()
    doc_id = uuid.uuid4()
    doc = Document(
        id=doc_id,
        batch_id=batch_id,
        page_start=0,
        page_end=0,
        tags=[],
    )
    batch = Batch(id=batch_id, source_uri="s3://b/", page_count=1, status="split")
    page = Page(
        batch_id=batch_id,
        page_index=0,
        text="@@DOC category=invoice date=2024-03-15@@\nInvoice",
    )

    session = MagicMock()
    session.get.side_effect = lambda model, pk: {
        doc_id: doc,
        batch_id: batch,
    }.get(pk)
    session.scalars.return_value.all.return_value = [page]

    msg = JobMessage(type="enrich.run", entity_id=str(doc_id), batch_id=str(batch_id))
    with patch("pipeline.jobs.get_llm_provider") as mock_llm:
        from pipeline.providers.fake_llm import FakeLlmProvider

        mock_llm.return_value = FakeLlmProvider()
        next_msgs = enrich_run(session, msg)

    assert len(next_msgs) == 1
    assert next_msgs[0].type == "commit.archive"
    assert doc.document_date == date(2024, 3, 15)
    assert doc.metadata_json is not None
    assert "enrich" in doc.metadata_json
    assert "error" not in doc.metadata_json["enrich"]


def test_enrich_run_llm_failure_sets_error_metadata():
    batch_id = uuid.uuid4()
    doc_id = uuid.uuid4()
    doc = Document(id=doc_id, batch_id=batch_id, page_start=0, page_end=0, tags=[])
    batch = Batch(id=batch_id, source_uri="s3://b/", page_count=1)

    session = MagicMock()
    session.get.side_effect = lambda model, pk: {doc_id: doc, batch_id: batch}.get(pk)
    session.scalars.return_value.all.return_value = [
        Page(batch_id=batch_id, page_index=0, text="some ocr text")
    ]

    mock_provider = MagicMock()
    mock_provider.enrich.side_effect = RuntimeError("openai down")

    msg = JobMessage(type="enrich.run", entity_id=str(doc_id), batch_id=str(batch_id))
    with patch("pipeline.jobs.get_llm_provider", return_value=mock_provider):
        enrich_run(session, msg)

    assert doc.metadata_json["enrich"]["error"] == "llm_failure"
    assert doc.category == "other"
    assert doc.needs_review is True
