import json
from typing import Any

import pytest
from botocore.exceptions import ClientError
from conftest import World
from fakes import make_pdf

from docqa.handlers import docs_api, ingest_handler, query_api
from docqa.ingest import KEY_RE, ingest_document, object_key

U1, U2 = "user-1", "user-2"
PDF = make_pdf(["lettuce grows in deep water culture", "keep ph between 5.8 and 6.2 nutrient"])


# ---- helpers ---------------------------------------------------------------
def api(route: str, owner: str | None = U1, body: Any = None, doc: str | None = None) -> dict:
    ev: dict[str, Any] = {"routeKey": route, "requestContext": {"requestId": "rid-1"}}
    if owner:
        ev["requestContext"]["authorizer"] = {"jwt": {"claims": {"sub": owner}}}
    if body is not None:
        ev["body"] = body if isinstance(body, str) else json.dumps(body)
    if doc:
        ev["pathParameters"] = {"document_id": doc}
    return ev


def call(world: World, mod: Any, event: dict) -> tuple[int, dict]:
    r = mod.handle(event, None, world.deps)
    return r["statusCode"], json.loads(r["body"])


def upload(world: World, owner: str = U1, data: bytes = PDF) -> str:
    status, body = call(
        world,
        docs_api,
        api(
            "POST /documents/upload-url",
            owner,
            {"filename": "guide.pdf", "content_type": "application/pdf"},
        ),
    )
    assert status == 201
    world.s3.objects[object_key(owner, body["document_id"])] = data
    return str(body["document_id"])


def ingest(world: World, doc: str, owner: str = U1, etag: str = "e1", force: bool = False) -> str:
    return ingest_document(world.deps, owner, doc, etag=etag, force=force)


def record(world: World, doc: str, owner: str = U1):
    return world.deps.repo.get(owner, doc)


def ready_doc(world: World, owner: str = U1) -> str:
    doc = upload(world, owner)
    assert ingest(world, doc, owner) == "ready"
    return doc


def throttle(*_: Any, **__: Any) -> None:
    raise ClientError({"Error": {"Code": "ThrottlingException"}}, "InvokeModel")


# ---- upload URL ------------------------------------------------------------
def test_upload_url_is_scoped_short_lived_and_server_chosen(world: World):
    status, body = call(
        world,
        docs_api,
        api(
            "POST /documents/upload-url",
            body={
                "filename": "../../x.pdf",
                "content_type": "application/pdf",
                "key": "evil/key",
                "bucket": "evil-bucket",
            },
        ),
    )
    assert status == 201 and body["method"] == "PUT" and body["request_id"] == "rid-1"
    op, params, ttl = world.s3.presigned[0]
    assert op == "put_object" and ttl == 300
    assert params["Bucket"] == "docs-bucket" and params["ContentType"] == "application/pdf"
    m = KEY_RE.match(params["Key"])
    assert m and m["owner"] == U1 and m["doc"] == body["document_id"]
    assert record(world, body["document_id"]).filename == "x.pdf"
    assert record(world, body["document_id"]).status == "UPLOADED"


@pytest.mark.parametrize(
    "body",
    [
        {"filename": "a.txt", "content_type": "application/pdf"},
        {"filename": "a.pdf", "content_type": "image/png"},
        "not json",
        "[1,2]",
    ],
)
def test_upload_validation_returns_400(world: World, body):
    status, resp = call(world, docs_api, api("POST /documents/upload-url", body=body))
    assert status == 400 and resp["error"]["code"]


def test_oversized_body_is_413(world: World):
    status, _ = call(world, docs_api, api("POST /documents/upload-url", body="x" * 9000))
    assert status == 413


def test_unauthenticated_is_401(world: World):
    status, resp = call(world, docs_api, api("GET /documents", owner=None))
    assert status == 401 and resp["error"]["code"] == "UNAUTHENTICATED"


def test_document_quota(world: World):
    for _ in range(3):
        upload(world)
    status, resp = call(
        world,
        docs_api,
        api(
            "POST /documents/upload-url",
            body={"filename": "a.pdf", "content_type": "application/pdf"},
        ),
    )
    assert status == 409 and resp["error"]["code"] == "DOCUMENT_LIMIT_REACHED"


