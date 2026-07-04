"""Unit tests for commit.archive metadata sync integration (v1.1 task-7)."""

from __future__ import annotations

import uuid
from datetime import date
from unittest.mock import MagicMock, patch

from pipeline.jobs import _build_sync_input, commit_archive
from pipeline.models import Artifact, Batch, Document
from pipeline.queue import JobMessage


def _enrich_meta(**overrides) -> dict:
    base = {
        "title": "Invoice",
        "category": "invoice",
        "confidence": 0.9,
        "document_date": "2024-03-15",
        "document_date_confidence": 0.9,
        "document_date_sync_eligible": True,
        "document_date_rejected_future": False,
        "originator": "ACME Supplies",
        "originator_confidence": 0.85,
        "originator_sync_eligible": True,
        "entities": [],
        "tag_overflow": False,
    }
    base.update(overrides)
    return base


def _doc(**kwargs) -> Document:
    defaults = {
        "batch_id": uuid.uuid4(),
        "page_start": 0,
        "page_end": 0,
        "title": "ACME Invoice",
        "category": "invoice",
        "tags": ["invoice", "paid"],
        "document_date": date(2024, 3, 15),
        "originator": "ACME Supplies",
        "metadata_json": {"enrich": _enrich_meta(), "sync": None},
    }
    defaults.update(kwargs)
    return Document(**defaults)


def test_build_sync_input_uses_enrich_eligibility_flags():
    doc = _doc()
    sync_input = _build_sync_input(doc)

    assert sync_input["category"] == "invoice"
    assert sync_input["tags"] == ["invoice", "paid"]
    assert sync_input["document_date"] == "2024-03-15"
    assert sync_input["originator"] == "ACME Supplies"
    assert sync_input["document_date_sync_eligible"] is True
    assert sync_input["originator_sync_eligible"] is True
    assert sync_input["document_date_rejected_future"] is False


def test_build_sync_input_falls_back_to_document_date_column():
    doc = _doc(
        metadata_json={"enrich": {"document_date_sync_eligible": True}},
        document_date=date(2024, 6, 1),
    )
    sync_input = _build_sync_input(doc)
    assert sync_input["document_date"] == "2024-06-01"


def _commit_session(doc: Document, batch: Batch):
    session = MagicMock()
    session.get.side_effect = lambda model, pk: {
        doc.id: doc,
        batch.id: batch,
    }.get(pk)
    session.scalar.return_value = Artifact(
        batch_id=batch.id,
        kind="original",
        uri="s3://bucket/original/x.pdf",
        checksum="abc",
    )
    session.scalars.return_value.all.return_value = [doc]
    return session


@patch("pipeline.jobs.S3ObjectStore")
@patch("pipeline.jobs.get_archive_provider")
@patch("pipeline.jobs.extract_pages", return_value=b"%PDF")
def test_commit_archive_syncs_after_upsert(mock_extract, mock_get_archive, mock_store_cls):
    batch_id = uuid.uuid4()
    doc_id = uuid.uuid4()
    doc = _doc(id=doc_id, batch_id=batch_id, paperless_id=None)
    batch = Batch(id=batch_id, source_uri="s3://b/", page_count=1, status="enrich")

    mock_store = MagicMock()
    mock_store_cls.return_value = mock_store
    mock_store.get.return_value = b"%PDF-original"

    mock_archive = MagicMock()
    mock_archive.upsert_document.return_value = 42
    mock_archive.sync_metadata.return_value = {
        "ok": True,
        "partial": False,
        "tag_ids": [10, 11],
        "correspondent_id": 5,
        "document_type_id": 3,
        "content_date_field_id": 7,
        "content_date": "2024-03-15",
        "errors": None,
    }
    mock_get_archive.return_value = mock_archive

    session = _commit_session(doc, batch)
    msg = JobMessage(type="commit.archive", entity_id=str(doc_id), batch_id=str(batch_id))

    next_msgs = commit_archive(session, msg)

    mock_archive.upsert_document.assert_called_once()
    mock_archive.sync_metadata.assert_called_once_with(42, _build_sync_input(doc))
    assert doc.paperless_id == 42
    assert doc.metadata_json["sync"]["ok"] is True
    assert doc.metadata_json["sync"]["tag_ids"] == [10, 11]
    assert len(next_msgs) == 1
    assert next_msgs[0].type == "batch.finalize"


