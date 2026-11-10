"""Bedrock embeddings (Titan Text Embeddings V2).

The SAME model + dimension must be used for chunks and questions, otherwise
the vectors live in different "spaces" and distances are meaningless.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from typing import Any

import boto3
from botocore.config import Config

from docqa.throttle import RateLimiter, call_with_backoff


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
    def __init__(
        self,
        client: Any,
        model_id: str,
        dimensions: int,
        *,
        requests_per_minute: int = 50,
        max_retries: int = 6,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._client = client
        self._model_id = model_id
        self._dimensions = dimensions
        self._limiter = RateLimiter(requests_per_minute, clock=clock, sleep=sleep)
        self._max_retries = max_retries
        self._sleep = sleep
        self.input_tokens = 0  # running total, for cost visibility
        self.throttle_retries = 0

    def _invoke(self, body: str) -> Any:
        def call() -> Any:
            return self._client.invoke_model(
                modelId=self._model_id,
                body=body,
                contentType="application/json",
                accept="application/json",
            )

        return call_with_backoff(
            call, self._limiter, self._max_retries, self._sleep, self._count_retry
        )

    def _count_retry(self) -> None:
        self.throttle_retries += 1

    def embed_many(self, texts: list[str]) -> list[list[float]]:
        return [self.embed(t) for t in texts]

    def embed(self, text: str) -> list[float]:
        if not text.strip():
            raise EmbeddingError("EMPTY_INPUT", "Cannot embed empty text")
        body = json.dumps({"inputText": text, "dimensions": self._dimensions, "normalize": True})
        response = self._invoke(body)
        payload = json.loads(response["body"].read())
        vector = payload.get("embedding")
        if not isinstance(vector, list) or len(vector) != self._dimensions:
            raise EmbeddingError(
                "EMBEDDING_DIMENSION_MISMATCH",
                f"Expected {self._dimensions} dimensions from {self._model_id}",
            )
        self.input_tokens += int(payload.get("inputTextTokenCount", 0))
        return [float(x) for x in vector]
