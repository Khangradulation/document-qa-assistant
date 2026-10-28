"""Thin wrapper over the Amazon S3 Vectors API (boto3 client 's3vectors').

Each vector = key + float32 data + metadata. The metadata is what lets a
search result be turned back into "page 7 of report.pdf".

NOTE: the index must declare "text" as a NON-filterable metadata key
(Terraform, Phase 4) because filterable metadata has a small size cap and
chunk text is several KB. Non-filterable keys can still be returned.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import boto3

from docqa.models import Chunk, RetrievedChunk

PUT_BATCH = 100  # conservative; the docs page we checked states no exact limit
DELETE_BATCH = 100


def make_s3vectors_client(region: str) -> Any:
    return boto3.client("s3vectors", region_name=region)


class VectorStore:
    def __init__(self, client: Any, bucket: str, index: str) -> None:
        self._client = client
        self._bucket = bucket
        self._index = index

    def put_chunks(
        self,
        chunks: Sequence[Chunk],
        vectors: Sequence[Sequence[float]],
        *,
        filename: str,
        owner_id: str,
    ) -> None:
        if len(chunks) != len(vectors):
            raise ValueError("chunks and vectors must be the same length")
        items = [
            {
                "key": c.vector_key,
                "data": {"float32": list(v)},
                "metadata": {
                    "document_id": c.document_id,
                    "owner_id": owner_id,
                    "filename": filename,
                    "page": c.page,
                    "chunk_id": c.chunk_id,
                    "text": c.text,
                },
            }
            for c, v in zip(chunks, vectors, strict=True)
        ]
        for i in range(0, len(items), PUT_BATCH):
            self._client.put_vectors(
                vectorBucketName=self._bucket,
                indexName=self._index,
                vectors=items[i : i + PUT_BATCH],
            )

    def query(
        self, vector: Sequence[float], *, document_id: str, owner_id: str, top_k: int
    ) -> list[RetrievedChunk]:
        # Both conditions are enforced by the *server*, so one user's question
        # can never retrieve another user's (or another document's) chunks.
        response = self._client.query_vectors(
            vectorBucketName=self._bucket,
            indexName=self._index,
            topK=top_k,
            queryVector={"float32": list(vector)},
            filter={
                "$and": [
                    {"document_id": {"$eq": document_id}},
                    {"owner_id": {"$eq": owner_id}},
                ]
            },
            returnMetadata=True,
            returnDistance=True,
        )
        results: list[RetrievedChunk] = []
        for item in response.get("vectors", []):
            meta = item.get("metadata") or {}
            try:
                chunk = Chunk(
                    document_id=str(meta["document_id"]),
                    chunk_id=str(meta["chunk_id"]),
                    page=int(meta["page"]),
                    text=str(meta["text"]),
                )
                results.append(
                    RetrievedChunk(
                        chunk=chunk,
                        filename=str(meta.get("filename", "")),
                        distance=float(item["distance"]),
                    )
                )
            except KeyError, TypeError, ValueError:
                continue  # malformed entry: skip rather than cite garbage
        results.sort(key=lambda r: r.distance)
        return results

    def delete_keys(self, keys: Sequence[str]) -> None:
        for i in range(0, len(keys), DELETE_BATCH):
            self._client.delete_vectors(
                vectorBucketName=self._bucket,
                indexName=self._index,
                keys=list(keys[i : i + DELETE_BATCH]),
            )
