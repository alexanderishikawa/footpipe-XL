"""Runtime configuration loaded from environment variables.

All secret names mirror `.env.example` at the repo root. Defaults keep the
service runnable with the Compose stack and `fake` providers (M1-M7).
"""

from __future__ import annotations

from functools import lru_cache

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
    max_pages_per_batch: int = 200
    max_pages_per_day: int = 500

    # API
    api_host: str = "0.0.0.0"
    api_port: int = 8080

    # Categories taxonomy config
    categories_path: str = "/app/config/categories.yaml"

    # Poller
    poll_interval_seconds: float = 2.0
    landing_prefix: str = "landing/"


@lru_cache
def get_settings() -> Settings:
    return Settings()
