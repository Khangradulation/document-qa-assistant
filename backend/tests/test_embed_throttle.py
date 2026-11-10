"""Embedding pacing (<= N req/min) and retry on ThrottlingException. No AWS needed."""

from __future__ import annotations

from typing import Any

import pytest
from botocore.exceptions import ClientError
from fakes import FakeBedrock

from docqa.embeddings import Embedder, RateLimiter


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def time(self) -> float:
        return self.now

    def sleep(self, s: float) -> None:
        self.now += s


def client_error(code: str) -> ClientError:
    return ClientError({"Error": {"Code": code, "Message": "x"}}, "InvokeModel")


def test_limiter_never_exceeds_budget_in_any_window() -> None:
    clock = FakeClock()
    limiter = RateLimiter(5, 60.0, clock=clock.time, sleep=clock.sleep)
    stamps = []
    for _ in range(23):
        limiter.wait()
        stamps.append(clock.now)
    for t in stamps:
        assert sum(1 for u in stamps if t <= u < t + 60.0) <= 5
    assert clock.now >= 3 * 60.0  # 23 calls at 5/min needs >= 4 windows


def test_embedder_paces_calls() -> None:
    clock = FakeClock()
    bedrock = FakeBedrock(dims=8)
    emb = Embedder(bedrock, "m", 8, requests_per_minute=10, sleep=clock.sleep, clock=clock.time)
    out = emb.embed_many([f"text {i}" for i in range(25)])
    assert len(out) == 25 and len(bedrock.embed_calls) == 25
    assert clock.now >= 120.0


def test_embedder_retries_throttling_then_succeeds() -> None:
    clock = FakeClock()
    bedrock = FakeBedrock(dims=8)
    real = bedrock.invoke_model
    left = [2]

    def flaky(**kw: Any) -> Any:
        if left[0]:
            left[0] -= 1
            raise client_error("ThrottlingException")
        return real(**kw)

    bedrock.invoke_model = flaky  # type: ignore[method-assign]
    emb = Embedder(bedrock, "m", 8, sleep=clock.sleep, clock=clock.time)
    assert len(emb.embed("hello")) == 8
    assert emb.throttle_retries == 2


def test_embedder_gives_up_after_max_retries() -> None:
    clock = FakeClock()
    bedrock = FakeBedrock(dims=8)

    def always(**kw: Any) -> Any:
        raise client_error("ThrottlingException")

    bedrock.invoke_model = always  # type: ignore[method-assign]
    emb = Embedder(bedrock, "m", 8, max_retries=3, sleep=clock.sleep, clock=clock.time)
    with pytest.raises(ClientError) as e:
        emb.embed("hello")
    assert e.value.response["Error"]["Code"] == "ThrottlingException"
    assert emb.throttle_retries == 3


def test_non_throttle_errors_are_not_retried() -> None:
    bedrock = FakeBedrock(dims=8)
    calls = [0]

    def denied(**kw: Any) -> Any:
        calls[0] += 1
        raise client_error("AccessDeniedException")

    bedrock.invoke_model = denied  # type: ignore[method-assign]
    with pytest.raises(ClientError):
        Embedder(bedrock, "m", 8).embed("hello")
    assert calls[0] == 1
