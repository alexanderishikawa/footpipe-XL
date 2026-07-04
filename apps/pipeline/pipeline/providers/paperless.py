"""PaperlessArchive — commits final PDFs into Paperless-ngx via its REST API.

Bootstraps a token from admin credentials when `PAPERLESS_TOKEN` is unset,
uploads via `post_document`, then polls the task queue until the document is
consumed so we can persist its real `paperless_id`.
"""

from __future__ import annotations

import re
import time

import httpx

from ..config import get_settings


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

    # --- auth -----------------------------------------------------------------
    def _get_token(self) -> str:
        if self._token:
            return self._token
        resp = httpx.post(
            f"{self._base}/api/token/",
            json={"username": self._admin_user, "password": self._admin_pass},
            timeout=15.0,
        )
        if resp.status_code != 200:
            raise PaperlessError(f"token request failed: {resp.status_code} {resp.text}")
        self._token = resp.json()["token"]
        return self._token

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Token {self._get_token()}"}

    # --- health ---------------------------------------------------------------
    def health(self) -> bool:
        try:
            resp = httpx.get(f"{self._base}/api/", headers=self._headers(), timeout=10.0)
            return resp.status_code == 200
        except Exception:
            return False

    # --- upsert ---------------------------------------------------------------
    def upsert_document(self, title: str, pdf_bytes: bytes, metadata: dict) -> int:
        """Upload a PDF and return its Paperless document id (idempotent by checksum)."""
        files = {"document": ("document.pdf", pdf_bytes, "application/pdf")}
        data = {"title": title[:120]}
        created = metadata.get("created")
        if created:
            data["created"] = created

        resp = httpx.post(
            f"{self._base}/api/documents/post_document/",
            headers=self._headers(),
            data=data,
            files=files,
            timeout=60.0,
        )
        if resp.status_code not in (200, 201):
            # Paperless returns 400 when the checksum already exists.
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
            resp = httpx.get(
                f"{self._base}/api/tasks/",
                headers=self._headers(),
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
                        # Paperless rejects identical PDFs as duplicates; treat
                        # that as idempotent success and return the existing id.
                        if "duplicate" in result.lower():
                            m = re.search(r"#(\d+)", result)
                            if m:
                                return int(m.group(1))
                            found = self._find_by_title(title)
                            if found is not None:
                                return found
                        raise PaperlessError(f"consumption failed: {result}")
            time.sleep(2.0)
        # Fall back to a title lookup in case the task record rotated out.
        found = self._find_by_title(title)
        if found is not None:
            return found
        raise PaperlessError(f"timed out awaiting consumption of task {task_id}")

    def _find_by_title(self, title: str) -> int | None:
        resp = httpx.get(
            f"{self._base}/api/documents/",
            headers=self._headers(),
            params={"title__icontains": title[:120], "page_size": 1},
            timeout=15.0,
        )
        if resp.status_code == 200:
            results = resp.json().get("results", [])
            if results:
                return int(results[0]["id"])
        return None

    def count_by_title(self, needle: str) -> int:
        resp = httpx.get(
            f"{self._base}/api/documents/",
            headers=self._headers(),
            params={"title__icontains": needle, "page_size": 1},
            timeout=15.0,
        )
        if resp.status_code != 200:
            raise PaperlessError(f"count failed: {resp.status_code} {resp.text}")
        return int(resp.json().get("count", 0))
