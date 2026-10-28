"""Lambda: document management routes (upload URL, list, get, delete, reprocess, download URL)."""

from __future__ import annotations

import json
import uuid
from typing import Any

from docqa.chunking import keys_from_counts
from docqa.deps import AppDeps, build_deps
from docqa.http import ApiError, guarded, owner_from_event, parse_json_body
from docqa.ingest import object_key
from docqa.models import (
    DocumentRecord,
    DocumentStatus,
    UploadUrlRequest,
    is_valid_document_id,
)
from docqa.repo import utc_now


def _doc_id(event: dict[str, Any]) -> str:
    doc_id = str((event.get("pathParameters") or {}).get("document_id", ""))
    if not is_valid_document_id(doc_id):
        raise ApiError(400, "INVALID_DOCUMENT_ID", "document_id is invalid")
    return doc_id


def _require(deps: AppDeps, owner: str, doc_id: str) -> DocumentRecord:
    rec = deps.repo.get(owner, doc_id)
    if rec is None:  # also what you get for someone else's document: no existence leak
        raise ApiError(404, "NOT_FOUND", "Document not found")
    return rec


def create_upload_url(
    deps: AppDeps, owner: str, event: dict[str, Any]
) -> tuple[int, dict[str, Any]]:
    s = deps.settings
    req = UploadUrlRequest.parse(parse_json_body(event))
    if len(deps.repo.list_for_owner(owner)) >= s.max_docs_per_user:
        raise ApiError(409, "DOCUMENT_LIMIT_REACHED", "Delete a document before uploading more")
    doc_id = uuid.uuid4().hex
    now = utc_now()
    deps.repo.create(DocumentRecord(doc_id, owner, req.filename, DocumentStatus.UPLOADED, now, now))
    # The key is built by US from verified values; the client never supplies it.
    url = deps.s3.generate_presigned_url(
        "put_object",
        Params={
            "Bucket": s.docs_bucket,
            "Key": object_key(owner, doc_id),
            "ContentType": req.content_type,
        },
        ExpiresIn=s.upload_url_ttl_seconds,
    )
    return 201, {
        "document_id": doc_id,
        "upload_url": url,
        "method": "PUT",
        "headers": {"Content-Type": req.content_type},
        "expires_in": s.upload_url_ttl_seconds,
        "max_bytes": s.max_pdf_bytes,
    }


def delete_document(deps: AppDeps, owner: str, doc_id: str) -> tuple[int, dict[str, Any]]:
    rec = _require(deps, owner, doc_id)
    # Mark DELETING first (blocks queries + new ingestion), then clean up. If any
    # step fails the record stays DELETING and the same DELETE can simply be retried.
    if rec.status != DocumentStatus.DELETING and not deps.repo.begin_delete(owner, doc_id):
        raise ApiError(409, "CONFLICT", "Document changed; please retry")
    if rec.chunks_per_page:
        deps.store.delete_keys(keys_from_counts(doc_id, rec.chunks_per_page))
    deps.s3.delete_object(Bucket=deps.settings.docs_bucket, Key=object_key(owner, doc_id))
    deps.repo.delete(owner, doc_id)
    return 200, {"deleted": True}


def reprocess(deps: AppDeps, owner: str, doc_id: str) -> tuple[int, dict[str, Any]]:
    rec = _require(deps, owner, doc_id)
    if rec.status not in (DocumentStatus.READY, DocumentStatus.FAILED):
        raise ApiError(409, "NOT_REPROCESSABLE", f"Cannot reprocess a {rec.status} document")
    deps.lambda_client.invoke(
        FunctionName=deps.settings.ingest_function_name,
        InvocationType="Event",  # async: returns immediately, Lambda retries on failure
        Payload=json.dumps({"reprocess": True, "owner_id": owner, "document_id": doc_id}),
    )
    return 202, {"accepted": True}


def download_url(deps: AppDeps, owner: str, doc_id: str) -> tuple[int, dict[str, Any]]:
    rec = _require(deps, owner, doc_id)
    if rec.status == DocumentStatus.DELETING:
        raise ApiError(404, "NOT_FOUND", "Document not found")
    url = deps.s3.generate_presigned_url(
        "get_object",
        Params={
            "Bucket": deps.settings.docs_bucket,
            "Key": object_key(owner, doc_id),
            "ResponseContentType": "application/pdf",
            "ResponseContentDisposition": "inline",
        },
        ExpiresIn=deps.settings.upload_url_ttl_seconds,
    )
    return 200, {"url": url, "expires_in": deps.settings.upload_url_ttl_seconds}


def handle(event: dict[str, Any], context: Any, deps: AppDeps) -> dict[str, Any]:
    def work(_request_id: str) -> tuple[int, dict[str, Any]]:
        owner = owner_from_event(event)
        route = event.get("routeKey", "")
        if route == "POST /documents/upload-url":
            return create_upload_url(deps, owner, event)
        if route == "GET /documents":
            return 200, {"documents": [r.to_public() for r in deps.repo.list_for_owner(owner)]}
        if route == "GET /documents/{document_id}":
            return 200, _require(deps, owner, _doc_id(event)).to_public()
        if route == "DELETE /documents/{document_id}":
            return delete_document(deps, owner, _doc_id(event))
        if route == "POST /documents/{document_id}/reprocess":
            return reprocess(deps, owner, _doc_id(event))
        if route == "GET /documents/{document_id}/download-url":
            return download_url(deps, owner, _doc_id(event))
        raise ApiError(404, "NOT_FOUND", "Route not found")

    return guarded(event, context, work)


def lambda_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    return handle(event, context, build_deps())
