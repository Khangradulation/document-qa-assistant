"""Bedrock embeddings (Titan Text Embeddings V2).

The SAME model + dimension must be used for chunks and questions, otherwise
the vectors live in different "spaces" and distances are meaningless.
"""

from __future__ import annotations

import json
import random
import time
from collections import deque
from collections.abc import Callable
from typing import Any

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

_THROTTLE_CODES = {"ThrottlingException", "TooManyRequestsException"}


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


class RateLimiter:
    """Sliding-window limiter: at most `max_calls` per `window` seconds.

    Titan V2 has no batch endpoint (one InvokeModel = one text), so "batching" means
    pacing the calls to stay under the account quota (60/min) with some headroom.
    """

    def __init__(
        self,
        max_calls: int,
        window: float = 60.0,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if max_calls < 1:
            raise ValueError("max_calls must be >= 1")
        self._max = max_calls
        self._window = window
        self._clock = clock
        self._sleep = sleep
        self._calls: deque[float] = deque()

    def wait(self) -> None:
        while True:
            now = self._clock()
            while self._calls and now - self._calls[0] >= self._window:
                self._calls.popleft()
            if len(self._calls) < self._max:
                self._calls.append(now)
                return
            self._sleep(self._window - (now - self._calls[0]))


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
        """Pace every call, and back off (exponential + jitter) on throttling."""
        for attempt in range(self._max_retries + 1):
            self._limiter.wait()
            try:
                return self._client.invoke_model(
                    modelId=self._model_id,
                    body=body,
                    contentType="application/json",
                    accept="application/json",
                )
            except ClientError as exc:
                code = exc.response.get("Error", {}).get("Code", "")
                if code not in _THROTTLE_CODES:
                    raise
                if attempt == self._max_retries:
                    raise  # callers map throttling to 429 / Lambda retry
                self.throttle_retries += 1
                self._sleep(min(30.0, 2.0**attempt) * (0.5 + random.random() / 2))  # noqa: S311 (jitter, not security)
        raise AssertionError("unreachable")

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
