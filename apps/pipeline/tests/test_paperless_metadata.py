"""Unit tests for PaperlessArchive metadata sync (Phase 0 + v1.1 sync_metadata)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import httpx
import pytest

from pipeline.providers.base import Enrichment
from pipeline.providers.paperless import PaperlessArchive, PaperlessError

_BASE = "http://paperless.test"
_TOKEN = "test-token"


def _archive(*, bootstrap_types: bool = False) -> PaperlessArchive:
    arch = PaperlessArchive()
    arch._base = _BASE
    arch._token = _TOKEN
    arch._bootstrap_types = bootstrap_types
    arch._bootstrap_done = True
    return arch


def _mock_response(status_code: int, json_data=None, text: str = "") -> MagicMock:
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status_code
    resp.text = text
    resp.json.return_value = json_data if json_data is not None else {}
    resp.headers = {"content-type": "application/json"}
    return resp


def _patch_httpx(handler):
    return patch(
        "pipeline.providers.paperless.httpx.request",
        side_effect=lambda method, url, **kw: handler(method.lower(), url, **kw),
    )


def test_upsert_applies_tags_and_document_type():
    arch = _archive()
    metadata = {
        "created": "2024-01-15",
        "category": "invoice",
        "tags": ["vendor", "paid"],
    }
    patch_calls: list[tuple] = []

    def fake_request(method, url, **kwargs):
        if method == "post" and url.endswith("/api/documents/post_document/"):
            return _mock_response(200, "task-1")
        if method == "get" and "/api/tasks/" in url:
            return _mock_response(
                200,
                {"results": [{"status": "SUCCESS", "related_document": 42}]},
            )
        if method == "get" and "/api/tags/" in url:
            name = kwargs.get("params", {}).get("name__iexact", "")
            if name == "vendor":
                return _mock_response(200, {"results": [{"id": 10, "name": "vendor"}]})
            if name == "paid":
                return _mock_response(200, {"results": []})
            return _mock_response(200, {"results": []})
        if method == "post" and url.endswith("/api/tags/"):
            body = kwargs.get("json", {})
            return _mock_response(201, {"id": 11, "name": body.get("name")})
        if method == "get" and "/api/document_types/" in url:
            name = kwargs.get("params", {}).get("name__iexact", "")
            if name == "Invoice":
                return _mock_response(200, {"results": [{"id": 5, "name": "Invoice"}]})
            return _mock_response(200, {"results": []})
        if method == "patch" and "/api/documents/42/" in url:
            patch_calls.append((url, kwargs.get("json")))
            return _mock_response(200, {})
        raise AssertionError(f"unexpected {method} {url}")

    with _patch_httpx(fake_request):
        doc_id = arch.upsert_document("Test Doc [batch-1]", b"%PDF-1.4", metadata)

    assert doc_id == 42
    assert len(patch_calls) == 1
    assert patch_calls[0][1] == {"tags": [10, 11], "document_type": 5}


def test_duplicate_upload_still_applies_metadata():
    arch = _archive()
    metadata = {"category": "bank", "tags": ["statement"]}
    patch_calls: list[dict] = []

    def fake_request(method, url, **kwargs):
        if method == "post" and url.endswith("/api/documents/post_document/"):
            return _mock_response(400, text="Document with this checksum already exists")
        if method == "get" and "/api/documents/" in url and "title__icontains" in str(
            kwargs.get("params", {})
        ):
            return _mock_response(200, {"results": [{"id": 99, "title": "Existing"}]})
        if method == "get" and "/api/tags/" in url:
            return _mock_response(200, {"results": [{"id": 20, "name": "statement"}]})
        if method == "get" and "/api/document_types/" in url:
            name = kwargs.get("params", {}).get("name__iexact", "")
            if name == "Bank Statement":
                return _mock_response(200, {"results": [{"id": 3, "name": "Bank Statement"}]})
            return _mock_response(200, {"results": []})
        if method == "patch" and "/api/documents/99/" in url:
            patch_calls.append(kwargs.get("json", {}))
            return _mock_response(200, {})
        raise AssertionError(f"unexpected {method} {url}")

    with _patch_httpx(fake_request):
        doc_id = arch.upsert_document("Existing [batch-2]", b"%PDF-dup", metadata)

    assert doc_id == 99
    assert patch_calls == [{"tags": [20], "document_type": 3}]


def test_task_failure_duplicate_still_applies_metadata():
    arch = _archive()
    metadata = {"tags": ["tax"], "category": "tax"}
    patch_calls: list[dict] = []

    def fake_request(method, url, **kwargs):
        if method == "post" and url.endswith("/api/documents/post_document/"):
            return _mock_response(200, "task-dup")
        if method == "get" and "/api/tasks/" in url:
            return _mock_response(
                200,
                {
                    "results": [
                        {
                            "status": "FAILURE",
                            "result": "Duplicate document detected #77",
                        }
                    ]
                },
            )
        if method == "get" and "/api/tags/" in url:
            return _mock_response(200, {"results": [{"id": 30, "name": "tax"}]})
        if method == "get" and "/api/document_types/" in url:
            name = kwargs.get("params", {}).get("name__iexact", "")
            if name == "Tax Document":
                return _mock_response(200, {"results": [{"id": 7, "name": "Tax Document"}]})
            return _mock_response(200, {"results": []})
        if method == "patch" and "/api/documents/77/" in url:
            patch_calls.append(kwargs.get("json", {}))
            return _mock_response(200, {})
        raise AssertionError(f"unexpected {method} {url}")

    with _patch_httpx(fake_request), patch("pipeline.providers.paperless.time.sleep"):
        doc_id = arch.upsert_document("Dup Task [batch-3]", b"%PDF-task", metadata)

    assert doc_id == 77
    assert patch_calls == [{"tags": [30], "document_type": 7}]


def test_missing_document_type_skips_type_in_patch():
    arch = _archive()
    metadata = {"category": "unknown-cat", "tags": ["misc"]}
    patch_calls: list[dict] = []

    def fake_request(method, url, **kwargs):
        if method == "post" and url.endswith("/api/documents/post_document/"):
            return _mock_response(200, "task-2")
        if method == "get" and "/api/tasks/" in url:
            return _mock_response(
                200,
                {"results": [{"status": "SUCCESS", "related_document": 50}]},
            )
        if method == "get" and "/api/tags/" in url:
            return _mock_response(200, {"results": [{"id": 40, "name": "misc"}]})
        if method == "get" and "/api/document_types/" in url:
            return _mock_response(200, {"results": []})
        if method == "patch" and "/api/documents/50/" in url:
            patch_calls.append(kwargs.get("json", {}))
            return _mock_response(200, {})
        raise AssertionError(f"unexpected {method} {url}")

    with _patch_httpx(fake_request):
        arch.upsert_document("No Type [batch-4]", b"%PDF", metadata)

    assert patch_calls == [{"tags": [40]}]


def test_metadata_patch_failure_raises():
    arch = _archive()
    metadata = {"tags": ["fail-tag"]}

    def fake_request(method, url, **kwargs):
        if method == "post" and url.endswith("/api/documents/post_document/"):
            return _mock_response(200, "task-3")
        if method == "get" and "/api/tasks/" in url:
            return _mock_response(
                200,
                {"results": [{"status": "SUCCESS", "related_document": 60}]},
            )
        if method == "get" and "/api/tags/" in url:
            return _mock_response(200, {"results": [{"id": 50, "name": "fail-tag"}]})
        if method == "patch":
            return _mock_response(500, text="server error")
        raise AssertionError(f"unexpected {method} {url}")

    with _patch_httpx(fake_request):
        with pytest.raises(PaperlessError, match="metadata patch failed"):
            arch.upsert_document("Patch Fail", b"%PDF", metadata)


def test_sync_metadata_happy_path():
    arch = _archive()
    patch_payloads: list[dict] = []
    meta = {
        "category": "invoice",
        "tags": ["invoice", "entity:acme-corp"],
        "document_date": "2024-03-15",
        "document_date_sync_eligible": True,
        "document_date_rejected_future": False,
        "originator": "ACME Supplies",
        "originator_sync_eligible": True,
    }

    def fake_request(method, url, **kwargs):
        if method == "get" and "/api/tags/" in url:
            name = kwargs.get("params", {}).get("name__iexact", "")
            ids = {"invoice": 1, "entity:acme-corp": 2}
            if name in ids:
                return _mock_response(200, {"results": [{"id": ids[name], "name": name}]})
            return _mock_response(200, {"results": []})
        if method == "get" and "/api/document_types/" in url:
            return _mock_response(200, {"results": [{"id": 5, "name": "Invoice"}]})
        if method == "get" and "/api/correspondents/" in url:
            return _mock_response(200, {"results": []})
        if method == "post" and url.endswith("/api/correspondents/"):
            return _mock_response(201, {"id": 9, "name": "ACME Supplies"})
        if method == "get" and "/api/custom_fields/" in url:
            return _mock_response(200, {"results": [{"id": 18, "name": "Content Date"}]})
        if method == "patch" and "/api/documents/42/" in url:
            patch_payloads.append(kwargs.get("json", {}))
            return _mock_response(200, {})
        raise AssertionError(f"unexpected {method} {url} {kwargs}")

    with _patch_httpx(fake_request):
        result = arch.sync_metadata(42, meta)

    assert result["ok"] is True
    assert result["partial"] is False
    assert result["tag_ids"] == [1, 2]
    assert result["correspondent_id"] == 9
    assert result["document_type_id"] == 5
    assert result["content_date_field_id"] == 18
    assert result["content_date"] == "2024-03-15"
    assert result["errors"] is None
    assert patch_payloads == [
        {
            "tags": [1, 2],
            "document_type": 5,
            "correspondent": 9,
            "custom_fields": [{"field": 18, "value": "2024-03-15"}],
        }
    ]


def test_sync_metadata_skips_date_when_not_eligible():
    arch = _archive()
    patch_payloads: list[dict] = []
    meta = {
        "category": "bank",
        "tags": ["bank"],
        "document_date": "2099-01-01",
        "document_date_sync_eligible": False,
        "document_date_rejected_future": True,
        "originator_sync_eligible": False,
    }

    def fake_request(method, url, **kwargs):
        if method == "get" and "/api/tags/" in url:
            return _mock_response(200, {"results": [{"id": 3, "name": "bank"}]})
        if method == "get" and "/api/document_types/" in url:
            return _mock_response(200, {"results": [{"id": 4, "name": "Bank Statement"}]})
        if method == "patch":
            patch_payloads.append(kwargs.get("json", {}))
            return _mock_response(200, {})
        raise AssertionError(f"unexpected {method} {url}")

    with _patch_httpx(fake_request):
        result = arch.sync_metadata(10, meta)

    assert "custom_fields" not in (patch_payloads[0] if patch_payloads else {})
    assert result["content_date"] is None
    assert result["ok"] is True


def test_sync_metadata_tag_create_race_regets():
    arch = _archive()
    get_counts: dict[str, int] = {"new-tag": 0}

    def fake_request(method, url, **kwargs):
        if method == "get" and "/api/tags/" in url:
            name = kwargs.get("params", {}).get("name__iexact", "")
            get_counts[name] = get_counts.get(name, 0) + 1
            if name == "new-tag" and get_counts[name] >= 2:
                return _mock_response(200, {"results": [{"id": 55, "name": "new-tag"}]})
            return _mock_response(200, {"results": []})
        if method == "post" and url.endswith("/api/tags/"):
            return _mock_response(400, text="duplicate")
        if method == "patch":
            return _mock_response(200, {})
        raise AssertionError(f"unexpected {method} {url}")

    with _patch_httpx(fake_request):
        result = arch.sync_metadata(1, {"tags": ["new-tag"]})

    assert result["tag_ids"] == [55]
    assert get_counts["new-tag"] >= 2


def test_sync_metadata_retries_patch_on_429():
    arch = _archive()
    patch_attempts = {"n": 0}

    def fake_request(method, url, **kwargs):
        if method == "get" and "/api/tags/" in url:
            return _mock_response(200, {"results": [{"id": 1, "name": "x"}]})
        if method == "patch":
            patch_attempts["n"] += 1
            if patch_attempts["n"] == 1:
                return _mock_response(429, text="rate limited")
            return _mock_response(200, {})
        raise AssertionError(f"unexpected {method} {url}")

    with _patch_httpx(fake_request), patch("pipeline.providers.paperless.time.sleep"):
        result = arch.sync_metadata(5, {"tags": ["x"]})

    assert patch_attempts["n"] == 2
    assert result["ok"] is True


def test_sync_metadata_from_enrichment_object():
    arch = _archive()
    enr = Enrichment(
        title="T",
        summary="S",
        category="tax",
        tags=["tax"],
        confidence=0.9,
        document_date="2023-12-01",
        document_date_confidence=0.95,
        originator="IRS",
        originator_confidence=0.8,
    )
    patch_payloads: list[dict] = []

    def fake_request(method, url, **kwargs):
        if method == "get" and "/api/tags/" in url:
            return _mock_response(200, {"results": [{"id": 8, "name": "tax"}]})
        if method == "get" and "/api/document_types/" in url:
            return _mock_response(200, {"results": [{"id": 6, "name": "Tax Document"}]})
        if method == "get" and "/api/correspondents/" in url:
            return _mock_response(200, {"results": [{"id": 12, "name": "IRS"}]})
        if method == "get" and "/api/custom_fields/" in url:
            return _mock_response(200, {"results": [{"id": 20, "name": "Content Date"}]})
        if method == "patch":
            patch_payloads.append(kwargs.get("json", {}))
            return _mock_response(200, {})
        raise AssertionError(f"unexpected {method} {url}")

    with _patch_httpx(fake_request):
        result = arch.sync_metadata(99, enr)

    assert result["ok"] is True
    assert patch_payloads[0]["correspondent"] == 12
    assert patch_payloads[0]["custom_fields"] == [{"field": 20, "value": "2023-12-01"}]


def test_sync_metadata_partial_when_type_missing():
    arch = _archive()

    def fake_request(method, url, **kwargs):
        if method == "get" and "/api/tags/" in url:
            return _mock_response(200, {"results": [{"id": 1, "name": "misc"}]})
        if method == "get" and "/api/document_types/" in url:
            return _mock_response(200, {"results": []})
        if method == "patch":
            return _mock_response(200, {})
        raise AssertionError(f"unexpected {method} {url}")

    with _patch_httpx(fake_request):
        result = arch.sync_metadata(3, {"category": "missing", "tags": ["misc"]})

    assert result["partial"] is True
    assert result["ok"] is False
    assert result["errors"] is not None
    assert any("document_type" in e for e in result["errors"])


def test_tag_cache_avoids_duplicate_gets():
    arch = _archive()
    tag_gets = {"n": 0}

    def fake_request(method, url, **kwargs):
        if method == "get" and "/api/tags/" in url:
            tag_gets["n"] += 1
            return _mock_response(200, {"results": [{"id": 7, "name": "cached"}]})
        if method == "patch":
            return _mock_response(200, {})
        raise AssertionError(f"unexpected {method} {url}")

    with _patch_httpx(fake_request):
        arch.sync_metadata(1, {"tags": ["cached", "cached"]})
        arch.sync_metadata(2, {"tags": ["cached"]})

    assert tag_gets["n"] == 1


def test_correspondent_name_truncated_over_128_chars():
    arch = _archive()
    long_name = "A" * 200
    posted: list[str] = []

    def fake_request(method, url, **kwargs):
        if method == "get" and "/api/correspondents/" in url:
            return _mock_response(200, {"results": []})
        if method == "post" and url.endswith("/api/correspondents/"):
            posted.append(kwargs["json"]["name"])
            return _mock_response(201, {"id": 1, "name": posted[-1]})
        if method == "patch":
            return _mock_response(200, {})
        raise AssertionError(f"unexpected {method} {url}")

    with _patch_httpx(fake_request):
        arch.sync_metadata(
            1,
            {
                "originator": long_name,
                "originator_sync_eligible": True,
            },
        )

    assert len(posted) == 1
    assert len(posted[0]) <= 128
    assert posted[0].endswith(posted[0].split("-")[-1])


def test_get_document_tags_resolves_ids_via_cache_and_api():
    arch = _archive()
    arch._tag_cache = {"invoice": 1, "scanned": 2}
    tag_gets: list[int] = []

    def fake_request(method, url, **kwargs):
        if method == "get" and url.endswith("/api/documents/42/"):
            return _mock_response(200, {"id": 42, "tags": [1, 2, 99]})
        if method == "get" and url.endswith("/api/tags/99/"):
            tag_gets.append(99)
            return _mock_response(200, {"id": 99, "name": "entity:foo"})
        raise AssertionError(f"unexpected {method} {url}")

    with _patch_httpx(fake_request):
        tags = arch.get_document_tags(42)

    assert tags == ["invoice", "scanned", "entity:foo"]
    assert tag_gets == [99]
    assert arch._tag_cache["entity:foo"] == 99


def test_get_document_tags_raises_on_document_error():
    arch = _archive()

    def fake_request(method, url, **kwargs):
        if method == "get" and "/api/documents/5/" in url:
            return _mock_response(404, text="not found")
        raise AssertionError(f"unexpected {method} {url}")

    with _patch_httpx(fake_request):
        with pytest.raises(PaperlessError, match="get document failed"):
            arch.get_document_tags(5)