# ---- ingestion + status transitions ---------------------------------------
def test_happy_path_uploaded_to_ready(world: World):
    doc = upload(world)
    assert record(world, doc).status == "UPLOADED"
    assert ingest(world, doc) == "ready"
    rec = record(world, doc)
    assert rec.status == "READY" and rec.page_count == 2 and rec.chunk_count == 2
    assert rec.chunks_per_page == [1, 1] and rec.error_code is None
    assert {v["metadata"]["page"] for v in world.s3v.items.values()} == {1, 2}


def test_duplicate_s3_event_does_nothing(world: World):
    doc = ready_doc(world)
    calls = len(world.bedrock.embed_calls)
    assert ingest(world, doc) == "duplicate"
    assert len(world.bedrock.embed_calls) == calls and len(world.s3v.items) == 2


def test_concurrent_worker_is_turned_away_but_crashed_worker_is_replaced(world: World):
    doc = upload(world)
    assert world.deps.repo.claim_processing(U1, doc, 900)  # worker A claims
    assert ingest(world, doc) == "busy"  # worker B blocked
    world.table.items[(U1, doc)]["updated_at"] = "2000-01-01T00:00:00.000000Z"  # A crashed long ago
    assert ingest(world, doc) == "ready"


def test_unknown_document_is_ignored(world: World):
    assert ingest(world, "f" * 32) == "ignored"


def test_bad_pdf_fails_permanently_without_retry(world: World):
    doc = upload(world, data=b"just text, not a pdf")
    assert ingest(world, doc) == "failed"
    rec = record(world, doc)
    assert rec.status == "FAILED" and rec.error_code == "NOT_A_PDF"


def test_oversized_pdf_fails_and_is_deleted(world: World):
    doc = upload(world, data=b"%PDF-" + b"0" * 200_000)
    assert ingest(world, doc) == "failed"
    assert record(world, doc).error_code == "FILE_TOO_LARGE"
    assert object_key(U1, doc) not in world.s3.objects


def test_transient_error_marks_failed_reraises_then_retry_succeeds(world: World):
    doc = upload(world)
    original = world.bedrock.invoke_model
    world.bedrock.invoke_model = throttle  # type: ignore[method-assign]
    with pytest.raises(ClientError):
        ingest(world, doc)  # Lambda would now retry automatically
    assert record(world, doc).status == "FAILED"
    assert record(world, doc).error_code == "INGESTION_ERROR"
    world.bedrock.invoke_model = original  # type: ignore[method-assign]
    assert ingest(world, doc) == "ready"  # FAILED -> PROCESSING -> READY
    assert record(world, doc).error_code is None


def test_partial_vector_write_never_reaches_ready(world: World):
    doc = upload(world)

    def boom(**kw: Any) -> None:
        raise ClientError({"Error": {"Code": "InternalServerException"}}, "PutVectors")

    world.s3v.put_vectors = boom  # type: ignore[method-assign]
    with pytest.raises(ClientError):
        ingest(world, doc)
    assert record(world, doc).status == "FAILED" and world.s3v.items == {}


def test_reprocessing_a_shorter_pdf_removes_stale_vectors(world: World):
    doc = ready_doc(world)
    assert len(world.s3v.items) == 2
    world.s3.objects[object_key(U1, doc)] = make_pdf(["only one page now"])
    result = ingest_handler.handle(
        {"reprocess": True, "owner_id": U1, "document_id": doc}, None, world.deps
    )
    assert result == ["ready"]
    assert list(world.s3v.items) == [f"{doc}:p001-c000"]  # page-2 vector gone
    assert record(world, doc).chunk_count == 1


def test_document_deleted_mid_ingest_leaves_no_vectors(world: World):
    doc = upload(world)
    real_put = world.s3v.put_vectors

    def put_then_delete(**kw: Any) -> dict:
        out = real_put(**kw)
        world.table.items[(U1, doc)]["status"] = "DELETING"  # user deletes meanwhile
        return out

    world.s3v.put_vectors = put_then_delete  # type: ignore[method-assign]
    assert ingest(world, doc) == "superseded"
    assert world.s3v.items == {}


# ---- S3 event handler ------------------------------------------------------
def s3_event(key: str, bucket: str = "docs-bucket", name: str = "ObjectCreated:Put") -> dict:
    return {
        "Records": [
            {
                "eventName": name,
                "s3": {"bucket": {"name": bucket}, "object": {"key": key, "eTag": "e1"}},
            }
        ]
    }


