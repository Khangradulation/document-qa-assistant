"""In-memory stand-ins for AWS clients. They mimic only the request/response
shapes we verified in the boto3 docs, so unit tests need no AWS account."""

from __future__ import annotations

import hashlib
import io
import json
import math
from typing import Any

from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas


def make_pdf(pages: list[str]) -> bytes:
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    for text in pages:
        if text:
            c.drawString(50, 780, text)
        c.showPage()
    c.save()
    return buf.getvalue()


def fake_vector(text: str, dims: int) -> list[float]:
    """Deterministic pseudo-embedding: bag-of-words hashed into `dims` buckets,
    L2-normalised. Texts sharing words end up close together (cosine)."""
    v = [0.0] * dims
    for w in text.lower().split():
        h = int(hashlib.md5(w.encode()).hexdigest(), 16)  # noqa: S324
        v[h % dims] += 1.0
    norm = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / norm for x in v]


class FakeBedrock:
    def __init__(self, dims: int = 64, reply: str = "{}") -> None:
        self.dims = dims
        self.reply = reply
        self.embed_calls: list[dict[str, Any]] = []
        self.converse_calls: list[dict[str, Any]] = []
        self.bad_dims = False

    def invoke_model(self, **kw: Any) -> dict[str, Any]:
        body = json.loads(kw["body"])
        self.embed_calls.append(body)
        n = self.dims - 1 if self.bad_dims else self.dims
        vec = fake_vector(body["inputText"], self.dims)[:n]
        payload = json.dumps({"embedding": vec, "inputTextTokenCount": 7}).encode()
        return {"body": io.BytesIO(payload)}

    def converse(self, **kw: Any) -> dict[str, Any]:
        self.converse_calls.append(kw)
        return {
            "output": {"message": {"content": [{"text": self.reply}]}},
            "usage": {"inputTokens": 100, "outputTokens": 20},
        }


class FakeS3Vectors:
    """Stores vectors in a dict; query does cosine distance + the $and/$eq filter."""

    def __init__(self) -> None:
        self.items: dict[str, dict[str, Any]] = {}
        self.put_calls = 0

    def put_vectors(self, **kw: Any) -> dict[str, Any]:
        self.put_calls += 1
        for v in kw["vectors"]:
            self.items[v["key"]] = v  # same key overwrites => idempotent
        return {}

    def delete_vectors(self, **kw: Any) -> dict[str, Any]:
        for k in kw["keys"]:
            self.items.pop(k, None)
        return {}

    def query_vectors(self, **kw: Any) -> dict[str, Any]:
        conds = kw["filter"]["$and"]
        want = {next(iter(c)): next(iter(c.values()))["$eq"] for c in conds}
        q = kw["queryVector"]["float32"]
        scored = []
        for key, v in self.items.items():
            if any(v["metadata"].get(k) != val for k, val in want.items()):
                continue
            d = v["data"]["float32"]
            dist = 1 - sum(a * b for a, b in zip(q, d, strict=True))
            scored.append({"key": key, "distance": dist, "metadata": v["metadata"]})
        scored.sort(key=lambda r: r["distance"])
        return {"vectors": scored[: kw["topK"]], "distanceMetric": "cosine"}


# ---- Phase 3 fakes -----------------------------------------------------------
from botocore.exceptions import ClientError  # noqa: E402


def _cond_failed() -> ClientError:
    return ClientError({"Error": {"Code": "ConditionalCheckFailedException"}}, "x")


class FakeTable:
    """DynamoDB table keyed by (owner_id, document_id). Evaluates ONLY the
    condition shapes repo.py produces (status IN :f*, optional stale takeover)."""

    def __init__(self) -> None:
        self.items: dict[tuple[str, str], dict[str, Any]] = {}

    def put_item(self, Item: dict[str, Any], ConditionExpression: str = "") -> None:  # noqa: N803
        k = (Item["owner_id"], Item["document_id"])
        if ConditionExpression and k in self.items:
            raise _cond_failed()
        self.items[k] = dict(Item)

    def get_item(self, Key: dict[str, str]) -> dict[str, Any]:  # noqa: N803
        item = self.items.get((Key["owner_id"], Key["document_id"]))
        return {"Item": dict(item)} if item else {}

    def query(self, ExpressionAttributeValues: dict[str, Any], **_: Any) -> dict[str, Any]:  # noqa: N803
        owner = ExpressionAttributeValues[":o"]
        return {"Items": [dict(v) for (o, _d), v in self.items.items() if o == owner]}

    def update_item(
        self,
        Key: dict[str, str],
        ConditionExpression: str,
        ExpressionAttributeValues: dict[str, Any],
        **_: Any,
    ) -> None:  # noqa: N803,E501
        k = (Key["owner_id"], Key["document_id"])
        item = self.items.get(k)
        vals = ExpressionAttributeValues
        if item is None:
            raise _cond_failed()
        allowed = {v for name, v in vals.items() if name.startswith(":f")}
        ok = item["status"] in allowed
        if not ok and ":stale" in vals:
            ok = item["status"] == vals[":processing"] and item["updated_at"] < vals[":stale"]
        if not ok:
            raise _cond_failed()
        for name, v in vals.items():
            if name.startswith(":v_"):
                item[name[3:]] = v

    def delete_item(self, Key: dict[str, str]) -> None:  # noqa: N803
        self.items.pop((Key["owner_id"], Key["document_id"]), None)


class FakeS3:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
        self.presigned: list[tuple[str, dict[str, Any], int]] = []

    def head_object(self, Bucket: str, Key: str) -> dict[str, Any]:  # noqa: N803
        data = self.objects[Key]
        return {"ContentLength": len(data), "ETag": '"etag-' + str(len(data)) + '"'}

    def get_object(self, Bucket: str, Key: str) -> dict[str, Any]:  # noqa: N803
        return {"Body": io.BytesIO(self.objects[Key])}

    def delete_object(self, Bucket: str, Key: str) -> None:  # noqa: N803
        self.objects.pop(Key, None)

    def generate_presigned_url(self, op: str, Params: dict[str, Any], ExpiresIn: int) -> str:  # noqa: N803
        self.presigned.append((op, Params, ExpiresIn))
        return f"https://s3.example/{Params['Key']}?sig=x"


class FakeLambda:
    def __init__(self) -> None:
        self.invocations: list[dict[str, Any]] = []

    def invoke(self, **kw: Any) -> dict[str, Any]:
        self.invocations.append(kw)
        return {"StatusCode": 202}
