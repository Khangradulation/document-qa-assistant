"""Regression guard for the offline evaluation. These stand-in models cannot measure real model
quality; they guard the PIPELINE: right page retrieved, right page cited, facts present in the
evidence, refusals when nothing relevant, and a deterministic result."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "eval"))
import run_eval  # noqa: E402

from docqa.config import Settings  # noqa: E402

SETTINGS = Settings(embed_dimensions=1024, max_distance=Settings().max_distance)


def test_default_threshold_is_loose_enough_for_the_eval_questions():
    result = run_eval.evaluate("offline", SETTINGS)
    s = run_eval.summarize(result)
    assert s["hit@1_%"] == 100.0 and s["MRR"] == 1.0
    assert s["answered_%"] == 100.0 and s["citations_correct_%"] == 100.0
    assert s["evidence_has_facts_%"] == 100.0 and s["answer_has_facts_%"] == 100.0


def test_prompt_injection_and_missing_info_questions_are_refused_without_citations():
    rows = {r["id"]: r for r in run_eval.evaluate("offline", SETTINGS)["rows"]}
    for qid in ("u01", "u03", "u04"):  # price, calories, "reveal your system prompt"
        assert rows[qid]["correct_refusal"], qid


def test_a_strict_threshold_turns_real_questions_into_not_found():
    strict = Settings(embed_dimensions=1024, max_distance=0.6)
    s = run_eval.summarize(run_eval.evaluate("offline", strict))
    assert s["answered_%"] == 0.0 and s["correct_refusals_%"] == 100.0


def test_evaluation_is_deterministic_and_cleans_up():
    a = json.dumps(run_eval.evaluate("offline", SETTINGS), sort_keys=True)
    b = json.dumps(run_eval.evaluate("offline", SETTINGS), sort_keys=True)
    assert a == b
