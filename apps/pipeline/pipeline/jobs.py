"""Pipeline job handlers (idempotent) and dispatch.

Each handler runs inside a DB session supplied by the worker, mutates state,
and returns the next `JobMessage`s to enqueue. A single worker consumes the
queue serially, so handlers do not contend for the same batch.
"""

from __future__ import annotations

import json
import logging
from datetime import date, datetime, timezone

from sqlalchemy import select

from .categories import load_categories
from .config import Settings, get_settings
from .models import Artifact, Batch, Document, Page
from .objectstore import S3ObjectStore, checksum_bytes
from .pdfutil import extract_pages, page_count
from .providers.base import Enrichment
from .providers.registry import get_archive_provider, get_llm_provider, get_ocr_provider
from .queue import JobMessage
from .split_logic import split_pages

log = logging.getLogger(__name__)

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


def _parse_content_date(iso_str: str | None) -> date | None:
    if not iso_str:
        return None
    try:
        return date.fromisoformat(iso_str)
    except ValueError:
        log.warning("invalid document_date ISO string: %r", iso_str)
        return None


def _is_future_content_date(value: date) -> bool:
    return value > date.today()


def _enrich_metadata_base(doc: Document) -> dict:
    existing = doc.metadata_json if isinstance(doc.metadata_json, dict) else {}
    return {"sync": existing.get("sync")}


def _build_enrich_metadata(
    enr: Enrichment,
    *,
    settings: Settings,
    parsed_date: date | None,
    future_date: bool,
) -> dict:
    date_sync_eligible = (
        parsed_date is not None
        and not future_date
        and enr.document_date_confidence >= settings.metadata_date_min_conf
    )
    originator_sync_eligible = (
        enr.originator is not None
        and enr.originator_confidence >= settings.metadata_originator_min_conf
    )
    return {
        "title": enr.title,
        "category": enr.category,
        "confidence": enr.confidence,
        "document_date": enr.document_date,
        "document_date_confidence": enr.document_date_confidence,
        "document_date_sync_eligible": date_sync_eligible,
        "document_date_rejected_future": future_date,
        "originator": enr.originator,
        "originator_confidence": enr.originator_confidence,
        "originator_sync_eligible": originator_sync_eligible,
        "entities": list(enr.entities),
        "tag_overflow": enr.tags_overflow,
    }


def _enrichment_needs_review(
    enr: Enrichment,
    *,
    settings: Settings,
    future_date: bool,
    empty_text: bool,
) -> bool:
    if empty_text:
        return True
    if enr.confidence < settings.split_min_confidence:
        return True
    if enr.tags_overflow:
        return True
    if future_date:
        return True
    if enr.document_date and enr.document_date_confidence < settings.metadata_date_min_conf:
        return True
    if enr.originator and enr.originator_confidence < settings.metadata_originator_min_conf:
        return True
    return False


def _apply_enrichment_failure(doc: Document, *, doc_text: str, error: str = "llm_failure") -> None:
    snippet = (doc_text.strip().splitlines() or ["Untitled document"])[0][:120]
    doc.title = snippet or "Untitled document"
    doc.summary = doc_text.strip()[:400]
    doc.category = "other"
    doc.tags = ["other"]
    doc.enrich_confidence = 0.0
    doc.document_date = None
    doc.originator = None
    doc.entities = []
    base = _enrich_metadata_base(doc)
    base["enrich"] = {"error": error}
    doc.metadata_json = base
    doc.needs_review = True


def _apply_enrichment_to_document(
    doc: Document,
    enr: Enrichment,
    *,
    settings: Settings | None = None,
    doc_text: str = "",
) -> None:
    settings = settings or get_settings()
    doc.title = enr.title
    doc.summary = enr.summary
    doc.category = enr.category
    doc.tags = list(enr.tags)
    doc.enrich_confidence = enr.confidence
    doc.entities = list(enr.entities)

    parsed_date = _parse_content_date(enr.document_date)
    future_date = parsed_date is not None and _is_future_content_date(parsed_date)
    doc.document_date = parsed_date

    if (
        enr.originator
        and enr.originator_confidence >= settings.metadata_originator_min_conf
    ):
        doc.originator = enr.originator[:256]
    else:
        doc.originator = None

    base = _enrich_metadata_base(doc)
    base["enrich"] = _build_enrich_metadata(
        enr,
        settings=settings,
        parsed_date=parsed_date,
        future_date=future_date,
    )
    doc.metadata_json = base

    review = _enrichment_needs_review(
        enr,
        settings=settings,
        future_date=future_date,
        empty_text=not doc_text.strip(),
    )
    doc.needs_review = doc.needs_review or review


