"""OpenAI LLM enrichment for document title/summary/category/tags."""

from __future__ import annotations

import json
import logging
from typing import Any

from openai import OpenAI

from ..config import get_settings
from .base import Enrichment

log = logging.getLogger(__name__)


def _build_prompt(doc_text: str, categories: list[str]) -> tuple[str, str]:
    cats = ", ".join(categories)
    system = (
        "You classify scanned small-business mailroom documents. "
        "Respond with a single JSON object only (no markdown) with keys: "
        "title (string, <=120 chars), summary (string, <=400 chars), "
        "category (exactly one allowed value), tags (array of short strings), "
        f"confidence (float 0-1). Allowed categories: {cats}. "
        "If unsure, use category 'other' and lower confidence."
    )
    user = doc_text.strip()[:12_000] or "(empty document)"
    return system, user


def parse_enrichment(payload: dict[str, Any], categories: list[str]) -> Enrichment:
    category = str(payload.get("category") or "other").lower()
    if category not in categories:
        category = "other"
    tags_raw = payload.get("tags") or []
    tags = [str(t) for t in tags_raw if t]
    if category not in tags:
        tags.append(category)
    title = str(payload.get("title") or "Untitled document")[:120]
    summary = str(payload.get("summary") or "")[:400]
    try:
        confidence = float(payload.get("confidence", 0.7))
    except (TypeError, ValueError):
        confidence = 0.7
    confidence = max(0.0, min(1.0, confidence))
    return Enrichment(
        title=title,
        summary=summary,
        category=category,
        tags=sorted(set(tags)),
        confidence=confidence,
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