def test_s3_event_triggers_ingestion_and_duplicates_are_harmless(world: World):
    doc = upload(world)
    ev = s3_event(object_key(U1, doc))
    assert ingest_handler.handle(ev, None, world.deps) == ["ready"]
    assert ingest_handler.handle(ev, None, world.deps) == ["duplicate"]


def test_s3_event_rejects_foreign_buckets_and_odd_keys(world: World):
    doc = upload(world)
    assert ingest_handler.handle(
        s3_event(object_key(U1, doc), bucket="other"), None, world.deps
    ) == ["ignored"]
    assert ingest_handler.handle(s3_event("uploads/../../etc/passwd"), None, world.deps) == [
        "ignored"
    ]
    assert (
        ingest_handler.handle(
            s3_event(object_key(U1, doc), name="ObjectRemoved:Delete"), None, world.deps
        )
        == []
    )


# ---- read / list / isolation ----------------------------------------------
def test_list_and_get_are_owner_scoped_and_hide_internals(world: World):
    d1 = ready_doc(world, U1)
    ready_doc(world, U2)
    status, body = call(world, docs_api, api("GET /documents"))
    assert status == 200 and [d["document_id"] for d in body["documents"]] == [d1]
    for hidden in ("owner_id", "source_etag", "chunks_per_page"):
        assert hidden not in body["documents"][0]
    assert call(world, docs_api, api("GET /documents/{document_id}", U1, doc=d1))[0] == 200
    assert call(world, docs_api, api("GET /documents/{document_id}", U2, doc=d1))[0] == 404


def test_invalid_document_id_is_400(world: World):
    assert call(world, docs_api, api("GET /documents/{document_id}", doc="../x"))[0] == 400


def test_download_url(world: World):
    d1 = ready_doc(world)
    status, body = call(world, docs_api, api("GET /documents/{document_id}/download-url", doc=d1))
    assert status == 200 and body["url"].startswith("https://")
    assert (
        call(world, docs_api, api("GET /documents/{document_id}/download-url", U2, doc=d1))[0]
        == 404
    )


# ---- delete / reprocess ----------------------------------------------------
def test_delete_removes_vectors_object_and_record(world: World):
    d1 = ready_doc(world)
    other = ready_doc(world, U2)
    status, _ = call(world, docs_api, api("DELETE /documents/{document_id}", doc=d1))
    assert status == 200
    assert record(world, d1) is None and object_key(U1, d1) not in world.s3.objects
    assert all(k.startswith(other) for k in world.s3v.items)  # other user's data untouched
    assert call(world, docs_api, api("DELETE /documents/{document_id}", doc=d1))[0] == 404


def test_delete_cannot_touch_someone_elses_document(world: World):
    d1 = ready_doc(world, U1)
    assert call(world, docs_api, api("DELETE /documents/{document_id}", U2, doc=d1))[0] == 404
    assert record(world, d1, U1).status == "READY" and len(world.s3v.items) == 2


def test_failed_delete_is_retryable(world: World):
    d1 = ready_doc(world)
    real = world.deps.store.delete_keys

    def fail_once(keys: Any) -> None:
        world.deps.store.delete_keys = real  # type: ignore[method-assign]
        raise ClientError({"Error": {"Code": "InternalServerException"}}, "DeleteVectors")

    world.deps.store.delete_keys = fail_once  # type: ignore[method-assign]
    assert call(world, docs_api, api("DELETE /documents/{document_id}", doc=d1))[0] == 502
    assert record(world, d1).status == "DELETING"  # blocked from queries, retryable
    assert call(world, docs_api, api("DELETE /documents/{document_id}", doc=d1))[0] == 200
    assert world.s3v.items == {} and record(world, d1) is None


def test_reprocess_endpoint(world: World):
    d1 = ready_doc(world)
    status, _ = call(world, docs_api, api("POST /documents/{document_id}/reprocess", doc=d1))
    assert status == 202
    inv = world.lam.invocations[0]
    assert inv["InvocationType"] == "Event" and inv["FunctionName"] == "ingest-fn"
    assert json.loads(inv["Payload"]) == {"reprocess": True, "owner_id": U1, "document_id": d1}
    fresh = upload(world)  # UPLOADED docs can't be reprocessed
    assert (
        call(world, docs_api, api("POST /documents/{document_id}/reprocess", doc=fresh))[0] == 409
    )


