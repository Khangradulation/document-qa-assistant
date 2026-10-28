# Residual risks and what is still unproven

Honest state as of Phase 7. "Verified" = a command was run and passed. Nothing below has
touched real AWS, GitHub or a real model.

## Evidence we do have (all local)

| Check | Result |
|---|---|
| Backend: ruff, mypy (strict), pytest | pass, 95 tests + 6 live tests deselected |
| Frontend: eslint, tsc, vitest, build, npm audit | pass, 23 tests, 0 vulnerabilities |
| Terraform (2 roots): fmt, validate, Checkov | pass, 0 failed (every skip justified in code) |
| Workflows: actionlint + shellcheck | pass; also catches a deliberately broken file |
| `pip-audit` on Lambda dependencies | no known vulnerabilities |
| PDF fuzzing: 300 corrupted + truncated + hostile PDFs | only `parsed` or `PdfError`; nothing else escapes |
| Mutation checks | removing the duplicate-event guard or the owner filter fails tests |
| Offline RAG eval (stand-in models) | see `eval/results/offline.md`; reproducible byte-for-byte |

## Bugs this phase found (and fixed)

1. pypdf logged **raw document bytes** in warnings (would have reached CloudWatch). Muted; test added.
2. Citation excerpts were the *start* of the chunk, which often hid the supporting sentence
   (4 of 16 eval questions). Now the best-matching sentence, still verbatim text.
3. A malformed base64 request body returned 500 instead of 400.
4. No bound on chunks per document (cost / 300 s timeout). Added `MAX_CHUNKS=1000`, checked
   before any paid embedding call.
5. Default `MAX_DISTANCE` 0.75 was too strict in the offline eval (rejected 15 of 16 real
   questions). Changed to 0.9 (see risk R1: a judgment call, not proof).

## Unproven assumptions: settled only by `RUN_LIVE_TESTS=1 pytest -m integration -s`

| # | Assumption | Live test | If it is false |
|---|---|---|---|
| 1 | `put_vectors` overwrites an existing key | `test_1` | re-ingest would duplicate or error; need delete-then-put |
| 2 | `$and`/`$eq` filter syntax accepted | `test_2` | **every query fails** or leaks across users: fix before any use |
| 3 | 6 KB non-filterable `text` metadata round-trips | `test_3` | answers lose their evidence; move chunk text to DynamoDB/S3 |
| 4 | cosine distance scale (0 = identical) | `test_4` | `MAX_DISTANCE` meaning changes; re-tune |
| 5 | Titan + a Bedrock text model work end to end | `test_5a/5b` | model access / IAM / ARN problem |

Not tested by any of them: S3 `ObjectCreated` trigger, presigned-URL upload + bucket CORS,
Cognito sign-in, API Gateway JWT authorizer, CloudFront, alarms, the deploy role's IAM
completeness, and the GitHub workflows themselves.

## Risk register

| ID | Risk | Likelihood / impact | Mitigation now | Still open |
|---|---|---|---|---|
| R1 | Distance threshold is a guess; real Titan distances unknown | high / medium | loose pre-filter; model refuses; citations validated; `--mode live --sweep` to tune | run the live sweep, set the value from data |
| R2 | LLM answers a question it should refuse (the offline stand-in does this for `u02`) | medium / medium | grounded prompt, citation validation drops invented sources, no-valid-citation => "not found" | measure with the real model; prompt-injection resistance is *reduced*, not eliminated |
| R3 | Prompt injection inside a PDF | medium / medium | escaped tags, "data not instructions" prompt, model only picks source IDs, answer can't add citations | a crafted PDF can still bias the *answer text* of a user's own document |
| R4 | Presigned PUT cannot cap upload size | medium / low | oversize rejected + deleted at ingest; 10 MB check before parse; 5-minute URL | a user can briefly store a large object (bounded by quota of 20 docs + lifecycle) |
| R5 | PDF "decompression bomb" / memory exhaustion in the parser | low / medium | size, page and chunk caps; 1 GB Lambda; fuzz-tested | no CPU/memory-bomb specific test; only a size cap |
| R6 | Cost abuse by a signed-in user | medium / medium | no public sign-up, API throttle (10 rps/20 burst), 20-doc quota, 1000-chunk cap, 1000-char questions | no per-user rate limit or budget cap: AWS Budgets only *notify* |
| R7 | Deploy role needs IAM actions we did not predict | high / low | scoped by name prefix + permissions boundary | first apply will probably need one or two additions |
| R8 | Reprocess of an identical PDF + transient error deletes its good vectors until retry | low / low | doc is FAILED (not queryable) meanwhile; Lambda retries twice | documented trade-off |
| R9 | CloudFront default cert cannot enforce TLS >= 1.2; no WAF | medium / low | HTTPS-only redirect, security headers, CSP | needs a custom domain + ACM (+ WAF) for production |
| R10 | Orphaned vectors if a delete fails after the DB row is gone | low / low | DELETING state first, retryable; row deleted last | no background reconciliation job |
| R11 | Frontend: Safari may ignore `#page=N` | medium / low | link still opens the PDF | none |
| R12 | Single environment, solo project, no multi-tenant load testing | n/a | n/a | not production-ready; this is a demo |

## Demo-grade limits (not production)

* Authentication is real (Cognito JWT) but there is **no MFA**, no account recovery tuning,
  no per-user rate limits, and sign-up is admin-only.
* Everything lives in one account/Region; no backups beyond DynamoDB point-in-time recovery.
* Logs are metadata only, but the CloudWatch log groups use default encryption.

## Cleanup of live tests

The fixtures delete their temporary vector bucket and indexes. If a run is killed, list
leftovers and remove them (only ones named `docqa-it-*`):
```bash
aws s3vectors list-vector-buckets --query "vectorBuckets[?starts_with(vectorBucketName, 'docqa-it-')].vectorBucketName"
# for each: aws s3vectors list-indexes ... ; delete-index ... ; delete-vector-bucket ...
```
