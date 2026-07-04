"""Golden smoke test — end-to-end over fixtures with fake providers.

Uploads each fixture's `original.pdf` to the object store `landing/` prefix,
waits for the worker to process the batch to a terminal status, then asserts
the document counts / categories / archive results in `expected.json`.

Run inside the api container: `python -m pipeline.smoke` (see `make smoke`).
"""

from __future__ import annotations

import io
import json
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from pypdf import PdfReader, PdfWriter
from sqlalchemy import select

from .config import get_settings
from .db import session_scope
from .models import Batch, Document
from .objectstore import S3ObjectStore
from .providers.paperless import PaperlessArchive
from .schemas import metadata_synced_from_json

FIXTURES_DIR = Path("/app/fixtures")
TERMINAL = {"completed", "failed", "failed_partial", "skipped_duplicate"}


def _salt(pdf_bytes: bytes, run_id: str) -> bytes:
    """Add unique metadata so each smoke run produces a fresh (non-duplicate) batch."""
    reader = PdfReader(io.BytesIO(pdf_bytes))
    writer = PdfWriter()
    for page in reader.pages:
        writer.add_page(page)
    writer.add_metadata({"/Producer": "footpipe-smoke", "/Keywords": f"run:{run_id}"})
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


def discover_fixtures() -> list[tuple[str, Path, dict]]:
    out = []
    for d in sorted(FIXTURES_DIR.iterdir()):
        pdf = d / "original.pdf"
        exp = d / "expected.json"
        if pdf.exists() and exp.exists():
            out.append((d.name, pdf, json.loads(exp.read_text())))
    return out


def wait_for_batch(source_uri: str, timeout: float = 300.0) -> Batch | None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        with session_scope() as session:
            batch = session.scalar(select(Batch).where(Batch.source_uri == source_uri))
            if batch is not None and batch.status in TERMINAL:
                session.expunge(batch)
                return batch
        time.sleep(2.0)
    with session_scope() as session:
        batch = session.scalar(select(Batch).where(Batch.source_uri == source_uri))
        if batch is not None:
            session.expunge(batch)
        return batch


def check(name: str, cond: bool, detail: str) -> bool:
    status = "PASS" if cond else "FAIL"
    print(f"  [{status}] {name}: {detail}")
    return cond


def doc_has_tags_any_of(actual_tags: list[str], allowed: list[str]) -> bool:
    """True when at least one allowed tag is present (used by smoke + unit tests)."""
    if not allowed:
        return True
    actual = set(actual_tags)
    return any(tag in actual for tag in allowed)


def resolve_tags_for_assertion(
    paperless: PaperlessArchive,
    paperless_id: int | None,
    postgres_tags: list[str],
) -> tuple[list[str], str]:
    """Prefer live Paperless tags; fall back to Postgres Document.tags when unavailable."""
    if paperless_id is not None:
        try:
            return paperless.get_document_tags(paperless_id), "paperless"
        except Exception:  # noqa: BLE001
            pass
    return list(postgres_tags), "postgres"


def run() -> int:
    run_id = uuid.uuid4().hex[:8]
    store = S3ObjectStore()
    store.ensure_bucket()
    paperless = PaperlessArchive()
    date = datetime.now(timezone.utc).strftime("%Y/%m/%d")

    fixtures = discover_fixtures()
    if not fixtures:
        print("no fixtures found under /app/fixtures")
        return 1

    print(f"== footpipe smoke (run {run_id}); {len(fixtures)} fixtures ==")
    settings = get_settings()
    print(f"   OCR_PROVIDER={settings.ocr_provider} LLM_PROVIDER={settings.llm_provider}")

    uploaded: list[tuple[str, str, dict]] = []
    for name, pdf, expected in fixtures:
        salted = _salt(pdf.read_bytes(), f"{run_id}-{name}")
        key = f"landing/{date}/{name}-{run_id}/original.pdf"
        store.put(key, salted, content_type="application/pdf")
        source_uri = store.uri(f"landing/{date}/{name}-{run_id}/")
        uploaded.append((name, source_uri, expected))
        print(f"uploaded fixture '{name}' -> {source_uri}")

    all_ok = True
    for name, source_uri, expected in uploaded:
        print(f"\n-- fixture: {name} --")
        batch = wait_for_batch(source_uri)
        if batch is None:
            all_ok = False
            print("  [FAIL] batch never created")
            continue

        with session_scope() as session:
            docs = session.scalars(
                select(Document).where(Document.batch_id == batch.id)
            ).all()
            categories = [d.category for d in docs]
            needs_review = [d.needs_review for d in docs]
            paperless_ids = [d.paperless_id for d in docs]
            doc_tags = [list(d.tags or []) for d in docs]
            doc_metadata = [d.metadata_json for d in docs]
            batch_id = str(batch.id)

        ok = True
        ok &= check(
            "status", batch.status == "completed", f"got '{batch.status}' (error={batch.error})"
        )
        ok &= check(
            "document_count",
            len(docs) == expected["documents"],
            f"got {len(docs)}, expected {expected['documents']}",
        )
        allowed = set(expected["categories_any_of"])
        ok &= check(
            "categories_subset",
            all(c in allowed for c in categories),
            f"got {categories}, allowed {sorted(allowed)}",
        )
        ratio = (sum(1 for r in needs_review if r) / len(docs)) if docs else 0.0
        ok &= check(
            "needs_review_ratio",
            ratio <= expected.get("max_needs_review_ratio", 1.0),
            f"got {ratio:.2f}, max {expected.get('max_needs_review_ratio', 1.0)}",
        )
        ok &= check(
            "archived_all",
            all(pid is not None for pid in paperless_ids) and len(paperless_ids) > 0,
            f"paperless_ids={paperless_ids}",
        )
        try:
            count = paperless.count_by_title(batch_id)
        except Exception as exc:  # noqa: BLE001
            count = -1
            print(f"    (paperless count error: {exc})")
        ok &= check(
            "paperless_count",
            count == expected["documents"],
            f"documents titled with batch id in Paperless = {count}",
        )

        tags_any_of = expected.get("tags_any_of")
        if tags_any_of:
            require_synced = expected.get("require_metadata_synced", True)
            for i, pid in enumerate(paperless_ids):
                resolved, source = resolve_tags_for_assertion(
                    paperless, pid, doc_tags[i]
                )
                ok &= check(
                    f"tags_any_of_doc_{i}",
                    doc_has_tags_any_of(resolved, tags_any_of),
                    f"{source} tags={resolved}, need any of {tags_any_of}",
                )
                if require_synced:
                    synced = metadata_synced_from_json(doc_metadata[i])
                    ok &= check(
                        f"metadata_synced_doc_{i}",
                        synced,
                        f"metadata_json.sync.ok={synced}",
                    )

        all_ok &= ok

    print("\n== SMOKE", "PASSED ==" if all_ok else "FAILED ==")
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(run())
