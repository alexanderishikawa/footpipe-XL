"""Load the category taxonomy from config/categories.yaml."""

from __future__ import annotations

from .config import _DEFAULT_CATEGORY_ENTRIES, load_category_slugs, paperless_type_for_slug

_DEFAULT = [e.slug for e in _DEFAULT_CATEGORY_ENTRIES]

__all__ = ["_DEFAULT", "load_categories", "paperless_type_for_slug"]


def load_categories() -> list[str]:
    return load_category_slugs()
