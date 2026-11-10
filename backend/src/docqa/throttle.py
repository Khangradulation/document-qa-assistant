"""Shared client-side pacing and throttle retry for Bedrock calls."""

from __future__ import annotations

import random
import time
from collections import deque
from collections.abc import Callable
from typing import Any

from botocore.exceptions import ClientError

THROTTLE_CODES = {"ThrottlingException", "TooManyRequestsException"}


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


def call_with_backoff(
    call: Callable[[], Any],
    limiter: RateLimiter | None,
    max_retries: int,
    sleep: Callable[[float], None],
    on_retry: Callable[[], None],
) -> Any:
    """Pace each attempt, and back off (exponential + jitter) on throttling.
    After the last retry the original error is re-raised (callers map it to 429 / Lambda retry)."""
    for attempt in range(max_retries + 1):
        if limiter is not None:
            limiter.wait()
        try:
            return call()
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code", "")
            if code not in THROTTLE_CODES or attempt == max_retries:
                raise
            on_retry()
            sleep(min(30.0, 2.0**attempt) * (0.5 + random.random() / 2))  # noqa: S311 (jitter)
    raise AssertionError("unreachable")
