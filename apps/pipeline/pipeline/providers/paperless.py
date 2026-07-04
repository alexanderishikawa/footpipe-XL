"""PaperlessArchive — commits final PDFs into Paperless-ngx via its REST API.

Bootstraps a token from admin credentials when `PAPERLESS_TOKEN` is unset,
uploads via `post_document`, then polls the task queue until the document is
consumed so we can persist its real `paperless_id`.
"""

from __future__ import annotations

import hashlib
import logging
import re
import time
from typing import Any

import httpx

from ..config import get_settings, load_category_entries, paperless_type_for_slug
from .base import Enrichment

log = logging.getLogger(__name__)

_RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})
_MAX_RETRIES = 3
_BACKOFF_BASE = 0.5


class PaperlessError(RuntimeError):
    pass


class PaperlessArchive:
    name = "paperless"

    def __init__(self) -> None:
        s = get_settings()
        self._base = s.paperless_url.rstrip("/")
        self._token = s.paperless_token or None
        self._admin_user = s.paperless_admin_user
        self._admin_pass = s.paperless_admin_password
        self._content_date_field_name = s.paperless_content_date_field_name
        self._bootstrap_types = s.paperless_bootstrap_types
        self._tag_cache: dict[str, int] = {}
        self._doc_type_cache: dict[str, int] = {}
        self._correspondent_cache: dict[str, int] = {}
        self._custom_field_cache: dict[str, int] = {}
        self._bootstrap_done = False

    # --- auth -----------------------------------------------------------------
    def _get_token(self) -> str:
        if self._token:
            return self._token
        resp = self._request(
            "post",
            f"{self._base}/api/token/",
            json={"username": self._admin_user, "password": self._admin_pass},
            timeout=15.0,
            retries=0,
        )
        if resp.status_code != 200:
            raise PaperlessError(f"token request failed: {resp.status_code} {resp.text}")
        self._token = resp.json()["token"]
        return self._token

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Token {self._get_token()}"}

    # --- HTTP with retry ------------------------------------------------------
    def _request(self, method: str, url: str, *, retries: int = _MAX_RETRIES, **kwargs) -> httpx.Response:
        headers = kwargs.pop("headers", None) or self._headers()
        last_resp: httpx.Response | None = None
        attempts = retries + 1
        for attempt in range(attempts):
            resp = httpx.request(method, url, headers=headers, **kwargs)
            if resp.status_code not in _RETRYABLE_STATUS or attempt == attempts - 1:
                return resp
            last_resp = resp
            delay = _BACKOFF_BASE * (2**attempt)
            log.warning(
                "paperless %s %s returned %s; retry %s/%s in %.1fs",
                method.upper(),
                url,
                resp.status_code,
                attempt + 1,
                retries,
                delay,
            )
            time.sleep(delay)
        assert last_resp is not None
        return last_resp

    # --- health ---------------------------------------------------------------
    def health(self) -> bool:
        try:
            resp = self._request("get", f"{self._base}/api/", timeout=10.0)
            return resp.status_code == 200
        except Exception:
            return False

    # --- upsert ---------------------------------------------------------------
    def upsert_document(self, title: str, pdf_bytes: bytes, metadata: dict) -> int:
        """Upload a PDF and return its Paperless document id (idempotent by checksum)."""
        doc_id = self._upload_document(title, pdf_bytes, metadata)
        self._apply_metadata(doc_id, metadata)
        return doc_id

    def _upload_document(self, title: str, pdf_bytes: bytes, metadata: dict) -> int:
        files = {"document": ("document.pdf", pdf_bytes, "application/pdf")}
        data = {"title": title[:120]}
        created = metadata.get("created")
        if created:
            data["created"] = created

        resp = self._request(
            "post",
            f"{self._base}/api/documents/post_document/",
            data=data,
            files=files,
            timeout=60.0,
        )
        if resp.status_code not in (200, 201):
            if resp.status_code == 400 and "already exists" in resp.text.lower():
                existing = self._find_by_title(title)
                if existing is not None:
                    return existing
            raise PaperlessError(f"upload failed: {resp.status_code} {resp.text}")

        ctype = resp.headers.get("content-type", "")
        task_id = resp.json() if ctype.startswith("application/json") else resp.text
        task_id = str(task_id).strip().strip('"')
        return self._await_task(task_id, title)

    def _await_task(self, task_id: str, title: str, timeout: float = 180.0) -> int:
        deadline = time.time() + timeout
        while time.time() < deadline:
            resp = self._request(
                "get",
                f"{self._base}/api/tasks/",
                params={"task_id": task_id},
                timeout=15.0,
            )
            if resp.status_code == 200:
                tasks = resp.json()
                if isinstance(tasks, dict):
                    tasks = tasks.get("results", [])
                for task in tasks:
                    status = task.get("status")
                    if status == "SUCCESS":
                        doc_id = task.get("related_document")
                        if doc_id:
                            return int(doc_id)
                        found = self._find_by_title(title)
                        if found is not None:
                            return found
                    elif status == "FAILURE":
                        result = str(task.get("result") or "")
                        if "duplicate" in result.lower():
                            m = re.search(r"#(\d+)", result)
                            if m:
                                return int(m.group(1))
                            found = self._find_by_title(title)
                            if found is not None:
                                return found
                        raise PaperlessError(f"consumption failed: {result}")
            time.sleep(2.0)
        found = self._find_by_title(title)
        if found is not None:
            return found
        raise PaperlessError(f"timed out awaiting consumption of task {task_id}")

    def _find_by_title(self, title: str) -> int | None:
        resp = self._request(
            "get",
            f"{self._base}/api/documents/",
            params={"title__icontains": title[:120], "page_size": 1},
            timeout=15.0,
        )
        if resp.status_code == 200:
            results = resp.json().get("results", [])
            if results:
                return int(results[0]["id"])
        return None

    def count_by_title(self, needle: str) -> int:
        resp = self._request(
            "get",
            f"{self._base}/api/documents/",
            params={"title__icontains": needle, "page_size": 1},
            timeout=15.0,
        )
        if resp.status_code != 200:
            raise PaperlessError(f"count failed: {resp.status_code} {resp.text}")
        return int(resp.json().get("count", 0))

    def get_document_tags(self, paperless_id: int) -> list[str]:
        """Return tag names for a Paperless document (resolves ids via tag cache/API)."""
        resp = self._request(
            "get",
            f"{self._base}/api/documents/{paperless_id}/",
            timeout=15.0,
        )
        if resp.status_code != 200:
            raise PaperlessError(
                f"get document failed: {resp.status_code} {resp.text}"
            )
        raw_ids = resp.json().get("tags") or []
        names: list[str] = []
        for raw_id in raw_ids:
            name = self._tag_name_for_id(int(raw_id))
            if name:
                names.append(name)
        return names

    def _tag_name_for_id(self, tag_id: int) -> str | None:
        for name, cached_id in self._tag_cache.items():
            if cached_id == tag_id:
                return name
        resp = self._request(
            "get",
            f"{self._base}/api/tags/{tag_id}/",
            timeout=15.0,
        )
        if resp.status_code != 200:
            return None
        name = str(resp.json().get("name") or "").strip()
        if not name:
            return None
        self._tag_cache[name] = tag_id
        return name

    # --- sync_metadata (v1.1) -------------------------------------------------
    def sync_metadata(self, paperless_id: int, enrichment_or_metadata: Enrichment | dict) -> dict[str, Any]:
        """PATCH tags, correspondent, document type, and content-date custom field.

        Returns an audit dict for ``metadata_json.sync`` (task-7 persists it).
        Gates date/originator sync using enrich eligibility flags when present.
        """
        self._ensure_bootstrap()
        meta = self._normalize_sync_input(enrichment_or_metadata)
        errors: list[str] = []
        tag_ids: list[int] = []
        correspondent_id: int | None = None
        document_type_id: int | None = None
        content_date: str | None = None
        content_date_field_id: int | None = None
        payload: dict[str, object] = {}

        for tag in meta.get("tags") or []:
            if not isinstance(tag, str):
                continue
            tag_id = self._resolve_tag_id(tag)
            if tag_id is not None:
                tag_ids.append(tag_id)
            else:
                errors.append(f"tag:{tag}")

        if tag_ids:
            payload["tags"] = tag_ids

        category = meta.get("category")
        if category:
            type_id = self._resolve_document_type_id(str(category))
            if type_id is not None:
                payload["document_type"] = type_id
                document_type_id = type_id
            else:
                errors.append(f"document_type:{category}")

        if meta.get("originator_sync_eligible") and meta.get("originator"):
            corr_id = self._resolve_correspondent_id(str(meta["originator"]))
            if corr_id is not None:
                payload["correspondent"] = corr_id
                correspondent_id = corr_id
            else:
                errors.append(f"correspondent:{meta['originator']}")

        if (
            meta.get("document_date_sync_eligible")
            and not meta.get("document_date_rejected_future")
            and meta.get("document_date")
        ):
            field_id = self._resolve_content_date_field_id()
            if field_id is not None:
                content_date = str(meta["document_date"])
                content_date_field_id = field_id
                payload["custom_fields"] = [{"field": field_id, "value": content_date}]
            else:
                errors.append("content_date_field")

        patch_ok = True
        if payload:
            try:
                self._patch_document(paperless_id, payload, raise_on_error=True)
            except PaperlessError as exc:
                patch_ok = False
                errors.append(str(exc))

        applied = patch_ok and bool(payload)
        partial = bool(errors) and applied
        ok = applied and not errors

        return {
            "ok": ok,
            "partial": partial,
            "tag_ids": tag_ids,
            "correspondent_id": correspondent_id,
            "document_type_id": document_type_id,
            "content_date_field_id": content_date_field_id,
            "content_date": content_date,
            "errors": errors or None,
        }

    def _normalize_sync_input(self, data: Enrichment | dict) -> dict[str, Any]:
        settings = get_settings()
        if isinstance(data, Enrichment):
            return {
                "category": data.category,
                "tags": list(data.tags),
                "document_date": data.document_date,
                "originator": data.originator,
                "document_date_sync_eligible": (
                    data.document_date is not None
                    and data.document_date_confidence >= settings.metadata_date_min_conf
                ),
                "originator_sync_eligible": (
                    data.originator is not None
                    and data.originator_confidence >= settings.metadata_originator_min_conf
                ),
                "document_date_rejected_future": False,
            }
        return {
            "category": data.get("category"),
            "tags": list(data.get("tags") or []),
            "document_date": data.get("document_date"),
            "originator": data.get("originator"),
            "document_date_sync_eligible": bool(data.get("document_date_sync_eligible")),
            "originator_sync_eligible": bool(data.get("originator_sync_eligible")),
            "document_date_rejected_future": bool(data.get("document_date_rejected_future")),
        }

    # --- bootstrap ------------------------------------------------------------
    def _ensure_bootstrap(self) -> None:
        if self._bootstrap_done:
            return
        if self._bootstrap_types:
            self._bootstrap_document_types()
        self._bootstrap_content_date_field()
        self._bootstrap_done = True

    def _bootstrap_document_types(self) -> None:
        for entry in load_category_entries():
            if entry.paperless_type in self._doc_type_cache:
                continue
            existing = self._get_by_name("/api/document_types/", entry.paperless_type)
            if existing is not None:
                self._doc_type_cache[entry.paperless_type] = existing
                continue
            created = self._request(
                "post",
                f"{self._base}/api/document_types/",
                json={"name": entry.paperless_type},
                timeout=15.0,
            )
            if created.status_code in (200, 201):
                self._doc_type_cache[entry.paperless_type] = int(created.json()["id"])
            elif created.status_code == 400:
                retry = self._get_by_name("/api/document_types/", entry.paperless_type)
                if retry is not None:
                    self._doc_type_cache[entry.paperless_type] = retry

    def _bootstrap_content_date_field(self) -> None:
        name = self._content_date_field_name
        if name in self._custom_field_cache:
            return
        existing = self._get_by_name("/api/custom_fields/", name)
        if existing is not None:
            self._custom_field_cache[name] = existing
            return
        created = self._request(
            "post",
            f"{self._base}/api/custom_fields/",
            json={"name": name, "data_type": "date"},
            timeout=15.0,
        )
        if created.status_code in (200, 201):
            self._custom_field_cache[name] = int(created.json()["id"])
        elif created.status_code == 400:
            retry = self._get_by_name("/api/custom_fields/", name)
            if retry is not None:
                self._custom_field_cache[name] = retry

    # --- metadata (Phase 0: tags + category → document_type) ------------------
    def _apply_metadata(self, doc_id: int, metadata: dict) -> None:
        payload: dict[str, object] = {}
        tags = metadata.get("tags")
        if isinstance(tags, list):
            tag_ids: list[int] = []
            for tag in tags:
                if isinstance(tag, str):
                    tag_id = self._resolve_tag_id(tag)
                    if tag_id is not None:
                        tag_ids.append(tag_id)
            if tag_ids:
                payload["tags"] = tag_ids

        category = metadata.get("category")
        if category:
            type_id = self._resolve_document_type_id(str(category))
            if type_id is not None:
                payload["document_type"] = type_id

        if not payload:
            return

        self._patch_document(doc_id, payload, raise_on_error=True)

    def _patch_document(
        self, doc_id: int, payload: dict[str, object], *, raise_on_error: bool
    ) -> bool:
        resp = self._request(
            "patch",
            f"{self._base}/api/documents/{doc_id}/",
            json=payload,
            timeout=15.0,
        )
        if resp.status_code in (200, 204):
            return True
        msg = f"metadata patch failed: {resp.status_code} {resp.text}"
        if raise_on_error:
            raise PaperlessError(msg)
        log.warning(msg)
        return False

    def _get_by_name(self, path: str, name: str) -> int | None:
        resp = self._request(
            "get",
            f"{self._base}{path}",
            params={"name__iexact": name, "page_size": 1},
            timeout=15.0,
        )
        if resp.status_code == 200:
            results = resp.json().get("results", [])
            if results:
                return int(results[0]["id"])
        return None

    def _resolve_tag_id(self, name: str) -> int | None:
        name = name.strip()[:128]
        if not name:
            return None
        cached = self._tag_cache.get(name)
        if cached is not None:
            return cached

        found = self._get_by_name("/api/tags/", name)
        if found is not None:
            self._tag_cache[name] = found
            return found

        create = self._request(
            "post",
            f"{self._base}/api/tags/",
            json={"name": name, "color": "#a6cee3", "matching_algorithm": 1},
            timeout=15.0,
        )
        if create.status_code in (200, 201):
            tag_id = int(create.json()["id"])
            self._tag_cache[name] = tag_id
            return tag_id
        if create.status_code == 400:
            retry = self._get_by_name("/api/tags/", name)
            if retry is not None:
                self._tag_cache[name] = retry
                return retry
        return None

    def _resolve_document_type_id(self, category_slug: str) -> int | None:
        display = paperless_type_for_slug(category_slug) or category_slug.strip()
        if not display:
            return None
        cached = self._doc_type_cache.get(display)
        if cached is not None:
            return cached
        found = self._get_by_name("/api/document_types/", display)
        if found is not None:
            self._doc_type_cache[display] = found
            return found
        return None

    def _resolve_correspondent_id(self, name: str) -> int | None:
        name = self._normalize_correspondent_name(name)
        if not name:
            return None
        cached = self._correspondent_cache.get(name)
        if cached is not None:
            return cached

        found = self._get_by_name("/api/correspondents/", name)
        if found is not None:
            self._correspondent_cache[name] = found
            return found

        create = self._request(
            "post",
            f"{self._base}/api/correspondents/",
            json={"name": name},
            timeout=15.0,
        )
        if create.status_code in (200, 201):
            corr_id = int(create.json()["id"])
            self._correspondent_cache[name] = corr_id
            return corr_id
        if create.status_code == 400:
            retry = self._get_by_name("/api/correspondents/", name)
            if retry is not None:
                self._correspondent_cache[name] = retry
                return retry
        return None

    def _resolve_content_date_field_id(self) -> int | None:
        name = self._content_date_field_name
        cached = self._custom_field_cache.get(name)
        if cached is not None:
            return cached
        found = self._get_by_name("/api/custom_fields/", name)
        if found is not None:
            self._custom_field_cache[name] = found
            return found
        return None

    @staticmethod
    def _normalize_correspondent_name(name: str) -> str:
        name = name.strip()
        if len(name) <= 128:
            return name
        digest = hashlib.sha256(name.encode()).hexdigest()[:8]
        return f"{name[:119]}-{digest}"