@patch("pipeline.jobs.get_archive_provider")
def test_commit_archive_resyncs_when_paperless_id_already_set(mock_get_archive):
    """Force retry: skip upload, still sync metadata in place."""
    batch_id = uuid.uuid4()
    doc_id = uuid.uuid4()
    doc = _doc(
        id=doc_id,
        batch_id=batch_id,
        paperless_id=99,
        needs_review=False,
    )
    batch = Batch(id=batch_id, source_uri="s3://b/", page_count=1)

    mock_archive = MagicMock()
    mock_archive.sync_metadata.return_value = {
        "ok": True,
        "partial": False,
        "tag_ids": [1],
        "correspondent_id": None,
        "document_type_id": 2,
        "content_date_field_id": None,
        "content_date": None,
        "errors": None,
    }
    mock_get_archive.return_value = mock_archive

    session = _commit_session(doc, batch)
    msg = JobMessage(type="commit.archive", entity_id=str(doc_id), batch_id=str(batch_id))

    with patch("pipeline.jobs.S3ObjectStore") as mock_store_cls:
        next_msgs = commit_archive(session, msg)
        mock_store_cls.assert_not_called()

    mock_archive.upsert_document.assert_not_called()
    mock_archive.sync_metadata.assert_called_once_with(99, _build_sync_input(doc))
    assert doc.metadata_json["sync"]["ok"] is True
    assert len(next_msgs) == 1


@patch("pipeline.jobs.S3ObjectStore")
@patch("pipeline.jobs.get_archive_provider")
@patch("pipeline.jobs.extract_pages", return_value=b"%PDF")
def test_commit_archive_sync_failure_sets_needs_review_but_completes(
    mock_extract, mock_get_archive, mock_store_cls
):
    batch_id = uuid.uuid4()
    doc_id = uuid.uuid4()
    doc = _doc(id=doc_id, batch_id=batch_id, paperless_id=None, needs_review=False)
    batch = Batch(id=batch_id, source_uri="s3://b/", page_count=1)

    mock_store_cls.return_value.get.return_value = b"%PDF"
    mock_store = mock_store_cls.return_value

    mock_archive = MagicMock()
    mock_archive.upsert_document.return_value = 55
    mock_archive.sync_metadata.return_value = {
        "ok": False,
        "partial": True,
        "tag_ids": [1],
        "correspondent_id": None,
        "document_type_id": None,
        "content_date_field_id": None,
        "content_date": None,
        "errors": ["correspondent:ACME Supplies"],
    }
    mock_get_archive.return_value = mock_archive

    session = _commit_session(doc, batch)
    msg = JobMessage(type="commit.archive", entity_id=str(doc_id), batch_id=str(batch_id))

    next_msgs = commit_archive(session, msg)

    assert doc.paperless_id == 55
    assert doc.needs_review is True
    assert doc.metadata_json["sync"]["partial"] is True
    assert len(next_msgs) == 1
    assert next_msgs[0].type == "batch.finalize"
    mock_store.put.assert_called_once()


@patch("pipeline.jobs.S3ObjectStore")
@patch("pipeline.jobs.get_archive_provider")
@patch("pipeline.jobs.extract_pages", return_value=b"%PDF")
def test_commit_archive_sync_exception_sets_needs_review(
    mock_extract, mock_get_archive, mock_store_cls
):
    batch_id = uuid.uuid4()
    doc_id = uuid.uuid4()
    doc = _doc(id=doc_id, batch_id=batch_id, paperless_id=None)
    batch = Batch(id=batch_id, source_uri="s3://b/", page_count=1)

    mock_store_cls.return_value.get.return_value = b"%PDF"
    mock_archive = MagicMock()
    mock_archive.upsert_document.return_value = 77
    mock_archive.sync_metadata.side_effect = RuntimeError("paperless down")
    mock_get_archive.return_value = mock_archive

    session = _commit_session(doc, batch)
    msg = JobMessage(type="commit.archive", entity_id=str(doc_id), batch_id=str(batch_id))

    next_msgs = commit_archive(session, msg)

    assert doc.paperless_id == 77
    assert doc.needs_review is True
    assert doc.metadata_json["sync"]["ok"] is False
    assert doc.metadata_json["sync"]["errors"] == ["sync_metadata_exception"]
    assert len(next_msgs) == 1
