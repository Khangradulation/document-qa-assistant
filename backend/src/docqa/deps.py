"""Builds real AWS clients once per Lambda container (cold start) and reuses them."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from typing import Any

import boto3
from botocore.config import Config

from docqa.answer import BedrockGenerator, QueryDeps
from docqa.config import Settings
from docqa.embeddings import Embedder, make_bedrock_client
from docqa.repo import DocumentRepo
from docqa.vectorstore import VectorStore, make_s3vectors_client


@dataclass(frozen=True)
class AppDeps:
    settings: Settings
    repo: DocumentRepo
    s3: Any
    lambda_client: Any
    embedder: Embedder
    store: VectorStore
    generator: BedrockGenerator

    @property
    def query_deps(self) -> QueryDeps:
        s = self.settings
        return QueryDeps(self.embedder, self.store, self.generator, s.top_k, s.max_distance)


@lru_cache(maxsize=1)
def build_deps() -> AppDeps:
    s = Settings.from_env()
    s.validate()
    bedrock = make_bedrock_client(s.region)
    # SigV4 is required for presigned URLs in every Region.
    s3 = boto3.client("s3", region_name=s.region, config=Config(signature_version="s3v4"))
    table = boto3.resource("dynamodb", region_name=s.region).Table(s.table_name)
    return AppDeps(
        settings=s,
        repo=DocumentRepo(table),
        s3=s3,
        lambda_client=boto3.client("lambda", region_name=s.region),
        embedder=Embedder(bedrock, s.embed_model_id, s.embed_dimensions),
        store=VectorStore(make_s3vectors_client(s.region), s.vector_bucket, s.vector_index),
        generator=BedrockGenerator(bedrock, s.generation_model_id),
    )
