"""Pydantic response models for the control API (docs/api-contract.md)."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, model_validator


def metadata_synced_from_json(metadata_json: dict | None) -> bool:
    """True only when metadata_json.sync.ok is explicitly True."""
    if not isinstance(metadata_json, dict):
        return False
    sync = metadata_json.get("sync")
    if not isinstance(sync, dict):
        return False
    return sync.get("ok") is True


class DocumentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    page_start: int
    page_end: int
    title: str | None = None
    summary: str | None = None
    category: str | None = None
    tags: list[str] = []
    document_date: date | None = None
    originator: str | None = None
    entities: list[str] = []
    split_confidence: float
    enrich_confidence: float | None = None
    needs_review: bool
    paperless_id: int | None = None
    metadata_synced: bool = False

    @model_validator(mode="wrap")
    @classmethod
    def _derive_metadata_synced(cls, data: Any, handler) -> DocumentOut:
        metadata_json = None
        if hasattr(data, "metadata_json"):
            metadata_json = data.metadata_json
        elif isinstance(data, dict):
            metadata_json = data.get("metadata_json")
        out = handler(data)
        synced = metadata_synced_from_json(metadata_json)
        if synced != out.metadata_synced:
            return out.model_copy(update={"metadata_synced": synced})
        return out


class JobOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    type: str
    status: str
    attempts: int
    last_error: str | None = None


class BatchOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    status: str
    source_uri: str
    page_count: int
    error: str | None = None
    documents: list[DocumentOut] = []
    jobs: list[JobOut] = []
    created_at: datetime
    updated_at: datetime


class RetryRequest(BaseModel):
    force: bool = False


class RetryResponse(BaseModel):
    id: uuid.UUID
    status: str
    requeued_jobs: list[str]


class LandingHookRequest(BaseModel):
    prefix: str


class LandingHookResponse(BaseModel):
    batch_id: uuid.UUID | None = None
    status: str


class HealthResponse(BaseModel):
    status: str
    checks: dict[str, str]
