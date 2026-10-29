"""LIVE tests against real AWS. NEVER run by default: they cost a little and need credentials.

    RUN_LIVE_TESTS=1 AWS_REGION=us-east-2 .venv/bin/pytest -m integration -s
    # plus, to also call Bedrock (embedding + text generation):
    RUN_LIVE_BEDROCK=1 GENERATION_MODEL_ID=<model enabled in your account> ...

Each test settles one assumption the mocked unit tests cannot:
  1 put_vectors overwrites an existing key        (idempotent ingestion depends on it)
  2 the $and/$eq metadata filter syntax works     (document + owner scoping depends on it)
  3 big non-filterable "text" metadata round-trips (chunk text is returned from search)
  4 what "distance" means for a cosine index      (max_distance threshold depends on it)
  5 Bedrock embed + converse work end to end      (real RAG quality, run via eval/run_eval.py)
A temporary vector bucket is created and deleted by the fixtures. If a run is interrupted, look for
leftover buckets named docqa-it-* and delete them (see docs/RESIDUAL_RISKS.md).
"""

import os
import sys
import time
import uuid
from pathlib import Path

import boto3
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "eval"))

from docqa.embeddings import Embedder, make_bedrock_client  # noqa: E402
from docqa.models import Chunk  # noqa: E402
from docqa.vectorstore import VectorStore  # noqa: E402

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.environ.get("RUN_LIVE_TESTS") != "1",
        reason="live AWS test: set RUN_LIVE_TESTS=1 and AWS credentials (costs a little money)",
    ),
]
needs_bedrock = pytest.mark.skipif(
    os.environ.get("RUN_LIVE_BEDROCK") != "1",
    reason="paid model calls: also set RUN_LIVE_BEDROCK=1",
)
REGION = os.environ.get("AWS_REGION", "us-east-2")
DIMS = 8


@pytest.fixture(scope="module")
def s3v():
    return boto3.client("s3vectors", region_name=REGION)


@pytest.fixture(scope="module")
def bucket(s3v):
    name = f"docqa-it-{uuid.uuid4().hex[:12]}"
    s3v.create_vector_bucket(vectorBucketName=name)
    indexes: list[str] = []
    yield name, indexes
    for idx in indexes:  # always clean up, even if a test failed
        try:
            s3v.delete_index(vectorBucketName=name, indexName=idx)
        except Exception as exc:
            print(f"cleanup: could not delete index {idx}: {type(exc).__name__}")
    try:
        s3v.delete_vector_bucket(vectorBucketName=name)
    except Exception as exc:
        print(f"cleanup: could not delete bucket {name}: {type(exc).__name__}")


def make_index(s3v, bucket, dims: int) -> str:
    name, indexes = bucket
    idx = f"idx-{dims}-{uuid.uuid4().hex[:6]}"
    s3v.create_index(
        vectorBucketName=name, indexName=idx, dataType="float32", dimension=dims,
        distanceMetric="cosine", metadataConfiguration={"nonFilterableMetadataKeys": ["text"]},
    )  # fmt: skip
    indexes.append(idx)
    return idx


def eventually(fn, seconds: float = 15.0):
    """Retry briefly in case reads lag writes (we have not verified the consistency model)."""
    deadline = time.monotonic() + seconds
    while True:
        result = fn()
        if result or time.monotonic() > deadline:
            return result
        time.sleep(1)


def unit(i: int, dims: int = DIMS) -> list[float]:
    v = [0.0] * dims
    v[i] = 1.0
    return v


def test_1_put_vectors_overwrites_an_existing_key(s3v, bucket):
    idx = make_index(s3v, bucket, DIMS)
    name = bucket[0]
    base = {"vectorBucketName": name, "indexName": idx}
    s3v.put_vectors(
        **base, vectors=[{"key": "k", "data": {"float32": unit(0)}, "metadata": {"v": 1}}]
    )
    s3v.put_vectors(
        **base, vectors=[{"key": "k", "data": {"float32": unit(1)}, "metadata": {"v": 2}}]
    )
    got = eventually(
        lambda: s3v.get_vectors(**base, keys=["k"], returnMetadata=True, returnData=True)["vectors"]
    )
    assert len(got) == 1, "expected exactly one vector for the key"
    assert got[0]["metadata"]["v"] == 2, "second put did NOT overwrite the first"
    assert got[0]["data"]["float32"][1] == pytest.approx(1.0)


