import pytest
from fakes import FakeBedrock, FakeLambda, FakeS3, FakeS3Vectors, FakeTable

from docqa.answer import BedrockGenerator
from docqa.config import Settings
from docqa.deps import AppDeps
from docqa.embeddings import Embedder
from docqa.repo import DocumentRepo
from docqa.vectorstore import VectorStore

DIMS = 64


class World:
    """Everything a handler needs, wired to in-memory fakes."""

    def __init__(self) -> None:
        self.bedrock, self.s3v = FakeBedrock(DIMS), FakeS3Vectors()
        self.table, self.s3, self.lam = FakeTable(), FakeS3(), FakeLambda()
        settings = Settings(
            docs_bucket="docs-bucket", vector_bucket="vb", vector_index="idx",
            table_name="t", ingest_function_name="ingest-fn", embed_dimensions=DIMS,
            generation_model_id="gen-model", max_distance=0.95, max_docs_per_user=3,
            max_pdf_bytes=100_000,
        )  # fmt: skip
        self.deps = AppDeps(
            settings=settings,
            repo=DocumentRepo(self.table),
            s3=self.s3,
            lambda_client=self.lam,
            embedder=Embedder(self.bedrock, settings.embed_model_id, DIMS),
            store=VectorStore(self.s3v, "vb", "idx"),
            generator=BedrockGenerator(self.bedrock, "gen-model"),
        )


@pytest.fixture
def world() -> World:
    return World()
