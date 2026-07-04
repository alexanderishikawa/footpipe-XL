"""S3-compatible object store (MinIO in Compose).

Thin wrapper implementing the `ObjectStore` interface from docs/design.md:
put / get / list / sign.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

import boto3
from botocore.client import Config
from botocore.exceptions import ClientError

from .config import get_settings


@dataclass
class ObjectInfo:
    key: str
    size: int
    etag: str


class S3ObjectStore:
    def __init__(self) -> None:
        s = get_settings()
        self._bucket = s.object_store_bucket
        self._endpoint = s.object_store_endpoint
        self._client = boto3.client(
            "s3",
            endpoint_url=s.object_store_endpoint,
            aws_access_key_id=s.object_store_access_key,
            aws_secret_access_key=s.object_store_secret_key,
            region_name=s.object_store_region,
            config=Config(signature_version="s3v4"),
        )

    @property
    def bucket(self) -> str:
        return self._bucket

    def ensure_bucket(self) -> None:
        try:
            self._client.head_bucket(Bucket=self._bucket)
        except ClientError:
            self._client.create_bucket(Bucket=self._bucket)

    def put(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> str:
        self._client.put_object(Bucket=self._bucket, Key=key, Body=data, ContentType=content_type)
        return self.uri(key)

    def get(self, key: str) -> bytes:
        obj = self._client.get_object(Bucket=self._bucket, Key=key)
        return obj["Body"].read()

    def exists(self, key: str) -> bool:
        try:
            self._client.head_object(Bucket=self._bucket, Key=key)
            return True
        except ClientError:
            return False

    def list(self, prefix: str) -> list[ObjectInfo]:
        paginator = self._client.get_paginator("list_objects_v2")
        out: list[ObjectInfo] = []
        for page in paginator.paginate(Bucket=self._bucket, Prefix=prefix):
            for item in page.get("Contents", []):
                out.append(
                    ObjectInfo(key=item["Key"], size=item["Size"], etag=item["ETag"].strip('"'))
                )
        return out

    def list_prefixes(self, prefix: str) -> list[str]:
        """Return immediate 'directory' prefixes under `prefix`."""
        result = self._client.list_objects_v2(Bucket=self._bucket, Prefix=prefix, Delimiter="/")
        return [cp["Prefix"] for cp in result.get("CommonPrefixes", [])]

    def sign(self, key: str, expires: int = 3600) -> str:
        return self._client.generate_presigned_url(
            "get_object", Params={"Bucket": self._bucket, "Key": key}, ExpiresIn=expires
        )

    def uri(self, key: str) -> str:
        return f"s3://{self._bucket}/{key}"

    def health(self) -> bool:
        self._client.list_buckets()
        return True


def checksum_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()