def test_2_filter_scopes_results_by_document_and_owner(s3v, bucket):
    idx = make_index(s3v, bucket, DIMS)
    store = VectorStore(s3v, bucket[0], idx)
    a, b = "a" * 32, "b" * 32
    store.put_chunks(
        [Chunk(a, "p001-c000", 1, "alpha")], [unit(0)], filename="a.pdf", owner_id="u1"
    )
    store.put_chunks([Chunk(b, "p001-c000", 1, "beta")], [unit(0)], filename="b.pdf", owner_id="u2")
    hits = eventually(lambda: store.query(unit(0), document_id=a, owner_id="u1", top_k=5))
    assert [h.chunk.document_id for h in hits] == [a], "filter did not isolate document A"
    assert store.query(unit(0), document_id=a, owner_id="u2", top_k=5) == [], "owner filter leaked"


def test_3_large_nonfilterable_text_metadata_round_trips(s3v, bucket):
    idx = make_index(s3v, bucket, DIMS)
    store = VectorStore(s3v, bucket[0], idx)
    text = ("lettuce grows best in deep water culture. " * 200)[:6000]  # bigger than a real chunk
    store.put_chunks(
        [Chunk("c" * 32, "p001-c000", 1, text)], [unit(2)], filename="c.pdf", owner_id="u"
    )
    hits = eventually(lambda: store.query(unit(2), document_id="c" * 32, owner_id="u", top_k=1))
    assert hits and hits[0].chunk.text == text, "text metadata was truncated or not returned"


def test_4_cosine_distance_semantics(s3v, bucket, capsys):
    idx = make_index(s3v, bucket, DIMS)
    name = bucket[0]
    vecs = {"same": unit(0), "orthogonal": unit(1), "opposite": [-x for x in unit(0)]}
    s3v.put_vectors(
        vectorBucketName=name, indexName=idx,
        vectors=[{"key": k, "data": {"float32": v}} for k, v in vecs.items()],
    )  # fmt: skip

    def query():
        return s3v.query_vectors(
            vectorBucketName=name, indexName=idx, topK=3, queryVector={"float32": unit(0)},
            returnDistance=True,
        )  # fmt: skip

    res = eventually(lambda: query() if len(query()["vectors"]) == 3 else None)
    d = {v["key"]: v["distance"] for v in res["vectors"]}
    with capsys.disabled():
        print(
            f"\nOBSERVED cosine distances (query = 'same'): {d}  metric={res.get('distanceMetric')}"
        )
    assert res["distanceMetric"] == "cosine"
    assert d["same"] < 0.01 < d["orthogonal"] < d["opposite"], f"unexpected distance scale: {d}"


@needs_bedrock
def test_5a_titan_embeddings_have_the_configured_dimension_and_capture_meaning():
    dims = int(os.environ.get("EMBED_DIMENSIONS", "1024"))
    emb = Embedder(
        make_bedrock_client(REGION),
        os.environ.get("EMBED_MODEL_ID", "amazon.titan-embed-text-v2:0"),
        dims,
    )
    a, b, c = (emb.embed(t) for t in (
        "What pH should hydroponic lettuce nutrient solution have?",
        "Keep the nutrient solution pH between 5.8 and 6.2 for lettuce.",
        "The quarterly tax filing deadline is in April.",
    ))  # fmt: skip
    cos = lambda x, y: sum(p * q for p, q in zip(x, y, strict=True))  # noqa: E731 (vectors are normalized)
    assert len(a) == dims and cos(a, b) > cos(a, c)


@needs_bedrock
def test_5b_end_to_end_rag_with_real_models(s3v, bucket, capsys):
    import run_eval

    from docqa.config import Settings

    model = os.environ.get("GENERATION_MODEL_ID")
    if not model:
        pytest.skip("set GENERATION_MODEL_ID to a text model enabled in your account")
    dims = int(os.environ.get("EMBED_DIMENSIONS", "1024"))
    idx = make_index(s3v, bucket, dims)
    settings = Settings(
        region=REGION, vector_bucket=bucket[0], vector_index=idx,
        generation_model_id=model, embed_dimensions=dims,
    )  # fmt: skip
    summary = run_eval.summarize(run_eval.evaluate("live", settings))
    with capsys.disabled():
        print(f"\nLIVE EVAL SUMMARY: {summary}")
    # Generous floors: this checks that the system works, not that it is excellent.
    assert summary["hit@1_%"] >= 75 and summary["citations_correct_%"] >= 75
