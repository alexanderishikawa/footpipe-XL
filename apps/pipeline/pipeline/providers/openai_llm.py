"""OpenAI LLM enrichment for document title/summary/category/tags."""

from __future__ import annotations

import json
import logging
import re
from datetime import date
from typing import Any

from openai import OpenAI

from ..config import get_settings
from .base import Enrichment, normalize_enrichment_tags

log = logging.getLogger(__name__)

_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_MAX_ENTITIES = 10


def _clamp_confidence(value: Any, *, default: float = 0.0) -> float:
    try:
        confidence = float(value)
    except (TypeError, ValueError):
        return default
    return max(0.0, min(1.0, confidence))


def _parse_document_date(raw: Any) -> str | None:
    if raw is None or raw == "null":
        return None
    s = str(raw).strip()
    if not s or not _ISO_DATE.match(s):
        return None
    try:
        date.fromisoformat(s)
    except ValueError:
        return None
    return s


def _parse_optional_str(raw: Any, *, max_len: int = 256) -> str | None:
    if raw is None or raw == "null":
        return None
    s = str(raw).strip()
    if not s:
        return None
    return s[:max_len]


def _parse_entities(raw: Any) -> list[str]:
    if not raw:
        return []
    if not isinstance(raw, list):
        return []
    entities: list[str] = []
    for item in raw:
        if not item:
            continue
        s = str(item).strip()
        if s:
            entities.append(s[:256])
        if len(entities) >= _MAX_ENTITIES:
            break
    return entities


def _build_prompt(doc_text: str, categories: list[str]) -> tuple[str, str]:
    cats = ", ".join(categories)
    system = (
        "You classify scanned small-business mailroom documents. "
        "Respond with a single JSON object only (no markdown) with keys: "
        "title (string, <=120 chars), summary (string, <=400 chars), "
        "category (exactly one allowed value), tags (array of short strings), "
        "confidence (float 0-1), "
        "document_date (ISO date YYYY-MM-DD or null), "
        "document_date_confidence (float 0-1), "
        "originator (string or null), originator_confidence (float 0-1), "
        "entities (array of strings, max 10). "
        f"Allowed categories: {cats}. "
        "Prefer the document date printed on the form (invoice date, statement period end, "
        "letter date), not the scan or upload date. "
        "originator is the issuer or sender (bank name, IRS, vendor), not the recipient. "
        "entities are people and organizations mentioned (account holders, payees). "
        "If uncertain, use null and low confidence; never invent account numbers. "
        "If unsure of category, use 'other' and lower confidence."
    )
    user = doc_text.strip()[:12_000] or "(empty document)"
    return system, user


def parse_enrichment(payload: dict[str, Any], categories: list[str]) -> Enrichment:
    category = str(payload.get("category") or "other").lower()
    if category not in categories:
        category = "other"
    tags_raw = payload.get("tags") or []
    tags = [str(t) for t in tags_raw if t]
    title = str(payload.get("title") or "Untitled document")[:120]
    summary = str(payload.get("summary") or "")[:400]
    confidence = _clamp_confidence(payload.get("confidence"), default=0.7)

    document_date = _parse_document_date(payload.get("document_date"))
    document_date_confidence = _clamp_confidence(payload.get("document_date_confidence"))
    if document_date is None:
        document_date_confidence = 0.0

    originator = _parse_optional_str(payload.get("originator"))
    originator_confidence = _clamp_confidence(payload.get("originator_confidence"))
    if originator is None:
        originator_confidence = 0.0

    entities = _parse_entities(payload.get("entities"))

    settings = get_settings()
    normalized = normalize_enrichment_tags(
        category=category,
        tags=tags,
        entities=entities,
        max_tags=settings.max_enrichment_tags,
    )

    return Enrichment(
        title=title,
        summary=summary,
        category=category,
        tags=normalized.tags,
        confidence=confidence,
        document_date=document_date,
        document_date_confidence=document_date_confidence,
        originator=originator,
        originator_confidence=originator_confidence,
        entities=entities,
        tags_overflow=normalized.needs_review_overflow,
    )


class OpenAiLlm:
    name = "openai"

    def __init__(self) -> None:
        settings = get_settings()
        if not settings.openai_api_key:
            raise ValueError("OPENAI_API_KEY is required for LLM_PROVIDER=openai")
        self._client = OpenAI(api_key=settings.openai_api_key)
        self._model = settings.openai_model

    def enrich(self, doc_text: str, categories: list[str]) -> Enrichment:
        system, user = _build_prompt(doc_text, categories)
        log.info("openai enrich: model=%s chars=%d", self._model, len(user))
        response = self._client.chat.completions.create(
            model=self._model,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            temperature=0.2,
        )
        content = response.choices[0].message.content or "{}"
        payload = json.loads(content)
        return parse_enrichment(payload, categories)
