"""Pydantic response models for the control API (docs/api-contract.md)."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict


class DocumentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    page_start: int
    page_end: int
    title: str | None = None
    summary: str | None = None
    category: str | None = None
    tags: list[str] = []
    split_confidence: float
    enrich_confidence: float | None = None
    needs_review: bool
    paperless_id: int | None = None


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
