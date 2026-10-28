"""Bedrock embeddings (Titan Text Embeddings V2).

The SAME model + dimension must be used for chunks and questions, otherwise
the vectors live in different "spaces" and distances are meaningless.
"""

from __future__ import annotations

import json
from typing import Any

import boto3
from botocore.config import Config


class EmbeddingError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def make_bedrock_client(region: str) -> Any:
    """Bounded timeouts + bounded retries (botocore 'standard' mode backs off)."""
    return boto3.client(
        "bedrock-runtime",
        region_name=region,
        config=Config(
            retries={"max_attempts": 3, "mode": "standard"},
            connect_timeout=5,
            read_timeout=30,
        ),
    )


class Embedder:
    def __init__(self, client: Any, model_id: str, dimensions: int) -> None:
        self._client = client
        self._model_id = model_id
        self._dimensions = dimensions
        self.input_tokens = 0  # running total, for cost visibility

    def embed(self, text: str) -> list[float]:
        if not text.strip():
            raise EmbeddingError("EMPTY_INPUT", "Cannot embed empty text")
        body = json.dumps({"inputText": text, "dimensions": self._dimensions, "normalize": True})
        response = self._client.invoke_model(
            modelId=self._model_id,
            body=body,
            contentType="application/json",
            accept="application/json",
        )
        payload = json.loads(response["body"].read())
        vector = payload.get("embedding")
        if not isinstance(vector, list) or len(vector) != self._dimensions:
            raise EmbeddingError(
                "EMBEDDING_DIMENSION_MISMATCH",
                f"Expected {self._dimensions} dimensions from {self._model_id}",
            )
        self.input_tokens += int(payload.get("inputTextTokenCount", 0))
        return [float(x) for x in vector]
