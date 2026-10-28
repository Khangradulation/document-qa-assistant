"""Lambda: ingestion. Triggered by S3 ObjectCreated events, or invoked directly
by the API for reprocessing ({"reprocess": true, "owner_id": ..., "document_id": ...})."""

from __future__ import annotations

from typing import Any
from urllib.parse import unquote_plus

from docqa.deps import AppDeps, build_deps
from docqa.ingest import KEY_RE, ingest_document
from docqa.logs import log


def handle(event: dict[str, Any], context: Any, deps: AppDeps) -> list[str]:
    s = deps.settings
    outcomes: list[str] = []

    if event.get("reprocess") is True:
        owner, doc = str(event.get("owner_id", "")), str(event.get("document_id", ""))
        m = KEY_RE.match(f"uploads/{owner}/{doc}.pdf")
        if not m:
            return ["ignored"]
        key = f"uploads/{owner}/{doc}.pdf"
        head = deps.s3.head_object(Bucket=s.docs_bucket, Key=key)
        etag = str(head.get("ETag", "")).strip('"')
        return [ingest_document(deps, owner, doc, etag=etag, force=True)]

    for record in event.get("Records", []):
        if not str(record.get("eventName", "")).startswith("ObjectCreated"):
            continue
        s3 = record.get("s3", {})
        if s3.get("bucket", {}).get("name") != s.docs_bucket:
            outcomes.append("ignored")
            continue
        key = unquote_plus(s3.get("object", {}).get("key", ""))  # S3 URL-encodes keys
        m = KEY_RE.match(key)
        if not m:
            log("WARNING", "ingest_ignored_key")  # never log the raw key from outside
            outcomes.append("ignored")
            continue
        etag = str(s3.get("object", {}).get("eTag", ""))
        outcomes.append(ingest_document(deps, m["owner"], m["doc"], etag=etag, force=False))
    return outcomes


def lambda_handler(event: dict[str, Any], context: Any) -> list[str]:
    return handle(event, context, build_deps())