# ---- query endpoint --------------------------------------------------------
QUESTION = "what ph nutrient between 5.8 and 6.2"


def ask(world: World, doc: str, owner: str = U1, q: str = QUESTION) -> tuple[int, dict]:
    return call(world, query_api, api("POST /query", owner, {"document_id": doc, "question": q}))


def test_query_returns_answer_with_real_citation(world: World):
    doc = ready_doc(world)
    world.bedrock.reply = json.dumps(
        {"answerable": True, "answer": "pH 5.8-6.2", "sources": ["S1"]}
    )
    status, body = ask(world, doc)
    assert status == 200 and body["answered"] and body["request_id"] == "rid-1"
    assert body["citations"][0]["page"] == 2 and body["citations"][0]["filename"] == "guide.pdf"


def test_query_not_ready_is_409_and_foreign_doc_is_404(world: World):
    fresh = upload(world)
    assert ask(world, fresh)[0] == 409
    doc = ready_doc(world)
    assert ask(world, doc, owner=U2)[0] == 404


def test_query_validation(world: World):
    doc = ready_doc(world)
    assert ask(world, doc, q="   ")[0] == 400
    assert ask(world, doc, q="x" * 2000)[0] == 400
    assert (
        call(world, query_api, api("POST /query", body={"document_id": "bad", "question": "q"}))[0]
        == 400
    )


def test_unanswerable_question_is_200_not_answered(world: World):
    doc = ready_doc(world)
    world.bedrock.reply = json.dumps({"answerable": False, "answer": "", "sources": []})
    status, body = ask(world, doc)
    assert status == 200 and body["answered"] is False and body["citations"] == []


def test_model_failures_map_to_safe_errors(world: World):
    doc = ready_doc(world)
    world.bedrock.reply = "not json at all"
    status, body = ask(world, doc)
    assert status == 502 and body["error"]["code"] == "UPSTREAM_ERROR"
    assert "not json" not in json.dumps(body)
    world.bedrock.invoke_model = throttle  # type: ignore[method-assign]
    assert ask(world, doc)[0] == 429


def test_logs_never_contain_the_question(world: World, capsys: pytest.CaptureFixture[str]):
    doc = ready_doc(world)
    world.bedrock.reply = json.dumps({"answerable": False, "answer": "", "sources": []})
    ask(world, doc, q="my very secret question text")
    out = capsys.readouterr().out
    assert "secret question" not in out and "query_done" in out and "QueryLatencyMs" in out


# ---- Phase 7 hardening -----------------------------------------------------
def test_reuploading_new_content_with_a_new_etag_is_reindexed(world: World):
    doc = upload(world)
    ev = s3_event(object_key(U1, doc))
    assert ingest_handler.handle(ev, None, world.deps) == ["ready"]
    world.s3.objects[object_key(U1, doc)] = make_pdf(["totally different single page"])
    ev["Records"][0]["s3"]["object"]["eTag"] = "e2"  # S3 reports a new ETag for new bytes
    assert ingest_handler.handle(ev, None, world.deps) == ["ready"]
    assert list(world.s3v.items) == [f"{doc}:p001-c000"]  # old page-2 vector removed
    assert "different" in world.s3v.items[f"{doc}:p001-c000"]["metadata"]["text"]


def test_document_with_too_many_chunks_fails_before_any_paid_embedding_call(world: World):
    object.__setattr__(world.deps.settings, "max_chunks", 1)  # frozen dataclass: test-only override
    doc = upload(world)  # sample PDF produces 2 chunks
    assert ingest(world, doc) == "failed"
    assert record(world, doc).error_code == "DOCUMENT_TOO_LARGE"
    assert world.bedrock.embed_calls == [] and world.s3v.items == {}


def test_invalid_base64_body_is_a_400_not_a_500(world: World):
    ev = api("POST /query", body="@@@not-base64@@@")
    ev["isBase64Encoded"] = True
    status, body = call(world, query_api, ev)
    assert status == 400 and body["error"]["code"] == "INVALID_JSON"
