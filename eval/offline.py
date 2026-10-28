"""Stand-ins for Bedrock so the evaluation can run with no AWS account.

HashingEmbedder: hashed bag-of-words (+bigrams) vectors. Crude, but similar wording => nearby vectors.
ExtractiveAnswerer: behaves like a well-behaved LLM would: reads the <source> tags in the prompt,
picks the sentence that best overlaps the question, cites that source, and says "not answerable"
when nothing overlaps enough.

These validate the PIPELINE (chunk/page metadata, filters, thresholds, citation checks). They say
nothing about the quality of real embeddings or a real language model.
"""

from __future__ import annotations

import hashlib
import io
import json
import math
import re
from typing import Any

STOP = {
    "the", "a", "an", "of", "to", "is", "are", "how", "what", "do", "does", "i", "my", "for", "in",
    "and", "should", "be", "at", "it", "on", "or", "can", "that", "this", "with", "as", "by", "from",
    "which", "when", "you", "your", "me", "we", "our", "there",
}  # fmt: skip
MIN_OVERLAP = 2  # content words a sentence must share with the question to count as an answer


def tokens(text: str) -> list[str]:
    # keep inner dots so "5.8" stays one token, but drop sentence-final dots ("lettuce." -> "lettuce")
    words = (t.strip(".") for t in re.findall(r"[a-z0-9.]+", text.lower()))
    return [t for t in words if t and t not in STOP]


def embed(text: str, dims: int) -> list[float]:
    toks = tokens(text)
    feats = toks + [f"{a}_{b}" for a, b in zip(toks, toks[1:], strict=False)]
    v = [0.0] * dims
    for f in feats:
        v[int(hashlib.md5(f.encode()).hexdigest(), 16) % dims] += 1.0  # noqa: S324 (not security)
    norm = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / norm for x in v]


def _unescape(s: str) -> str:
    return s.replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&")


class OfflineBedrock:
    """Implements just the two Bedrock calls our code makes."""

    def __init__(self, dims: int) -> None:
        self.dims = dims

    def invoke_model(self, **kw: Any) -> dict[str, Any]:
        body = json.loads(kw["body"])
        payload = {"embedding": embed(body["inputText"], self.dims), "inputTextTokenCount": 0}
        return {"body": io.BytesIO(json.dumps(payload).encode())}

    def converse(self, **kw: Any) -> dict[str, Any]:
        prompt = kw["messages"][0]["content"][0]["text"]
        question = _unescape(re.search(r"<question>(.*?)</question>", prompt, re.S).group(1))  # type: ignore[union-attr]
        q = set(tokens(question))
        best: tuple[int, str, str] = (0, "", "")
        for sid, _page, text in re.findall(
            r'<source id="(S\d+)" page="(\d+)">(.*?)</source>', prompt, re.S
        ):
            for sentence in re.split(r"(?<=[.!?])\s+", _unescape(text)):
                overlap = len(q & set(tokens(sentence)))
                if overlap > best[0]:
                    best = (overlap, sid, sentence)
        ok = best[0] >= MIN_OVERLAP
        reply = {
            "answerable": ok,
            "answer": best[2] if ok else "",
            "sources": [best[1]] if ok else [],
        }
        return {
            "output": {"message": {"content": [{"text": json.dumps(reply)}]}},
            "usage": {"inputTokens": 0, "outputTokens": 0},
        }
