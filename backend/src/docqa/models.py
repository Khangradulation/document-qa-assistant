"""Typed request/response contracts and the citation data model.

We use plain dataclasses + small validators instead of a framework: fewer
dependencies, nothing compiled to package for Lambda, and easy to read.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Any


class ValidationError(ValueError):
    """Raised for bad client input. The API layer maps this to HTTP 400."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class DocumentStatus(StrEnum):
    UPLOADED = "UPLOADED"
    PROCESSING = "PROCESSING"
    READY = "READY"
    FAILED = "FAILED"
    DELETING = "DELETING"


# Allowed transitions. FAILED/READY -> PROCESSING enables safe reprocessing.
ALLOWED_TRANSITIONS: dict[DocumentStatus, set[DocumentStatus]] = {
    DocumentStatus.UPLOADED: {DocumentStatus.PROCESSING, DocumentStatus.DELETING},
    DocumentStatus.PROCESSING: {
        DocumentStatus.READY,
        DocumentStatus.FAILED,
        DocumentStatus.DELETING,
    },
    DocumentStatus.READY: {DocumentStatus.PROCESSING, DocumentStatus.DELETING},
    DocumentStatus.FAILED: {DocumentStatus.PROCESSING, DocumentStatus.DELETING},
    DocumentStatus.DELETING: set(),
}

_DOC_ID_RE = re.compile(r"^[0-9a-f]{32}$")
_FILENAME_RE = re.compile(r"[^A-Za-z0-9._ -]")


def is_valid_document_id(value: str) -> bool:
    return bool(_DOC_ID_RE.match(value))


def sanitize_filename(name: str) -> str:
    """Keep only safe characters; never trust a client filename as a path."""
    base = name.replace("\\", "/").split("/")[-1]
    cleaned = _FILENAME_RE.sub("_", base).strip(" .")
    return cleaned[:100]


@dataclass(frozen=True)
class UploadUrlRequest:
    filename: str
    content_type: str

    @classmethod
    def parse(cls, body: dict[str, Any]) -> UploadUrlRequest:
        filename = body.get("filename")
        content_type = body.get("content_type")
        if not isinstance(filename, str) or not filename:
            raise ValidationError("INVALID_FILENAME", "filename is required")
        safe = sanitize_filename(filename)
        if not safe.lower().endswith(".pdf") or len(safe) < 5:
            raise ValidationError("INVALID_FILENAME", "filename must end in .pdf")
        if content_type != "application/pdf":
            raise ValidationError("INVALID_CONTENT_TYPE", "content_type must be application/pdf")
        return cls(filename=safe, content_type=content_type)


@dataclass(frozen=True)
class QueryRequest:
    document_id: str
    question: str

    @classmethod
    def parse(cls, body: dict[str, Any], max_question_chars: int) -> QueryRequest:
        document_id = body.get("document_id")
        question = body.get("question")
        if not isinstance(document_id, str) or not is_valid_document_id(document_id):
            raise ValidationError("INVALID_DOCUMENT_ID", "document_id is invalid")
        if not isinstance(question, str) or not question.strip():
            raise ValidationError("INVALID_QUESTION", "question is required")
        question = question.strip()
        if len(question) > max_question_chars:
            raise ValidationError(
                "QUESTION_TOO_LONG", f"question must be at most {max_question_chars} characters"
            )
        return cls(document_id=document_id, question=question)


@dataclass(frozen=True)
class Chunk:
    """A piece of text from ONE page. Page number is never lost or merged."""

    document_id: str
    chunk_id: str  # deterministic, e.g. "p003-c001"
    page: int  # 1-based original PDF page
    text: str

    @property
    def vector_key(self) -> str:
        return f"{self.document_id}:{self.chunk_id}"


@dataclass(frozen=True)
class RetrievedChunk:
    chunk: Chunk
    filename: str
    distance: float


@dataclass(frozen=True)
class Citation:
    chunk_id: str
    filename: str
    page: int
    excerpt: str


@dataclass
class QueryResponse:
    request_id: str
    answer: str
    answered: bool  # False => "not found in the provided documents"
    citations: list[Citation] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class DocumentRecord:
    document_id: str
    owner_id: str
    filename: str
    status: DocumentStatus
    created_at: str
    updated_at: str
    error_code: str | None = None
    error_detail: str | None = None
    page_count: int | None = None
    chunk_count: int | None = None
    # Internal-only (never returned by the API):
    source_etag: str | None = None  # S3 ETag of the version last indexed
    chunks_per_page: list[int] = field(default_factory=list)  # to rebuild vector keys

    def to_public(self) -> dict[str, Any]:
        """What the API returns. Never includes owner_id or S3 keys."""
        d = asdict(self)
        for internal in ("owner_id", "source_etag", "chunks_per_page"):
            d.pop(internal)
        d["status"] = str(self.status)
        return d
