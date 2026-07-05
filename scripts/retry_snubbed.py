#!/usr/bin/env python3
"""Re-queue enrich.run for documents snubbed by LLM failures (e.g. OpenAI balance).

Also re-OCR batches that failed with empty page text (split produced 0 documents).
"""

from __future__ import annotations

import argparse
import sys

from sqlalchemy import select

from pipeline.db import session_scope
from pipeline.models import Batch, Document, Job
from pipeline.queue import JobMessage, enqueue


def find_llm_snubbed(session) -> list[Document]:
    rows = session.scalars(
        select(Document).where(
            Document.metadata_json["enrich"]["error"].as_string() == "llm_failure"
        )
    ).all()
    if rows:
        return list(rows)
    # Fallback: zero-confidence other docs flagged for review.
    return list(
        session.scalars(
            select(Document).where(
                Document.category == "other",
                Document.enrich_confidence == 0,
                Document.needs_review.is_(True),
            )
        ).all()
    )


def find_empty_split_batches(session) -> list[Batch]:
    return list(
        session.scalars(
            select(Batch).where(
                Batch.status.in_(("failed", "failed_partial")),
                Batch.error.isnot(None),
            )
        ).all()
    )


def requeue_enrich(docs: list[Document]) -> int:
    if not docs:
        return 0
    batch_ids: set = set()
    with session_scope() as session:
        for doc in docs:
            d = session.get(Document, doc.id)
            if d is None:
                continue
            batch = session.get(Batch, d.batch_id)
            if batch is not None:
                batch.status = "enrich"
                batch.error = None
                batch_ids.add(batch.id)
            enqueue(
                JobMessage(
                    type="enrich.run",
                    entity_id=str(d.id),
                    batch_id=str(d.batch_id),
                )
            )
    print(f"requeued enrich.run for {len(docs)} document(s) across {len(batch_ids)} batch(es)")
    return len(docs)


def requeue_ocr(batch: Batch) -> None:
    with session_scope() as session:
        b = session.get(Batch, batch.id)
        if b is None:
            return
        b.status = "ocr"
        b.error = None
        ocr_job = session.scalar(
            select(Job).where(Job.batch_id == b.id, Job.type == "ocr.run")
        )
        entity = ocr_job.entity_id if ocr_job else str(b.id)
        if ocr_job:
            ocr_job.status = "queued"
    enqueue(
        JobMessage(
            type="ocr.run",
            entity_id=entity,
            batch_id=str(batch.id),
            force=True,
        )
    )
    print(f"requeued ocr.run (force) for batch {batch.id}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--ocr-empty",
        action="store_true",
        help="Also force re-OCR failed batches with no documents (empty OCR text)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="List what would be retried without enqueueing",
    )
    args = parser.parse_args()

    with session_scope() as session:
        docs = find_llm_snubbed(session)
        empty_batches = find_empty_split_batches(session) if args.ocr_empty else []

    if not docs and not empty_batches:
        print("nothing to retry")
        return 0

    if docs:
        print(f"found {len(docs)} document(s) with llm_failure")
        for d in docs[:20]:
            print(f"  doc {d.id} batch {d.batch_id} pages {d.page_start}-{d.page_end}")
        if len(docs) > 20:
            print(f"  ... and {len(docs) - 20} more")

    if empty_batches:
        print(f"found {len(empty_batches)} failed batch(es) for re-OCR")
        for b in empty_batches:
            print(f"  batch {b.id} error={b.error!r}")

    if args.dry_run:
        return 0

    requeue_enrich(docs)
    for b in empty_batches:
        requeue_ocr(b)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
