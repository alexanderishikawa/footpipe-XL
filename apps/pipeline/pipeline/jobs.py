"""Pipeline job handlers (idempotent) and dispatch.

Each handler runs inside a DB session supplied by the worker, mutates state,
and returns the next `JobMessage`s to enqueue. A single worker consumes the
queue serially, so handlers do not contend for the same batch.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

from sqlalchemy import select

from .categories import load_categories
from .config import get_settings
from .models import Artifact, Batch, Document, Page
from .objectstore import S3ObjectStore, checksum_bytes
from .pdfutil import extract_pages, page_count
from .providers.registry import get_archive_provider, get_llm_provider, get_ocr_provider
from .queue import JobMessage
from .split_logic import split_pages

MAX_ATTEMPTS = 3


def _key_from_uri(store: S3ObjectStore, uri: str) -> str:
    prefix = f"s3://{store.bucket}/"
    return uri[len(prefix):] if uri.startswith(prefix) else uri


def _original_key(session, store: S3ObjectStore, batch: Batch) -> str:
    art = session.scalar(
        select(Artifact).where(Artifact.batch_id == batch.id, Artifact.kind == "original")
    )
    if art is None:
        raise RuntimeError(f"no original artifact for batch {batch.id}")
    return _key_from_uri(store, art.uri)


# --- ingest.register ----------------------------------------------------------
def ingest_register(session, msg: JobMessage) -> list[JobMessage]:
    store = S3ObjectStore()
    settings = get_settings()
    original_key = msg.entity_id
    prefix = original_key.rsplit("/", 1)[0] + "/"
    source_uri = store.uri(prefix)

    existing = session.scalar(select(Batch).where(Batch.source_uri == source_uri))
    if existing is not None:
        return []  # already ingested (poller de-dupes, but be safe)

    pdf_bytes = store.get(original_key)
    checksum = checksum_bytes(pdf_bytes)
    pages = page_count(pdf_bytes)

    batch = Batch(source_uri=source_uri, page_count=pages, status="landed", checksum=checksum)
    session.add(batch)
    session.flush()

    session.add(
        Artifact(
            batch_id=batch.id,
            kind="original",
            uri=store.uri(original_key),
            checksum=checksum,
        )
    )

    # Duplicate checksum -> skipped_duplicate (do not reprocess).
    dupe = session.scalar(
        select(Batch).where(Batch.checksum == checksum, Batch.id != batch.id)
    )
    if dupe is not None:
        batch.status = "skipped_duplicate"
        return []

    # Guardrails.
    if pages > settings.max_pages_per_batch:
        batch.status = "failed"
        batch.error = (
            f"page_count {pages} exceeds MAX_PAGES_PER_BATCH={settings.max_pages_per_batch}"
        )
        return []

    start_of_day = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    total_today = sum(
        b.page_count
        for b in session.scalars(select(Batch).where(Batch.created_at >= start_of_day)).all()
    )
    if total_today > settings.max_pages_per_day:
        batch.status = "failed"
        batch.error = (
            f"daily pages {total_today} exceeds MAX_PAGES_PER_DAY={settings.max_pages_per_day}"
        )
        return []

    for i in range(pages):
        session.add(Page(batch_id=batch.id, page_index=i))

    return [JobMessage(type="ocr.run", entity_id=str(batch.id), batch_id=str(batch.id))]


# --- ocr.run ------------------------------------------------------------------
def ocr_run(session, msg: JobMessage) -> list[JobMessage]:
    store = S3ObjectStore()
    batch = session.get(Batch, _uuid(msg.entity_id))
    if batch is None:
        raise RuntimeError(f"batch {msg.entity_id} not found")
    batch.status = "ocr"

    pages = session.scalars(
        select(Page).where(Page.batch_id == batch.id).order_by(Page.page_index)
    ).all()

    already = all(p.text is not None for p in pages) and len(pages) > 0
    if already and not msg.force:
        # Skip paid OCR when artifacts already exist (mandatory rule).
        return [JobMessage(type="split.run", entity_id=str(batch.id), batch_id=str(batch.id))]

    pdf_bytes = store.get(_original_key(session, store, batch))
    result = get_ocr_provider().ocr_document(pdf_bytes)

    ocr_json_key = f"ocr/{batch.id}.json"
    payload = [
        {"page_index": p.page_index, "text": p.text, "confidence": p.confidence}
        for p in result.pages
    ]
    store.put(ocr_json_key, json.dumps(payload).encode(), content_type="application/json")
    session.add(
        Artifact(
            batch_id=batch.id,
            kind="ocr_json",
            uri=store.uri(ocr_json_key),
            checksum=checksum_bytes(json.dumps(payload).encode()),
        )
    )

    by_index = {p.page_index: p for p in pages}
    for pr in result.pages:
        page = by_index.get(pr.page_index)
        if page is not None:
            page.text = pr.text
            page.ocr_uri = store.uri(ocr_json_key)

    return [JobMessage(type="split.run", entity_id=str(batch.id), batch_id=str(batch.id))]


# --- split.run ----------------------------------------------------------------
def split_run(session, msg: JobMessage) -> list[JobMessage]:
    settings = get_settings()
    batch = session.get(Batch, _uuid(msg.entity_id))
    if batch is None:
        raise RuntimeError(f"batch {msg.entity_id} not found")
    batch.status = "split"

    # Idempotency: clear any prior split output before recomputing.
    for doc in list(batch.documents):
        session.delete(doc)
    session.flush()

    pages = session.scalars(
        select(Page).where(Page.batch_id == batch.id).order_by(Page.page_index)
    ).all()
    ordered = [(p.page_index, p.text or "") for p in pages]
    split_docs = split_pages(ordered)

    next_msgs: list[JobMessage] = []
    for sd in split_docs:
        doc = Document(
            batch_id=batch.id,
            page_start=sd.page_start,
            page_end=sd.page_end,
            split_confidence=sd.confidence,
            needs_review=sd.confidence < settings.split_min_confidence,
            tags=[],
        )
        session.add(doc)
        session.flush()
        next_msgs.append(
            JobMessage(type="enrich.run", entity_id=str(doc.id), batch_id=str(batch.id))
        )

    if not next_msgs:
        # No documents (e.g. all-blank batch) -> finalize directly.
        return [JobMessage(type="batch.finalize", entity_id=str(batch.id), batch_id=str(batch.id))]
    return next_msgs


# --- enrich.run ---------------------------------------------------------------
def enrich_run(session, msg: JobMessage) -> list[JobMessage]:
    doc = session.get(Document, _uuid(msg.entity_id))
    if doc is None:
        raise RuntimeError(f"document {msg.entity_id} not found")
    batch = session.get(Batch, doc.batch_id)
    if batch is not None:
        batch.status = "enrich"

    pages = session.scalars(
        select(Page)
        .where(
            Page.batch_id == doc.batch_id,
            Page.page_index >= doc.page_start,
            Page.page_index <= doc.page_end,
        )
        .order_by(Page.page_index)
    ).all()
    doc_text = "\n".join(p.text or "" for p in pages)

    try:
        enr = get_llm_provider().enrich(doc_text, load_categories())
    except Exception:
        # Fallback on LLM failure (hands-off): category 'other' + OCR snippet.
        snippet = (doc_text.strip().splitlines() or ["Untitled document"])[0][:120]
        enr = None
        doc.title = snippet or "Untitled document"
        doc.summary = doc_text.strip()[:400]
        doc.category = "other"
        doc.tags = ["other"]
        doc.enrich_confidence = 0.0
    if enr is not None:
        doc.title = enr.title
        doc.summary = enr.summary
        doc.category = enr.category
        doc.tags = list(enr.tags)
        doc.enrich_confidence = enr.confidence

    return [JobMessage(type="commit.archive", entity_id=str(doc.id), batch_id=str(doc.batch_id))]


# --- commit.archive -----------------------------------------------------------
def commit_archive(session, msg: JobMessage) -> list[JobMessage]:
    store = S3ObjectStore()
    doc = session.get(Document, _uuid(msg.entity_id))
    if doc is None:
        raise RuntimeError(f"document {msg.entity_id} not found")
    batch = session.get(Batch, doc.batch_id)
    if batch is not None:
        batch.status = "commit"

    if doc.paperless_id is not None:
        return _maybe_finalize(session, batch)

    original_bytes = store.get(_original_key(session, store, batch))
    final_pdf = extract_pages(
        original_bytes,
        doc.page_start,
        doc.page_end,
        metadata={"/Producer": "footpipe-XL", "/Keywords": f"batch:{batch.id} doc:{doc.id}"},
    )
    final_key = f"final/{batch.id}/{doc.id}.pdf"
    store.put(final_key, final_pdf, content_type="application/pdf")
    session.add(
        Artifact(
            batch_id=batch.id,
            document_id=doc.id,
            kind="final_pdf",
            uri=store.uri(final_key),
            checksum=checksum_bytes(final_pdf),
        )
    )

    # Embed the batch id in the title so it is queryable in Paperless.
    base = (doc.title or "Untitled document")[:60]
    title = f"{base} [{batch.id}]"
    metadata = {
        "created": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "category": doc.category,
        "tags": doc.tags,
        "batch_id": str(batch.id),
        "summary": doc.summary,
    }
    paperless_id = get_archive_provider().upsert_document(title, final_pdf, metadata)
    doc.paperless_id = paperless_id
    session.flush()

    return _maybe_finalize(session, batch)


def _maybe_finalize(session, batch: Batch) -> list[JobMessage]:
    docs = session.scalars(select(Document).where(Document.batch_id == batch.id)).all()
    if docs and all(d.paperless_id is not None for d in docs):
        return [JobMessage(type="batch.finalize", entity_id=str(batch.id), batch_id=str(batch.id))]
    return []


# --- batch.finalize -----------------------------------------------------------
def batch_finalize(session, msg: JobMessage) -> list[JobMessage]:
    batch = session.get(Batch, _uuid(msg.entity_id))
    if batch is None:
        raise RuntimeError(f"batch {msg.entity_id} not found")

    docs = session.scalars(select(Document).where(Document.batch_id == batch.id)).all()
    committed = [d for d in docs if d.paperless_id is not None]
    if docs and len(committed) == len(docs):
        batch.status = "completed"
        batch.error = None
    elif committed:
        batch.status = "failed_partial"
        batch.error = f"{len(docs) - len(committed)} of {len(docs)} documents not archived"
    else:
        batch.status = "failed"
        batch.error = batch.error or "no documents archived"
    return []


DISPATCH = {
    "ingest.register": ingest_register,
    "ocr.run": ocr_run,
    "split.run": split_run,
    "enrich.run": enrich_run,
    "commit.archive": commit_archive,
    "batch.finalize": batch_finalize,
}


def _uuid(value: str):
    import uuid

    return uuid.UUID(str(value))
