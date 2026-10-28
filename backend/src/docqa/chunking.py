"""Page-aware chunking.

A chunk never spans two pages, so every chunk has exactly one true page number.
(Trade-off: a sentence that crosses a page break is split. Citations stay exact.)

Token counts are *approximated* from words (1 token ~= 0.75 words) to avoid a
tokenizer dependency. Titan accepts far more than our chunk size, so the
approximation only needs to be roughly right.
"""

from __future__ import annotations

from collections.abc import Sequence

from docqa.models import Chunk
from docqa.pdf import PageText

WORDS_PER_TOKEN = 0.75


def chunk_pages(
    document_id: str,
    pages: Sequence[PageText],
    chunk_tokens: int,
    overlap_tokens: int,
) -> list[Chunk]:
    if overlap_tokens >= chunk_tokens:
        raise ValueError("overlap must be smaller than chunk size")
    size = max(1, int(chunk_tokens * WORDS_PER_TOKEN))
    overlap = int(overlap_tokens * WORDS_PER_TOKEN)
    step = size - overlap

    chunks: list[Chunk] = []
    for page in pages:
        words = page.text.split()
        if not words:  # empty page: nothing to index
            continue
        start, index = 0, 0
        while True:
            end = min(start + size, len(words))
            chunks.append(
                Chunk(
                    document_id=document_id,
                    # Deterministic ID: re-ingesting the same PDF yields the
                    # same keys, so writes overwrite instead of duplicating.
                    chunk_id=f"p{page.page:03d}-c{index:03d}",
                    page=page.page,
                    text=" ".join(words[start:end]),
                )
            )
            if end == len(words):
                break
            start += step
            index += 1
    return chunks


def chunks_per_page(chunks: Sequence[Chunk]) -> list[int]:
    """Index i holds the chunk count of page i+1. Stored in DynamoDB so we can
    rebuild every vector key later (for cleanup) without listing the index."""
    if not chunks:
        return []
    counts = [0] * max(c.page for c in chunks)
    for c in chunks:
        counts[c.page - 1] += 1
    return counts


def keys_from_counts(document_id: str, counts: Sequence[int]) -> list[str]:
    return [
        f"{document_id}:p{page:03d}-c{i:03d}"
        for page, n in enumerate(counts, start=1)
        for i in range(n)
    ]
