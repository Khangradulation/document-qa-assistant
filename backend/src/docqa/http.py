"""Small helpers shared by the API Lambdas: identity, body parsing, errors."""

from __future__ import annotations

import base64
import json
import re
from collections.abc import Callable
from typing import Any

from botocore.exceptions import ClientError

from docqa.answer import AnswerError
from docqa.embeddings import EmbeddingError
from docqa.logs import emit_metric, log
from docqa.models import ValidationError

MAX_BODY_BYTES = 8 * 1024
_OWNER_RE = re.compile(r"^[A-Za-z0-9-]{1,64}$")
_THROTTLE_CODES = {"ThrottlingException", "TooManyRequestsException", "ThrottledException"}


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


def owner_from_event(event: dict[str, Any]) -> str:
    """The owner is the Cognito `sub` claim that API Gateway has ALREADY verified
    (signature, expiry, audience). The client cannot choose or forge it."""
    try:
        sub = event["requestContext"]["authorizer"]["jwt"]["claims"]["sub"]
    except (KeyError, TypeError) as exc:
        raise ApiError(401, "UNAUTHENTICATED", "Sign-in required") from exc
    if not isinstance(sub, str) or not _OWNER_RE.match(sub):
        raise ApiError(401, "UNAUTHENTICATED", "Sign-in required")
    return sub


def parse_json_body(event: dict[str, Any]) -> dict[str, Any]:
    raw = event.get("body") or ""
    if event.get("isBase64Encoded"):
        try:
            raw = base64.b64decode(raw, validate=True).decode("utf-8", errors="replace")
        except ValueError as exc:  # binascii.Error is a ValueError
            raise ApiError(400, "INVALID_JSON", "Body is not valid base64") from exc
    if len(raw.encode()) > MAX_BODY_BYTES:
        raise ApiError(413, "PAYLOAD_TOO_LARGE", "Request body is too large")
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ApiError(400, "INVALID_JSON", "Body must be valid JSON") from exc
    if not isinstance(data, dict):
        raise ApiError(400, "INVALID_JSON", "Body must be a JSON object")
    return data


def respond(status: int, body: dict[str, Any]) -> dict[str, Any]:
    return {
        "statusCode": status,
        "headers": {"Content-Type": "application/json", "Cache-Control": "no-store"},
        "body": json.dumps(body),
    }


def guarded(
    event: dict[str, Any], context: Any, work: Callable[[str], tuple[int, dict[str, Any]]]
) -> dict[str, Any]:
    """Runs `work(request_id)` and turns every failure into a safe JSON error.
    Internal details go to the log (by type/code only), never to the client."""
    request_id = str(
        (event.get("requestContext") or {}).get("requestId")
        or getattr(context, "aws_request_id", "unknown")
    )

    def fail(status: int, code: str, message: str) -> dict[str, Any]:
        return respond(
            status, {"error": {"code": code, "message": message}, "request_id": request_id}
        )

    try:
        status, body = work(request_id)
        return respond(status, {**body, "request_id": request_id})
    except ApiError as exc:
        return fail(exc.status, exc.code, exc.message)
    except ValidationError as exc:
        return fail(400, exc.code, exc.message)
    except (AnswerError, EmbeddingError) as exc:
        emit_metric("UpstreamFailures", 1)
        log("ERROR", "upstream_error", request_id=request_id, error_code=exc.code)
        return fail(502, "UPSTREAM_ERROR", "The answer service failed. Please try again.")
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code", "Unknown")
        log("ERROR", "aws_error", request_id=request_id, aws_code=code)
        if code in _THROTTLE_CODES:
            return fail(429, "TOO_MANY_REQUESTS", "The service is busy. Please retry shortly.")
        return fail(502, "UPSTREAM_ERROR", "A backend service failed. Please try again.")
    except Exception as exc:
        log("ERROR", "unhandled_error", request_id=request_id, error_type=type(exc).__name__)
        return fail(500, "INTERNAL_ERROR", "Something went wrong.")
