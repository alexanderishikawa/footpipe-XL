"""Load the category taxonomy from config/categories.yaml."""

from __future__ import annotations

from functools import lru_cache

import yaml

from .config import get_settings

_DEFAULT = ["invoice", "contract", "bank", "tax", "correspondence", "check", "other"]


@lru_cache
def load_categories() -> list[str]:
    path = get_settings().categories_path
    try:
        with open(path, encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
    except FileNotFoundError:
        return list(_DEFAULT)
    cats = data.get("categories") or _DEFAULT
    if "other" not in cats:
        cats = [*cats, "other"]
    return list(cats)
