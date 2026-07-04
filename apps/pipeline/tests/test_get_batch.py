"""API contract tests for GET /batches/{id} v1.1 document fields (task-8)."""

from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from pipeline.api import app
from pipeline.models import Batch, Document
from pipeline.schemas import DocumentOut, metadata_synced_from_json


def _doc(**kwargs) -> Document:
    defaults = {
        "id": uuid.uuid4(),
        "batch_id": uuid.uuid4(),
        "page_start": 0,
        "page_end": 1,
        "title": "Bank Statement",
        "summary": "March statement",
        "category": "bank",
        "tags": ["bank"],
        "split_confidence": 0.95,
        "enrich_confidence": 0.9,
        "needs_review": False,
        "entities": [],
    }
    defaults.update(kwargs)
    return Document(**defaults)


def test_metadata_synced_from_json_true_only_when_ok():
    assert metadata_synced_from_json({"sync": {"ok": True}}) is True
    assert metadata_synced_from_json({"sync": {"ok": False}}) is False
    assert metadata_synced_from_json({"sync": {"ok": True, "partial": True}}) is True
    assert metadata_synced_from_json({"sync": {"partial": True, "errors": ["x"]}}) is False
    assert metadata_synced_from_json({"sync": None}) is False
    assert metadata_synced_from_json({}) is False
    assert metadata_synced_from_json(None) is False


def test_document_out_exposes_v11_fields_from_orm():
    doc = _doc(
        document_date=date(2024, 3, 15),
        originator="Chase Bank",
        entities=["John Dinglebarre", "Chase Bank"],
        paperless_id=42,
        metadata_json={
            "enrich": {"confidence": 0.9},
            "sync": {"ok": True, "partial": False, "errors": []},
        },
    )
    out = DocumentOut.model_validate(doc)
    assert out.document_date == date(2024, 3, 15)
    assert out.originator == "Chase Bank"
    assert out.entities == ["John Dinglebarre", "Chase Bank"]
    assert out.metadata_synced is True


def test_document_out_nullable_metadata_fields():
    doc = _doc(
        document_date=None,
        originator=None,
        entities=[],
        metadata_json={"enrich": {}, "sync": None},
    )
    out = DocumentOut.model_validate(doc)
    assert out.document_date is None
    assert out.originator is None
    assert out.entities == []
    assert out.metadata_synced is False


def test_get_batch_returns_v11_document_fields():
    batch_id = uuid.uuid4()
    doc_id = uuid.uuid4()
    now = datetime.now(timezone.utc)
    doc = _doc(
        id=doc_id,
        batch_id=batch_id,
        document_date=date(2024, 3, 15),
        originator="ACME Supplies",
        entities=["ACME Supplies"],
        paperless_id=99,
        metadata_json={"sync": {"ok": True}},
    )
    batch = Batch(
        id=batch_id,
        source_uri="s3://bucket/landing/test/",
        page_count=2,
        status="completed",
        created_at=now,
        updated_at=now,
    )
    batch.documents = [doc]
    batch.jobs = []

    mock_session = MagicMock()
    mock_session.get.return_value = batch

    with patch("pipeline.api.session_scope") as scope:
        scope.return_value.__enter__.return_value = mock_session
        client = TestClient(app)
        resp = client.get(f"/batches/{batch_id}")

    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == str(batch_id)
    assert len(body["documents"]) == 1
    d = body["documents"][0]
    assert d["id"] == str(doc_id)
    assert d["document_date"] == "2024-03-15"
    assert d["originator"] == "ACME Supplies"
    assert d["entities"] == ["ACME Supplies"]
    assert d["metadata_synced"] is True
    assert "metadata_json" not in d


def test_get_batch_metadata_synced_false_on_partial_sync():
    batch_id = uuid.uuid4()
    now = datetime.now(timezone.utc)
    doc = _doc(
        batch_id=batch_id,
        metadata_json={"sync": {"ok": False, "partial": True, "errors": ["tag_overflow"]}},
    )
    batch = Batch(
        id=batch_id,
        source_uri="s3://bucket/landing/partial/",
        page_count=1,
        status="completed",
        created_at=now,
        updated_at=now,
    )
    batch.documents = [doc]
    batch.jobs = []

    mock_session = MagicMock()
    mock_session.get.return_value = batch

    with patch("pipeline.api.session_scope") as scope:
        scope.return_value.__enter__.return_value = mock_session
        client = TestClient(app)
        resp = client.get(f"/batches/{batch_id}")

    assert resp.status_code == 200
    assert resp.json()["documents"][0]["metadata_synced"] is False
