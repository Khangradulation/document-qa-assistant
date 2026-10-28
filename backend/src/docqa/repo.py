"""DynamoDB access for document records.

Table design: partition key `owner_id`, sort key `document_id`.
 * Listing "my documents" is one Query on the partition: cheap and naturally
   owner-scoped (you cannot even express "someone else's documents").
 * Every status change is a CONDITIONAL write ("only if status is currently X").
   DynamoDB checks and writes atomically, so two Lambdas racing on the same
   document cannot both win. This is how we get safe state transitions.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from botocore.exceptions import ClientError

from docqa.models import ALLOWED_TRANSITIONS, DocumentRecord, DocumentStatus


def utc_now() -> str:
    # Fixed-width ISO-8601, so plain string comparison orders correctly.
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _is_conditional_failure(exc: ClientError) -> bool:
    return bool(exc.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException")


def _to_item(rec: DocumentRecord) -> dict[str, Any]:
    item: dict[str, Any] = {
        "owner_id": rec.owner_id,
        "document_id": rec.document_id,
        "filename": rec.filename,
        "status": str(rec.status),
        "created_at": rec.created_at,
        "updated_at": rec.updated_at,
        "chunks_per_page": rec.chunks_per_page,
    }
    for k in ("error_code", "error_detail", "page_count", "chunk_count", "source_etag"):
        if getattr(rec, k) is not None:
            item[k] = getattr(rec, k)
    return item


def _from_item(item: dict[str, Any]) -> DocumentRecord:
    def opt_int(k: str) -> int | None:
        return int(item[k]) if item.get(k) is not None else None  # DynamoDB gives Decimal

    return DocumentRecord(
        document_id=item["document_id"],
        owner_id=item["owner_id"],
        filename=item["filename"],
        status=DocumentStatus(item["status"]),
        created_at=item["created_at"],
        updated_at=item["updated_at"],
        error_code=item.get("error_code"),
        error_detail=item.get("error_detail"),
        page_count=opt_int("page_count"),
        chunk_count=opt_int("chunk_count"),
        source_etag=item.get("source_etag"),
        chunks_per_page=[int(x) for x in item.get("chunks_per_page", [])],
    )


class DocumentRepo:
    def __init__(self, table: Any, now: Callable[[], str] = utc_now) -> None:
        self._table = table
        self._now = now

    # ---- create / read ----------------------------------------------------
    def create(self, rec: DocumentRecord) -> None:
        self._table.put_item(
            Item=_to_item(rec), ConditionExpression="attribute_not_exists(document_id)"
        )

    def get(self, owner_id: str, document_id: str) -> DocumentRecord | None:
        resp = self._table.get_item(Key={"owner_id": owner_id, "document_id": document_id})
        item = resp.get("Item")
        return _from_item(item) if item else None

    def list_for_owner(self, owner_id: str) -> list[DocumentRecord]:
        resp = self._table.query(
            KeyConditionExpression="owner_id = :o", ExpressionAttributeValues={":o": owner_id}
        )
        recs = [_from_item(i) for i in resp.get("Items", [])]
        return sorted(recs, key=lambda r: r.created_at, reverse=True)

    # ---- conditional transitions -----------------------------------------
    def _update(
        self,
        owner_id: str,
        document_id: str,
        to: DocumentStatus,
        fields: dict[str, Any],
        *,
        stale_before: str | None = None,
    ) -> bool:
        """Move to `to` only from states the state machine allows."""
        from_states = [s for s, targets in ALLOWED_TRANSITIONS.items() if to in targets]
        values: dict[str, Any] = {f":f{i}": str(s) for i, s in enumerate(from_states)}
        condition = f"#status IN ({','.join(values)})"
        if stale_before is not None:
            # Lease takeover: a PROCESSING doc untouched for a long time means
            # the previous worker crashed; allow a new worker to claim it.
            condition += " OR (#status = :processing AND updated_at < :stale)"
            values[":processing"] = str(DocumentStatus.PROCESSING)
            values[":stale"] = stale_before
        all_fields = {"status": str(to), **fields, "updated_at": self._now()}
        sets = []
        for k, v in all_fields.items():
            values[f":v_{k}"] = v
            sets.append(f"{'#status' if k == 'status' else k} = :v_{k}")
        try:
            self._table.update_item(
                Key={"owner_id": owner_id, "document_id": document_id},
                UpdateExpression="SET " + ", ".join(sets),
                ConditionExpression=condition,
                ExpressionAttributeNames={"#status": "status"},
                ExpressionAttributeValues=values,
            )
            return True
        except ClientError as exc:
            if _is_conditional_failure(exc):
                return False
            raise

    def claim_processing(self, owner_id: str, document_id: str, lease_seconds: int) -> bool:
        cutoff = (datetime.now(UTC) - timedelta(seconds=lease_seconds)).strftime(
            "%Y-%m-%dT%H:%M:%S.%fZ"
        )
        return self._update(
            owner_id,
            document_id,
            DocumentStatus.PROCESSING,
            {"error_code": None, "error_detail": None},
            stale_before=cutoff,
        )

    def mark_ready(
        self, owner_id: str, document_id: str, *, page_count: int, counts: list[int], etag: str
    ) -> bool:
        return self._update(
            owner_id,
            document_id,
            DocumentStatus.READY,
            {
                "page_count": page_count,
                "chunk_count": sum(counts),
                "chunks_per_page": counts,
                "source_etag": etag,
            },
        )

    def mark_failed(self, owner_id: str, document_id: str, code: str, detail: str) -> bool:
        return self._update(
            owner_id,
            document_id,
            DocumentStatus.FAILED,
            {"error_code": code, "error_detail": detail[:200]},
        )

    def begin_delete(self, owner_id: str, document_id: str) -> bool:
        return self._update(owner_id, document_id, DocumentStatus.DELETING, {})

    def delete(self, owner_id: str, document_id: str) -> None:
        self._table.delete_item(Key={"owner_id": owner_id, "document_id": document_id})
