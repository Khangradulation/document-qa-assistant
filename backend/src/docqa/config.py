"""Runtime configuration, read from environment variables.

Nothing environment-specific (bucket names, model IDs, Region) is hard-coded:
Terraform injects these as Lambda environment variables, and locally you can
export them (see .env.example). Defaults below are only for *tuning knobs*.
"""

from __future__ import annotations

import os
from dataclasses import dataclass


def _int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    return default if raw is None or raw == "" else int(raw)


def _float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    return default if raw is None or raw == "" else float(raw)


@dataclass(frozen=True)
class Settings:
    # --- Infrastructure names (injected by Terraform) ---
    region: str = "us-east-1"
    docs_bucket: str = ""
    vector_bucket: str = ""
    vector_index: str = ""
    table_name: str = ""
    allowed_origin: str = ""  # frontend origin for CORS
    ingest_function_name: str = ""  # used by the API to trigger reprocessing

    # --- Models ---
    embed_model_id: str = "amazon.titan-embed-text-v2:0"
    embed_dimensions: int = 1024  # Titan V2 accepts 256 / 512 / 1024
    generation_model_id: str = ""  # must be chosen & enabled in your account

    # --- Retrieval ---
    top_k: int = 5
    # S3 Vectors returns a *distance* (lower = closer). Chunks farther than
    # this are dropped before the model sees them. A LOOSE pre-filter on purpose: too strict
    # silently turns every question into "not found", too loose only costs a model call that
    # should refuse. The model + citation check make the real call.
    # Tune with: python eval/run_eval.py --mode live --sweep
    max_distance: float = 0.9

    # --- Chunking (approximate tokens; see chunking.py) ---
    chunk_tokens: int = 800
    chunk_overlap_tokens: int = 100

    # --- Limits ---
    max_pdf_bytes: int = 10 * 1024 * 1024
    max_pages: int = 200
    max_chunks: int = 1000  # bounds embedding cost and ingest time
    max_question_chars: int = 1000
    upload_url_ttl_seconds: int = 300
    max_docs_per_user: int = 20  # cost guard
    processing_lease_seconds: int = 900  # a PROCESSING doc older than this may be taken over

    @classmethod
    def from_env(cls) -> Settings:
        e = os.environ.get
        return cls(
            region=e("AWS_REGION", cls.region),
            docs_bucket=e("DOCS_BUCKET", ""),
            vector_bucket=e("VECTOR_BUCKET", ""),
            vector_index=e("VECTOR_INDEX", ""),
            table_name=e("TABLE_NAME", ""),
            allowed_origin=e("ALLOWED_ORIGIN", ""),
            ingest_function_name=e("INGEST_FUNCTION_NAME", ""),
            embed_model_id=e("EMBED_MODEL_ID", cls.embed_model_id),
            embed_dimensions=_int("EMBED_DIMENSIONS", cls.embed_dimensions),
            generation_model_id=e("GENERATION_MODEL_ID", ""),
            top_k=_int("TOP_K", cls.top_k),
            max_distance=_float("MAX_DISTANCE", cls.max_distance),
            chunk_tokens=_int("CHUNK_TOKENS", cls.chunk_tokens),
            chunk_overlap_tokens=_int("CHUNK_OVERLAP_TOKENS", cls.chunk_overlap_tokens),
            max_pdf_bytes=_int("MAX_PDF_BYTES", cls.max_pdf_bytes),
            max_pages=_int("MAX_PAGES", cls.max_pages),
            max_chunks=_int("MAX_CHUNKS", cls.max_chunks),
            max_question_chars=_int("MAX_QUESTION_CHARS", cls.max_question_chars),
            upload_url_ttl_seconds=_int("UPLOAD_URL_TTL_SECONDS", cls.upload_url_ttl_seconds),
            max_docs_per_user=_int("MAX_DOCS_PER_USER", cls.max_docs_per_user),
            processing_lease_seconds=_int("PROCESSING_LEASE_SECONDS", cls.processing_lease_seconds),
        )

    def validate(self) -> None:
        if self.embed_dimensions not in (256, 512, 1024):
            raise ValueError("EMBED_DIMENSIONS must be 256, 512 or 1024 for Titan V2")
        if not 1 <= self.top_k <= 30:
            raise ValueError("TOP_K must be between 1 and 30")
        if self.chunk_overlap_tokens >= self.chunk_tokens:
            raise ValueError("CHUNK_OVERLAP_TOKENS must be smaller than CHUNK_TOKENS")
