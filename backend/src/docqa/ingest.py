"""The ingestion workflow: S3 object -> chunks -> embeddings -> vectors -> READY.

Safety properties (each is covered by a test):
 * Duplicate S3 events: a READY doc whose ETag we already indexed is skipped.
 * Concurrent runs: only one worker can "claim" a doc (conditional write).
 * READY is set LAST, after every vector is written.
 * Re-upload with fewer chunks: stale vectors are deleted by rebuilding their
   keys from the stored per-page counts.
 * Doc deleted mid-run: mark_ready fails, so we remove what we just wrote.
 * Permanent problems (bad PDF) -> FAILED, no retry. Transient problems
   (throttling, timeouts) -> FAILED but the exception is re-raised so Lambda's
   built-in async retry runs the job again (FAILED -> PROCESSING is allowed).
"""

from __future__ import annotations

import re

from docqa.chunking import chunk_pages, chunks_per_page, keys_from_counts
from docqa.deps import AppDeps
from docqa.logs import emit_metric, log
from docqa.pdf import PdfError, extract_pages

# The backend generates this key itself; we only ever PARSE keys of this shape.
KEY_RE = re.compile(r"^uploads/(?P<owner>[A-Za-z0-9-]{1,64})/(?P<doc>[0-9a-f]{32})\.pdf$")


def object_key(owner_id: str, document_id: str) -> str:
    return f"uploads/{owner_id}/{document_id}.pdf"


def ingest_document(
    deps: AppDeps, owner_id: str, document_id: str, *, etag: str, force: bool
) -> str:
    s, repo = deps.settings, deps.repo
    rec = repo.get(owner_id, document_id)
    if rec is None or rec.status == "DELETING":
        return "ignored"
    if not force and rec.status == "READY" and rec.source_etag == etag:
        return "duplicate"
    if not repo.claim_processing(owner_id, document_id, s.processing_lease_seconds):
        return "busy"  # another worker holds it

    key = object_key(owner_id, document_id)
    new_keys: list[str] = []
    try:
        head = deps.s3.head_object(Bucket=s.docs_bucket, Key=key)
        if head["ContentLength"] > s.max_pdf_bytes:
            deps.s3.delete_object(Bucket=s.docs_bucket, Key=key)  # don't keep oversize files
            raise PdfError("FILE_TOO_LARGE", "PDF is larger than the allowed size")
        data = deps.s3.get_object(Bucket=s.docs_bucket, Key=key)["Body"].read()
        pages = extract_pages(data, s.max_pdf_bytes, s.max_pages)
        chunks = chunk_pages(document_id, pages, s.chunk_tokens, s.chunk_overlap_tokens)
        if len(chunks) > s.max_chunks:  # checked BEFORE any paid embedding call
            raise PdfError(
                "DOCUMENT_TOO_LARGE", f"Document has more than {s.max_chunks} text chunks"
            )
        vectors = [deps.embedder.embed(c.text) for c in chunks]
        new_keys = [c.vector_key for c in chunks]

        deps.store.put_chunks(chunks, vectors, filename=rec.filename, owner_id=owner_id)

        stale = set(keys_from_counts(document_id, rec.chunks_per_page)) - set(new_keys)
        if stale:
            deps.store.delete_keys(sorted(stale))

        counts = chunks_per_page(chunks)
        if not repo.mark_ready(
            owner_id, document_id, page_count=len(pages), counts=counts, etag=etag
        ):
            deps.store.delete_keys(new_keys)  # deleted while we were working
            return "superseded"
        emit_metric("IngestionSucceeded", 1)
        emit_metric("ChunksIndexed", len(chunks))
        log("INFO", "ingest_ready", document_id=document_id, chunks=len(chunks))
        return "ready"
    except PdfError as exc:  # permanent: the file itself is the problem
        repo.mark_failed(owner_id, document_id, exc.code, exc.message)
        emit_metric("IngestionFailed", 1)
        log("WARNING", "ingest_rejected", document_id=document_id, error_code=exc.code)
        return "failed"
    except Exception as exc:  # transient or unexpected: record, clean up, let Lambda retry
        if new_keys:
            try:
                deps.store.delete_keys(new_keys)
            except Exception:
                log("ERROR", "ingest_cleanup_failed", document_id=document_id)
        repo.mark_failed(owner_id, document_id, "INGESTION_ERROR", "Processing failed")
        emit_metric("IngestionFailed", 1)
        log("ERROR", "ingest_error", document_id=document_id, error_type=type(exc).__name__)
        raise
