#!/usr/bin/env python3
"""Watch a scanner inbox folder and upload new PDFs to the object store landing zone.

Designed for Epson ES-580W "Scan to Network Folder" (SMB): the scanner drops a
multi-page PDF into a shared folder; this script renames it to footpipe's required
layout and uploads to MinIO/S3:

    landing/{YYYY}/{MM}/{DD}/{batch_id}/original.pdf

Run on the same host as the footpipe stack (or any machine with network access to
the object store). Reads object-store settings from environment (see .env.example).

Usage:
    python scripts/landing-watch.py /srv/scan-inbox
    python scripts/landing-watch.py /srv/scan-inbox --once   # process existing files and exit

Requires: boto3 (pip install boto3) or run inside the api container.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import boto3
from botocore.client import Config
from botocore.exceptions import ClientError

STABLE_SECONDS = 3.0
POLL_SECONDS = 2.0


def _client():
    return boto3.client(
        "s3",
        endpoint_url=os.environ.get("OBJECT_STORE_ENDPOINT", "http://localhost:9000"),
        aws_access_key_id=os.environ.get("OBJECT_STORE_ACCESS_KEY", "minioadmin"),
        aws_secret_access_key=os.environ.get("OBJECT_STORE_SECRET_KEY", "minioadmin"),
        region_name=os.environ.get("OBJECT_STORE_REGION", "us-east-1"),
        config=Config(signature_version="s3v4"),
    )


def _ensure_bucket(client, bucket: str) -> None:
    try:
        client.head_bucket(Bucket=bucket)
    except ClientError:
        client.create_bucket(Bucket=bucket)


def _landing_key(batch_id: str) -> str:
    now = datetime.now(timezone.utc)
    return f"landing/{now:%Y/%m/%d}/{batch_id}/original.pdf"


def _is_stable(path: Path, stable_seconds: float) -> bool:
    try:
        mtime = path.stat().st_mtime
        size = path.stat().st_size
    except OSError:
        return False
    if size == 0:
        return False
    return (time.time() - mtime) >= stable_seconds


def _upload_pdf(client, bucket: str, pdf: Path, batch_id: str) -> str:
    key = _landing_key(batch_id)
    with pdf.open("rb") as fh:
        client.put_object(
            Bucket=bucket,
            Key=key,
            Body=fh,
            ContentType="application/pdf",
        )
    return key


def _batch_id_from_name(pdf: Path) -> str:
    stem = pdf.stem.lower().replace(" ", "-")[:40]
    suffix = uuid.uuid4().hex[:6]
    return f"{stem}-{suffix}" if stem else f"scan-{suffix}"


def process_inbox(inbox: Path, *, once: bool = False) -> int:
    bucket = os.environ.get("OBJECT_STORE_BUCKET", "footpipe")
    client = _client()
    _ensure_bucket(client, bucket)
    seen: set[Path] = set()
    uploaded = 0

    print(f"landing-watch: inbox={inbox} bucket={bucket}", flush=True)

    while True:
        for pdf in sorted(inbox.glob("*.pdf")):
            if pdf in seen:
                continue
            if not _is_stable(pdf, STABLE_SECONDS):
                continue
            batch_id = _batch_id_from_name(pdf)
            try:
                key = _upload_pdf(client, bucket, pdf, batch_id)
            except Exception as exc:  # noqa: BLE001
                print(f"  [FAIL] {pdf.name}: {exc}", flush=True)
                continue
            print(f"  [OK] {pdf.name} -> s3://{bucket}/{key}", flush=True)
            # Archive locally so we do not re-upload on restart.
            done = inbox / "uploaded" / pdf.name
            done.parent.mkdir(parents=True, exist_ok=True)
            pdf.rename(done)
            seen.add(pdf)
            uploaded += 1

        if once:
            break
        time.sleep(POLL_SECONDS)

    return uploaded


def main() -> int:
    parser = argparse.ArgumentParser(description="Upload scanner PDFs to footpipe landing/")
    parser.add_argument("inbox", type=Path, help="Folder where the scanner saves PDFs")
    parser.add_argument(
        "--once",
        action="store_true",
        help="Process stable PDFs once and exit (no continuous watch)",
    )
    args = parser.parse_args()
    inbox: Path = args.inbox
    if not inbox.is_dir():
        print(f"inbox not found: {inbox}", file=sys.stderr)
        return 1
    count = process_inbox(inbox, once=args.once)
    print(f"landing-watch: uploaded {count} file(s)", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
