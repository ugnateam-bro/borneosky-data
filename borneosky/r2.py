"""Cloudflare R2 access (S3-compatible)."""

import gzip
import json
from functools import lru_cache

import boto3
from botocore.config import Config

from .config import require


@lru_cache(maxsize=1)
def client():
    return boto3.client(
        "s3",
        endpoint_url=f"https://{require('R2_ACCOUNT_ID')}.r2.cloudflarestorage.com",
        aws_access_key_id=require("R2_ACCESS_KEY_ID"),
        aws_secret_access_key=require("R2_SECRET_ACCESS_KEY"),
        region_name="auto",
        config=Config(retries={"max_attempts": 5, "mode": "standard"}),
    )


def bucket() -> str:
    return require("R2_BUCKET")


def put_json(key: str, obj, *, content_type: str = "application/json",
             cache_seconds: int = 300, gzipped: bool = False) -> int:
    """Upload obj as compact JSON, optionally pre-gzipped (served with
    Content-Encoding: gzip, which browsers decode transparently).
    Returns the byte size written."""
    body = json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode()
    extra = {}
    if gzipped:
        body = gzip.compress(body, compresslevel=9, mtime=0)
        extra["ContentEncoding"] = "gzip"
    client().put_object(
        Bucket=bucket(),
        Key=key,
        Body=body,
        ContentType=f"{content_type}; charset=utf-8",
        CacheControl=f"public, max-age={cache_seconds}",
        **extra,
    )
    return len(body)


def get_json(key: str):
    """Return the parsed object at key, or None if it does not exist."""
    try:
        resp = client().get_object(Bucket=bucket(), Key=key)
    except client().exceptions.NoSuchKey:
        return None
    body = resp["Body"].read()
    if resp.get("ContentEncoding") == "gzip":
        body = gzip.decompress(body)
    return json.loads(body)
