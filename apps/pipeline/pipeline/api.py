"""FastAPI control + observability API (docs/api-contract.md)."""

from __future__ import annotations

import uuid

from datetime import datetime, timezone

from fastapi import FastAPI, File, Form, Header, HTTPException, Response, UploadFile
from fastapi.responses import HTMLResponse
from sqlalchemy import select, text

from .config import get_settings
from .db import get_engine, session_scope
from .models import Batch, Job
from .objectstore import S3ObjectStore
from .providers.registry import get_archive_provider
from .queue import JobMessage, enqueue, get_redis
from .schemas import (
    BatchOut,
    HealthResponse,
    LandingHookRequest,
    LandingHookResponse,
    RetryRequest,
    RetryResponse,
)

app = FastAPI(title="footpipe-XL pipeline control API", version="0.1.0")

_UPLOAD_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>footpipe — upload batch</title>
  <style>
    body { font-family: system-ui, sans-serif; max-width: 32rem; margin: 2rem auto; padding: 0 1rem; }
    label { display: block; margin: 1rem 0 0.25rem; font-weight: 600; }
    input[type=file], input[type=text] { width: 100%; box-sizing: border-box; }
    button { margin-top: 1.25rem; padding: 0.6rem 1.2rem; font-size: 1rem; cursor: pointer; }
    .hint { color: #555; font-size: 0.9rem; margin-top: 0.25rem; }
    a { color: #06c; }
  </style>
</head>
<body>
  <h1>Upload a scan batch</h1>
  <p>Drop a multi-page PDF here. The worker picks it up from <code>landing/</code> automatically.</p>
  <form method="post" action="/upload" enctype="multipart/form-data">
    <label for="file">PDF file</label>
    <input id="file" name="file" type="file" accept="application/pdf,.pdf" required>
    <label for="batch_id">Batch name (optional)</label>
    <input id="batch_id" name="batch_id" type="text" placeholder="morning-mail">
    <p class="hint">Letters, numbers, dashes only. Leave blank for an auto name.</p>
    <button type="submit">Upload &amp; process</button>
  </form>
  <p class="hint"><a href="/health">Health</a> · <a href="http://localhost:8000" target="_blank">Paperless</a></p>
</body>
</html>"""

_RETRYABLE = {"failed", "dead"}
_TERMINAL = {"completed", "failed", "failed_partial", "skipped_duplicate"}


@app.get("/health", response_model=HealthResponse)
def health(response: Response) -> HealthResponse:
    checks: dict[str, str] = {}

    try:
        with get_engine().connect() as conn:
            conn.execute(text("SELECT 1"))
        checks["database"] = "ok"
    except Exception:
        checks["database"] = "error"

    try:
        get_redis().ping()
        checks["redis"] = "ok"
    except Exception:
        checks["redis"] = "error"

    try:
        S3ObjectStore().health()
        checks["object_store"] = "ok"
    except Exception:
        checks["object_store"] = "error"

    try:
        checks["paperless"] = "ok" if get_archive_provider().health() else "error"
    except Exception:
        checks["paperless"] = "error"

    ok = all(v == "ok" for v in checks.values())
    if not ok:
        response.status_code = 503
    return HealthResponse(status="ok" if ok else "degraded", checks=checks)


@app.get("/upload", response_class=HTMLResponse)
def upload_form() -> str:
    """Simple browser UI to land a PDF without MinIO console or CLI."""
    return _UPLOAD_HTML


@app.post("/upload", response_class=HTMLResponse)
async def upload_batch(
    file: UploadFile = File(...),
    batch_id: str = Form(default=""),
) -> str:
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="PDF file required")
    raw = await file.read()
    if not raw.startswith(b"%PDF"):
        raise HTTPException(status_code=400, detail="not a valid PDF")

    name = batch_id.strip().lower()
    if name:
        safe = "".join(c for c in name if c.isalnum() or c in "-_")
        if not safe:
            raise HTTPException(status_code=400, detail="invalid batch name")
    else:
        stem = (file.filename or "scan").rsplit(".", 1)[0]
        safe = "".join(c for c in stem.lower() if c.isalnum() or c in "-_")[:40] or "scan"
        safe = f"{safe}-{uuid.uuid4().hex[:6]}"

    now = datetime.now(timezone.utc)
    key = f"landing/{now:%Y/%m/%d}/{safe}/original.pdf"
    store = S3ObjectStore()
    store.put(key, raw, content_type="application/pdf")

    enqueue(JobMessage(type="ingest.register", entity_id=key))

    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"><title>Uploaded</title>
<style>body {{ font-family: system-ui, sans-serif; max-width: 36rem; margin: 2rem auto; }}</style>
</head><body>
  <h1>Uploaded</h1>
  <p>Your PDF is in <code>{key}</code>.</p>
  <p>The worker should pick it up within a few seconds.</p>
  <p><a href="/upload">Upload another</a> · <a href="http://localhost:8000" target="_blank">Paperless</a></p>
  <p class="hint">Watch logs: <code>make logs</code></p>
</body></html>"""


@app.get("/batches/{batch_id}", response_model=BatchOut)
def get_batch(batch_id: uuid.UUID) -> BatchOut:
    with session_scope() as session:
        batch = session.get(Batch, batch_id)
        if batch is None:
            raise HTTPException(status_code=404, detail="batch not found")
        # Touch relationships while the session is open.
        _ = (batch.documents, batch.jobs)
        return BatchOut.model_validate(batch)


@app.post("/batches/{batch_id}/retry", response_model=RetryResponse, status_code=202)
def retry_batch(batch_id: uuid.UUID, body: RetryRequest | None = None) -> RetryResponse:
    body = body or RetryRequest()
    requeued: list[JobMessage] = []
    with session_scope() as session:
        batch = session.get(Batch, batch_id)
        if batch is None:
            raise HTTPException(status_code=404, detail="batch not found")

        failed_jobs = session.scalars(
            select(Job).where(Job.batch_id == batch_id, Job.status.in_(_RETRYABLE))
        ).all()

        if body.force:
            # Allow re-OCR (paid path): requeue ocr.run from the top.
            ocr_job = session.scalar(
                select(Job).where(Job.batch_id == batch_id, Job.type == "ocr.run")
            )
            entity = ocr_job.entity_id if ocr_job else str(batch_id)
            if ocr_job:
                ocr_job.status = "queued"
            requeued.append(
                JobMessage(type="ocr.run", entity_id=entity, batch_id=str(batch_id), force=True)
            )
        else:
            if not failed_jobs:
                raise HTTPException(
                    status_code=409,
                    detail="no dead/failed jobs to retry (use force=true to re-OCR)",
                )
            for job in failed_jobs:
                job.status = "queued"
                requeued.append(
                    JobMessage(type=job.type, entity_id=job.entity_id, batch_id=str(batch_id))
                )

        if batch.status in _TERMINAL:
            batch.status = "ocr"
        new_status = batch.status

    for msg in requeued:
        enqueue(msg)

    return RetryResponse(
        id=batch_id, status=new_status, requeued_jobs=[m.type for m in requeued]
    )


@app.post("/hooks/landing", response_model=LandingHookResponse, status_code=202)
def landing_hook(
    body: LandingHookRequest, x_landing_secret: str | None = Header(default=None)
) -> LandingHookResponse:
    settings = get_settings()
    if not settings.landing_hook_secret:
        raise HTTPException(status_code=404, detail="landing hook not configured")
    if x_landing_secret != settings.landing_hook_secret:
        raise HTTPException(status_code=401, detail="invalid landing secret")

    store = S3ObjectStore()
    prefix = body.prefix if body.prefix.endswith("/") else body.prefix + "/"
    original = None
    for obj in store.list(prefix):
        if obj.key.endswith("/original.pdf") or obj.key.endswith("original.pdf"):
            original = obj.key
            break
    if original is None:
        raise HTTPException(status_code=404, detail="no original.pdf under prefix")

    source_uri = store.uri(original.rsplit("/", 1)[0] + "/")
    with session_scope() as session:
        existing = session.scalar(select(Batch).where(Batch.source_uri == source_uri))
        if existing is not None:
            return LandingHookResponse(batch_id=existing.id, status=existing.status)

    enqueue(JobMessage(type="ingest.register", entity_id=original))
    return LandingHookResponse(batch_id=None, status="landed")