# --- enrich.run ---------------------------------------------------------------
def enrich_run(session, msg: JobMessage) -> list[JobMessage]:
    settings = get_settings()
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
        log.exception("enrich.run LLM failure for document %s", doc.id)
        _apply_enrichment_failure(doc, doc_text=doc_text)
    else:
        _apply_enrichment_to_document(doc, enr, settings=settings, doc_text=doc_text)

    return [JobMessage(type="commit.archive", entity_id=str(doc.id), batch_id=str(doc.batch_id))]


def _build_sync_input(doc: Document) -> dict:
    """Build Paperless sync payload from Document columns + metadata_json.enrich flags."""
    enrich: dict = {}
    if isinstance(doc.metadata_json, dict):
        raw = doc.metadata_json.get("enrich")
        if isinstance(raw, dict):
            enrich = raw

    document_date = enrich.get("document_date")
    if document_date is None and doc.document_date is not None:
        document_date = doc.document_date.isoformat()

    return {
        "category": doc.category,
        "tags": list(doc.tags or []),
        "document_date": document_date,
        "originator": doc.originator,
        "document_date_sync_eligible": bool(enrich.get("document_date_sync_eligible")),
        "originator_sync_eligible": bool(enrich.get("originator_sync_eligible")),
        "document_date_rejected_future": bool(enrich.get("document_date_rejected_future")),
    }


def _sync_document_metadata(doc: Document) -> None:
    """Push enrichment to Paperless and persist audit under metadata_json.sync."""
    if doc.paperless_id is None:
        return

    archive = get_archive_provider()
    sync_input = _build_sync_input(doc)
    try:
        sync_result = archive.sync_metadata(doc.paperless_id, sync_input)
    except Exception:
        log.exception(
            "sync_metadata raised for document %s (paperless_id=%s)",
            doc.id,
            doc.paperless_id,
        )
        sync_result = {
            "ok": False,
            "partial": False,
            "tag_ids": [],
            "correspondent_id": None,
            "document_type_id": None,
            "content_date_field_id": None,
            "content_date": None,
            "errors": ["sync_metadata_exception"],
        }

    base = dict(doc.metadata_json) if isinstance(doc.metadata_json, dict) else {}
    base["sync"] = sync_result
    doc.metadata_json = base

    if not sync_result.get("ok"):
        doc.needs_review = True


# --- commit.archive -----------------------------------------------------------
def commit_archive(session, msg: JobMessage) -> list[JobMessage]:
    doc = session.get(Document, _uuid(msg.entity_id))
    if doc is None:
        raise RuntimeError(f"document {msg.entity_id} not found")
    batch = session.get(Batch, doc.batch_id)
    if batch is not None:
        batch.status = "commit"

    if doc.paperless_id is not None:
        _sync_document_metadata(doc)
        session.flush()
        return _maybe_finalize(session, batch)

    store = S3ObjectStore()

    original_bytes = store.get(_original_key(session, store, batch))
    # Provenance keyed on batch id + page range (stable across re-splits) so a
    # forced re-run produces byte-identical PDFs that Paperless dedupes, keeping
    # commit.archive idempotent instead of creating duplicate archive entries.
    final_pdf = extract_pages(
        original_bytes,
        doc.page_start,
        doc.page_end,
        metadata={
            "/Producer": "footpipe-XL",
            "/Keywords": f"batch:{batch.id} pages:{doc.page_start}-{doc.page_end}",
        },
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

    _sync_document_metadata(doc)
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
