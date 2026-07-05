"""Runtime configuration loaded from environment variables.

All secret names mirror `.env.example` at the repo root. Defaults keep the
service runnable with the Compose stack and `fake` providers (M1-M7).
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache

import yaml
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=None, extra="ignore")

    # Providers (fake default; azure/openai for live — M8)
    ocr_provider: str = "fake"
    llm_provider: str = "fake"
    azure_document_intelligence_endpoint: str = ""
    azure_document_intelligence_key: str = ""
    # Azure F0: 4 MB + 2 pages/request. S0: 500 MB + 2000 pages — raise chunk on paid tier.
    azure_ocr_chunk_pages: int = 2
    azure_ocr_chunk_max_bytes: int = 3_500_000
    openai_api_key: str = ""
    openai_model: str = "gpt-4o-mini"

    # Data stores
    database_url: str = "postgresql+psycopg://pipeline:pipeline@postgres:5432/pipeline"
    redis_url: str = "redis://redis:6379/0"

    # S3-compatible object store (MinIO in Compose)
    object_store_endpoint: str = "http://minio:9000"
    object_store_bucket: str = "footpipe"
    object_store_access_key: str = "minioadmin"
    object_store_secret_key: str = "minioadmin"
    object_store_region: str = "us-east-1"

    # Paperless-ngx
    paperless_url: str = "http://paperless:8000"
    paperless_token: str = ""
    paperless_admin_user: str = "admin"
    paperless_admin_password: str = "admin"

    # Optional landing webhook (poller is primary)
    landing_hook_secret: str = ""

    # Guardrails
    split_min_confidence: float = 0.6
    # Content-aware split: minimum weighted start-score to open a new document.
    split_start_threshold: float = 0.6
    max_pages_per_batch: int = 200
    max_pages_per_day: int = 500

    # API
    api_host: str = "0.0.0.0"
    api_port: int = 8080

    # Categories taxonomy config
    categories_path: str = "/app/config/categories.yaml"

    # v1.1 metadata sync thresholds (used by enrich.run / sync_metadata)
    metadata_date_min_conf: float = 0.7
    metadata_originator_min_conf: float = 0.6
    paperless_bootstrap_types: bool = True
    paperless_content_date_field_name: str = "Content Date"
    max_enrichment_tags: int = 20

    # Poller
    poll_interval_seconds: float = 2.0
    landing_prefix: str = "landing/"


@lru_cache
def get_settings() -> Settings:
    return Settings()


@dataclass(frozen=True)
class CategoryEntry:
    slug: str
    paperless_type: str


_DEFAULT_CATEGORY_ENTRIES: tuple[CategoryEntry, ...] = (
    CategoryEntry("invoice", "Invoice"),
    CategoryEntry("contract", "Contract"),
    CategoryEntry("bank", "Bank Statement"),
    CategoryEntry("tax", "Tax Document"),
    CategoryEntry("correspondence", "Correspondence"),
    CategoryEntry("check", "Check"),
    CategoryEntry("other", "Other"),
)


def _parse_category_entries(raw: object) -> list[CategoryEntry]:
    if not raw:
        return list(_DEFAULT_CATEGORY_ENTRIES)
    if isinstance(raw, list) and raw and isinstance(raw[0], str):
        return [CategoryEntry(slug=s, paperless_type=s.replace("_", " ").title()) for s in raw]
    entries: list[CategoryEntry] = []
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, dict):
            continue
        slug = str(item.get("slug") or "").strip().lower()
        if not slug:
            continue
        paperless_type = str(item.get("paperless_type") or slug.replace("_", " ").title())
        entries.append(CategoryEntry(slug=slug, paperless_type=paperless_type))
    if not entries:
        return list(_DEFAULT_CATEGORY_ENTRIES)
    if not any(e.slug == "other" for e in entries):
        entries.append(CategoryEntry("other", "Other"))
    return entries


@lru_cache
def load_category_entries() -> tuple[CategoryEntry, ...]:
    """Load slug + paperless_type rows from categories.yaml."""
    path = get_settings().categories_path
    try:
        with open(path, encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
    except FileNotFoundError:
        return _DEFAULT_CATEGORY_ENTRIES
    return tuple(_parse_category_entries(data.get("categories")))


def load_category_slugs() -> list[str]:
    return [e.slug for e in load_category_entries()]


def paperless_type_for_slug(slug: str) -> str | None:
    needle = slug.strip().lower()
    for entry in load_category_entries():
        if entry.slug == needle:
            return entry.paperless_type
    return None
