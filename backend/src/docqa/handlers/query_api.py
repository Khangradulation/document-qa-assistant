"""Lambda: POST /query. Read-only access to vectors; cannot upload or delete."""

from __future__ import annotations

import time
from typing import Any

from docqa.answer import answer_question
from docqa.deps import AppDeps, build_deps
from docqa.http import ApiError, guarded, owner_from_event, parse_json_body
from docqa.logs import emit_metric, log
from docqa.models import DocumentStatus, QueryRequest


def handle(event: dict[str, Any], context: Any, deps: AppDeps) -> dict[str, Any]:
    def work(request_id: str) -> tuple[int, dict[str, Any]]:
        owner = owner_from_event(event)
        req = QueryRequest.parse(parse_json_body(event), deps.settings.max_question_chars)
        rec = deps.repo.get(owner, req.document_id)
        if rec is None:
            raise ApiError(404, "NOT_FOUND", "Document not found")
        if rec.status != DocumentStatus.READY:
            raise ApiError(409, "DOCUMENT_NOT_READY", f"Document is {rec.status}, not READY")

        gen = deps.generator
        in0, out0 = gen.input_tokens, gen.output_tokens
        started = time.monotonic()
        try:
            resp = answer_question(
                deps.query_deps,
                question=req.question,
                document_id=req.document_id,
                owner_id=owner,
                request_id=request_id,
            )
        except Exception:
            emit_metric("QueryFailures", 1)
            raise
        elapsed_ms = (time.monotonic() - started) * 1000
        emit_metric("QueryLatencyMs", elapsed_ms, "Milliseconds")
        emit_metric("CitationsReturned", len(resp.citations))
        # Token counts only (cost visibility). Never the question or the answer text.
        log(
            "INFO",
            "query_done",
            request_id=request_id,
            answered=resp.answered,
            input_tokens=gen.input_tokens - in0,
            output_tokens=gen.output_tokens - out0,
        )
        body = resp.to_dict()
        body.pop("request_id")  # guarded() adds it
        return 200, body

    return guarded(event, context, work)


def lambda_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    return handle(event, context, build_deps())
