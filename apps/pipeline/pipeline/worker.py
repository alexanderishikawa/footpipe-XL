"""Worker: landing poller + serial job processor.

Single process, single consumer. Each iteration polls the object store's
`landing/` prefix for new batches, then processes one queued job. Job *state*
is tracked in Postgres `Job` rows for observability and retry.
"""

from __future__ import annotations

import logging
import time

from sqlalchemy import select

from .config import get_settings
from .db import session_scope
from .jobs import DISPATCH, MAX_ATTEMPTS
from .models import Batch, Job
from .objectstore import S3ObjectStore
from .queue import JobMessage, dequeue, enqueue

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s [worker] %(message)s")
log = logging.getLogger("footpipe.worker")


def _upsert_job(session, msg: JobMessage) -> Job:
    batch_uuid = _maybe_uuid(msg.batch_id)
    job = session.scalar(
        select(Job).where(
            Job.type == msg.type,
            Job.entity_id == msg.entity_id,
        )
    )
    if job is None:
        job = Job(
            type=msg.type,
            entity_id=msg.entity_id,
            batch_id=batch_uuid,
            status="queued",
            attempts=0,
        )
        session.add(job)
    return job


def _register_queued(session, msg: JobMessage) -> None:
    existing = session.scalar(
        select(Job).where(Job.type == msg.type, Job.entity_id == msg.entity_id)
    )
    if existing is None:
        session.add(
            Job(
                type=msg.type,
                entity_id=msg.entity_id,
                batch_id=_maybe_uuid(msg.batch_id),
                status="queued",
                attempts=0,
            )
        )


def process_one(msg: JobMessage) -> None:
    handler = DISPATCH.get(msg.type)
    if handler is None:
        log.error("unknown job type: %s", msg.type)
        return

    next_msgs: list[JobMessage] = []
    try:
        with session_scope() as session:
            job = _upsert_job(session, msg)
            job.status = "running"
            job.attempts += 1
            next_msgs = handler(session, msg)
            for nxt in next_msgs:
                _register_queued(session, nxt)
            job.status = "succeeded"
            job.last_error = None
        log.info("ok %s entity=%s -> %s", msg.type, msg.entity_id, [m.type for m in next_msgs])
    except Exception as exc:  # noqa: BLE001 - persist and continue
        log.exception("job failed: %s entity=%s", msg.type, msg.entity_id)
        with session_scope() as session:
            job = _upsert_job(session, msg)
            if job.attempts >= MAX_ATTEMPTS:
                job.status = "dead"
            else:
                job.status = "failed"
            job.last_error = str(exc)[:2000]
        return

    for nxt in next_msgs:
        enqueue(nxt)


def poll_landing(store: S3ObjectStore, seen: set[str]) -> None:
    settings = get_settings()
    try:
        objects = store.list(settings.landing_prefix)
    except Exception:
        log.exception("landing poll failed")
        return

    originals = [o.key for o in objects if o.key.endswith("/original.pdf")]
    if not originals:
        return

    with session_scope() as session:
        existing_sources = {
            row for row in session.scalars(select(Batch.source_uri)).all()
        }

    for key in originals:
        prefix = key.rsplit("/", 1)[0] + "/"
        source_uri = store.uri(prefix)
        if key in seen or source_uri in existing_sources:
            continue
        msg = JobMessage(type="ingest.register", entity_id=key)
        with session_scope() as session:
            _register_queued(session, msg)
        enqueue(msg)
        seen.add(key)
        log.info("enqueued ingest for %s", source_uri)


def wait_for_dependencies(retries: int = 60, delay: float = 2.0) -> S3ObjectStore:
    from .db import get_engine
    from .queue import get_redis

    for attempt in range(retries):
        try:
            get_engine().connect().close()
            get_redis().ping()
            store = S3ObjectStore()
            store.ensure_bucket()
            log.info("dependencies ready")
            return store
        except Exception as exc:  # noqa: BLE001
            log.info("waiting for dependencies (%s/%s): %s", attempt + 1, retries, exc)
            time.sleep(delay)
    raise RuntimeError("dependencies not ready")


def run() -> None:
    settings = get_settings()
    store = wait_for_dependencies()
    seen: set[str] = set()
    last_poll = 0.0
    log.info("worker started (ocr=%s llm=%s)", settings.ocr_provider, settings.llm_provider)

    while True:
        now = time.time()
        if now - last_poll >= settings.poll_interval_seconds:
            poll_landing(store, seen)
            last_poll = now

        msg = dequeue(timeout=2)
        if msg is not None:
            process_one(msg)


def _maybe_uuid(value: str | None):
    if not value:
        return None
    import uuid

    return uuid.UUID(str(value))


if __name__ == "__main__":
    run()
