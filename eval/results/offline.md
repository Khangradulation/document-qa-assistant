# RAG evaluation (offline)

> **Offline run with stand-in models** (hashed word-count embeddings, extractive answerer).
> It validates the pipeline plumbing, page metadata, citation checks and the distance-threshold
> mechanism. It does **not** measure Titan embeddings or any Bedrock model. Do not quote these
> numbers as model quality. The sample PDF has only 4 chunks, so retrieval is an easy task.

Settings: `{'top_k': 5, 'max_distance': 0.9}`

| metric | value |
|---|---|
| answerable_n | 16 |
| unanswerable_n | 4 |
| hit@1_% | 100.0 |
| MRR | 1.0 |
| answered_% | 100.0 |
| citations_correct_% | 100.0 |
| evidence_has_facts_% | 100.0 |
| answer_has_facts_% | 100.0 |
| correct_refusals_% | 75.0 |

## Per-question

| id | answerable | answered | best distance | cited pages | expected | notes |
|---|---|---|---|---|---|---|
| q01 | True | True | 0.897 | [1] | [1] | ok |
| q02 | True | True | 0.739 | [1] | [1] | ok |
| q03 | True | True | 0.768 | [1] | [1] | ok |
| q04 | True | True | 0.823 | [2] | [2] | ok |
| q05 | True | True | 0.841 | [2] | [2] | ok |
| q06 | True | True | 0.832 | [2] | [2] | ok |
| q07 | True | True | 0.832 | [2] | [2] | ok |
| q08 | True | True | 0.779 | [2] | [2] | ok |
| q09 | True | True | 0.69 | [3] | [3] | ok |
| q10 | True | True | 0.759 | [3] | [3] | ok |
| q11 | True | True | 0.856 | [3] | [3] | ok |
| q12 | True | True | 0.75 | [3] | [3] | ok |
| q13 | True | True | 0.825 | [4] | [4] | ok |
| q14 | True | True | 0.835 | [4] | [4] | ok |
| q15 | True | True | 0.849 | [4] | [4] | ok |
| q16 | True | True | 0.769 | [4] | [4] | ok |
| u01 | False | False | 0.968 | [] | [] | ok |
| u02 | False | True | 0.866 | [2] | [] | SHOULD HAVE REFUSED |
| u03 | False | False | 0.903 | [] | [] | ok |
| u04 | False | False | 0.97 | [] | [] | ok |

Best-chunk distance: answerable 0.69–0.897, unanswerable 0.866–0.97. A good threshold sits between the two ranges (overlap means no threshold can separate them).

## Threshold sweep

`max_distance` is a pre-filter: chunks farther than this never reach the model. Too strict and
real questions get 'not found'; too loose and the model sees weak evidence (it should still
refuse, and citations are still validated).

| max_distance | answered_% | citations_correct_% | answer_has_facts_% | correct_refusals_% |
|---|---|---|---|---|
| 0.6 | 0.0 | 0.0 | 0.0 | 100.0 |
| 0.75 | 12.5 | 12.5 | 12.5 | 100.0 |
| 0.85 | 87.5 | 87.5 | 87.5 | 100.0 |
| 0.9 | 100.0 | 100.0 | 100.0 | 75.0 |
| 1.0 | 100.0 | 100.0 | 100.0 | 75.0 |
| 2.0 | 100.0 | 100.0 | 100.0 | 75.0 |
