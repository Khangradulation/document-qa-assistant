"""RAG evaluation: retrieval, citations, groundedness, and unanswerable-question behaviour.

    .venv/bin/python eval/run_eval.py                  # offline: stand-in models, no AWS (default)
    .venv/bin/python eval/run_eval.py --mode live      # real Bedrock + S3 Vectors (costs money; needs AWS env)

Metrics (per answerable question unless noted):
  hit@1 / MRR    is a chunk from an expected page ranked first / how high (before the distance threshold)
  answered       the system produced an answer (not "not found")
  cites_right    >=1 citation, and EVERY cited page is an expected page
  evidence_ok    all expected keywords appear in the cited excerpts (the facts really are in the evidence)
  answer_ok      all expected keywords appear in the answer text (answer contains the facts)
Unanswerable questions: correct only if NOT answered and NO citations.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT / "backend" / "src"), str(ROOT / "backend" / "tests"), str(ROOT / "eval")]

from docqa.answer import BedrockGenerator, QueryDeps, answer_question  # noqa: E402
from docqa.chunking import chunk_pages  # noqa: E402
from docqa.config import Settings  # noqa: E402
from docqa.embeddings import Embedder  # noqa: E402
from docqa.pdf import extract_pages  # noqa: E402
from docqa.vectorstore import VectorStore  # noqa: E402

DOC_ID, OWNER = "e" * 32, "eval-owner"
PDF = ROOT / "data" / "greenfield_hydroponics_guide.pdf"
QUESTIONS = ROOT / "eval" / "questions.json"


def build(mode: str, s: Settings) -> tuple[Embedder, VectorStore, BedrockGenerator]:
    if mode == "offline":
        from fakes import FakeS3Vectors
        from offline import OfflineBedrock

        bedrock = OfflineBedrock(s.embed_dimensions)
        return (
            Embedder(bedrock, "offline-hash", s.embed_dimensions),
            VectorStore(FakeS3Vectors(), "offline", "offline"),
            BedrockGenerator(bedrock, "offline-extractive"),
        )
    from docqa.embeddings import make_bedrock_client
    from docqa.vectorstore import make_s3vectors_client

    for need in ("vector_bucket", "vector_index", "generation_model_id"):
        if not getattr(s, need):
            sys.exit(f"live mode needs {need.upper()} in the environment")
    br = make_bedrock_client(s.region)
    return (
        Embedder(br, s.embed_model_id, s.embed_dimensions),
        VectorStore(make_s3vectors_client(s.region), s.vector_bucket, s.vector_index),
        BedrockGenerator(br, s.generation_model_id),
    )


def evaluate(mode: str, s: Settings) -> dict:
    embedder, store, generator = build(mode, s)
    pages = extract_pages(PDF.read_bytes(), s.max_pdf_bytes, s.max_pages)
    chunks = chunk_pages(DOC_ID, pages, s.chunk_tokens, s.chunk_overlap_tokens)
    store.put_chunks(
        chunks, [embedder.embed(c.text) for c in chunks], filename=PDF.name, owner_id=OWNER
    )
    deps = QueryDeps(embedder, store, generator, s.top_k, s.max_distance)
    rows = []
    try:
        for q in json.loads(QUESTIONS.read_text()):
            hits = store.query(
                embedder.embed(q["question"]), document_id=DOC_ID, owner_id=OWNER, top_k=s.top_k
            )
            resp = answer_question(
                deps, question=q["question"], document_id=DOC_ID, owner_id=OWNER, request_id=q["id"]
            )
            exp = set(q["expected_pages"])
            ranks = [i for i, h in enumerate(hits, 1) if h.chunk.page in exp]
            cited_pages = {c.page for c in resp.citations}
            evidence = " ".join(c.excerpt for c in resp.citations).lower()
            kws = [k.lower() for k in q["expected_keywords"]]
            row = {
                "id": q["id"], "answerable": q["answerable"], "answered": resp.answered,
                "best_distance": round(hits[0].distance, 3) if hits else None,
                "cited_pages": sorted(cited_pages), "expected_pages": sorted(exp),
            }  # fmt: skip
            if q["answerable"]:
                row |= {
                    "rank": ranks[0] if ranks else None,
                    "cites_right": bool(cited_pages) and cited_pages <= exp,
                    "evidence_ok": bool(evidence) and all(k in evidence for k in kws),
                    "answer_ok": resp.answered and all(k in resp.answer.lower() for k in kws),
                }
            else:
                row["correct_refusal"] = (not resp.answered) and not resp.citations
            rows.append(row)
    finally:
        store.delete_keys([c.vector_key for c in chunks])  # live mode must leave nothing behind
    return {
        "mode": mode,
        "settings": {"top_k": s.top_k, "max_distance": s.max_distance},
        "rows": rows,
    }


def summarize(result: dict) -> dict:
    a = [r for r in result["rows"] if r["answerable"]]
    u = [r for r in result["rows"] if not r["answerable"]]
    pct = lambda n, d: round(100 * n / d, 1) if d else None  # noqa: E731
    ranks = [r["rank"] for r in a]
    return {
        "answerable_n": len(a), "unanswerable_n": len(u),
        "hit@1_%": pct(sum(r == 1 for r in ranks), len(a)),
        "MRR": round(statistics.mean(1 / r if r else 0 for r in ranks), 3) if a else None,
        "answered_%": pct(sum(r["answered"] for r in a), len(a)),
        "citations_correct_%": pct(sum(r["cites_right"] for r in a), len(a)),
        "evidence_has_facts_%": pct(sum(r["evidence_ok"] for r in a), len(a)),
        "answer_has_facts_%": pct(sum(r["answer_ok"] for r in a), len(a)),
        "correct_refusals_%": pct(sum(r["correct_refusal"] for r in u), len(u)),
    }  # fmt: skip


def report(result: dict, summary: dict) -> str:
    lines = [f"# RAG evaluation ({result['mode']})", ""]
    if result["mode"] == "offline":
        lines += [
            "> **Offline run with stand-in models** (hashed word-count embeddings, extractive answerer).",
            "> It validates the pipeline plumbing, page metadata, citation checks and the distance-threshold",
            "> mechanism. It does **not** measure Titan embeddings or any Bedrock model. Do not quote these",
            "> numbers as model quality. The sample PDF has only 4 chunks, so retrieval is an easy task.",
            "",
        ]
    lines += [f"Settings: `{result['settings']}`", "", "| metric | value |", "|---|---|"]
    lines += [f"| {k} | {v} |" for k, v in summary.items()]
    lines += [
        "",
        "## Per-question",
        "",
        "| id | answerable | answered | best distance | cited pages | expected | notes |",
        "|---|---|---|---|---|---|---|",
    ]
    for r in result["rows"]:
        bad = []
        if r["answerable"]:
            bad += [k for k in ("cites_right", "evidence_ok", "answer_ok") if not r[k]]
            if r["rank"] != 1:
                bad.append(f"rank={r['rank']}")
        elif not r["correct_refusal"]:
            bad.append("SHOULD HAVE REFUSED")
        lines.append(
            f"| {r['id']} | {r['answerable']} | {r['answered']} | {r['best_distance']} | {r['cited_pages']} | {r['expected_pages']} | {', '.join(bad) or 'ok'} |"
        )
    ad = [
        r["best_distance"]
        for r in result["rows"]
        if r["answerable"] and r["best_distance"] is not None
    ]
    ud = [
        r["best_distance"]
        for r in result["rows"]
        if not r["answerable"] and r["best_distance"] is not None
    ]
    if ad and ud:
        lines += [
            "",
            f"Best-chunk distance: answerable {min(ad)}–{max(ad)}, unanswerable {min(ud)}–{max(ud)}."
            " A good threshold sits between the two ranges (overlap means no threshold can separate them).",
        ]
    return "\n".join(lines) + "\n"


def sweep(mode: str, s: Settings) -> str:
    cols = ("answered_%", "citations_correct_%", "answer_has_facts_%", "correct_refusals_%")
    lines = [
        "", "## Threshold sweep", "",
        "`max_distance` is a pre-filter: chunks farther than this never reach the model. Too strict and",
        "real questions get 'not found'; too loose and the model sees weak evidence (it should still",
        "refuse, and citations are still validated).", "",
        "| max_distance | " + " | ".join(cols) + " |", "|---|" + "---|" * len(cols),
    ]  # fmt: skip
    for t in (0.6, 0.75, 0.85, 0.9, 1.0, 2.0):
        sm = summarize(evaluate(mode, Settings(**{**s.__dict__, "max_distance": t})))
        lines.append(f"| {t} | " + " | ".join(str(sm[c]) for c in cols) + " |")
    return "\n".join(lines) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["offline", "live"], default="offline")
    ap.add_argument("--max-distance", type=float)
    ap.add_argument("--sweep", action="store_true", help="also try several distance thresholds")
    args = ap.parse_args()
    s = Settings.from_env()
    if args.max_distance is not None:
        s = Settings(**{**s.__dict__, "max_distance": args.max_distance})
    if (
        args.mode == "offline"
    ):  # fixed config so the run is reproducible regardless of your shell env
        s = Settings(embed_dimensions=1024, top_k=s.top_k, max_distance=s.max_distance)
    result = evaluate(args.mode, s)
    summary = summarize(result)
    out = ROOT / "eval" / "results"
    out.mkdir(exist_ok=True)
    (out / f"{args.mode}.json").write_text(
        json.dumps({**result, "summary": summary}, indent=2) + "\n"
    )
    text = report(result, summary)
    if args.sweep:
        text += sweep(args.mode, s)
    (out / f"{args.mode}.md").write_text(text)
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
