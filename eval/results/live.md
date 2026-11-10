# RAG evaluation (live)

Settings: `{'top_k': 5, 'max_distance': 1.0}`

| metric | value |
|---|---|
| answerable_n | 16 |
| unanswerable_n | 4 |
| hit@1_% | 75.0 |
| MRR | 0.865 |
| answered_% | 100.0 |
| citations_correct_% | 100.0 |
| evidence_has_facts_% | 93.8 |
| answer_has_facts_% | 100.0 |
| correct_refusals_% | 100.0 |

## Per-question

| id | answerable | answered | best distance | cited pages | expected | notes |
|---|---|---|---|---|---|---|
| q01 | True | True | 0.405 | [1] | [1] | ok |
| q02 | True | True | 0.258 | [1] | [1] | ok |
| q03 | True | True | 0.811 | [1] | [1] | rank=3 |
| q04 | True | True | 0.536 | [2] | [2] | ok |
| q05 | True | True | 0.538 | [2] | [2] | ok |
| q06 | True | True | 0.727 | [2] | [2] | ok |
| q07 | True | True | 0.702 | [2] | [2] | evidence_ok |
| q08 | True | True | 0.697 | [2] | [2] | rank=2 |
| q09 | True | True | 0.249 | [3] | [3] | ok |
| q10 | True | True | 0.293 | [3] | [3] | ok |
| q11 | True | True | 0.817 | [3] | [3] | ok |
| q12 | True | True | 0.797 | [3] | [3] | ok |
| q13 | True | True | 0.483 | [4] | [4] | ok |
| q14 | True | True | 0.644 | [4] | [4] | ok |
| q15 | True | True | 0.861 | [4] | [4] | rank=2 |
| q16 | True | True | 0.748 | [4] | [4] | rank=2 |
| u01 | False | False | 0.949 | [] | [] | ok |
| u02 | False | False | 0.786 | [] | [] | ok |
| u03 | False | False | 0.75 | [] | [] | ok |
| u04 | False | False | 0.933 | [] | [] | ok |

Best-chunk distance: answerable 0.249–0.861, unanswerable 0.75–0.949. A good threshold sits between the two ranges (overlap means no threshold can separate them).
