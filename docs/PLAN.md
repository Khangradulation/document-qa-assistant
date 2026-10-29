# Plan and concept guide

## How the whole thing works (the 2-minute version)

**RAG = Retrieval-Augmented Generation.** An LLM doesn't know your PDF. So:

1. **Ingest (once per PDF).** Extract text page by page → cut into overlapping
   *chunks* → turn each chunk into an *embedding* (a list of 1024 numbers that
   captures meaning; similar meaning = nearby numbers) → store in a *vector index*.
2. **Ask (every question).** Embed the question with the *same* model → ask the
   index for the nearest chunks → paste only those chunks into a prompt → the LLM
   answers *from them* and we show which pages they came from (citations).

Why chunks keep their page number: each stored vector carries metadata
(`document_id`, `page`, `chunk_id`). That is how a citation can say "page 7".

## AWS pieces and why each exists

| Piece | Job | Why this one |
|---|---|---|
| S3 (docs bucket) | holds uploaded PDFs, private | cheap, durable; can trigger Lambda on upload |
| Presigned PUT URL | lets the browser upload straight to S3 for ~5 min | PDF never goes through our API, no AWS keys in browser |
| Lambda | runs our Python only when needed | no servers, pay per use |
| Bedrock | embeddings + text generation | managed models, no GPU |
| S3 Vectors | the vector index | serverless, pay per use, no cluster to keep running |
| DynamoDB | one row per document: status, errors | simple key lookups |
| API Gateway (HTTP API) | public HTTPS front door for the Lambdas | cheap and simple |
| CloudWatch | logs + alarms | built in |
| Terraform | creates all of the above from code | repeatable, reviewable, deletable |
| GitHub Actions + OIDC | CI/CD without stored AWS keys | GitHub proves its identity to AWS per run |

## Verified facts (checked against official docs on 2026-10-28)

- Lambda runtime `python3.14` is supported (deprecation Jun 2029).
- Titan Text Embeddings V2: model `amazon.titan-embed-text-v2:0`, request
  `{"inputText", "dimensions": 256|512|1024, "normalize"}`, response `embedding`.
- Boto3 client `s3vectors`: `put_vectors(vectorBucketName, indexName, vectors=[{key, data:{float32:[...]}, metadata}])`;
  `query_vectors(..., topK, queryVector={float32:[...]}, filter, returnDistance, returnMetadata)`.
  Returns a **distance** (metric `cosine` or `euclidean`), not a similarity score.
  A `filter` or `returnMetadata` also requires `s3vectors:GetVectors` permission.
- Terraform AWS provider has `aws_s3vectors_vector_bucket` and `aws_s3vectors_index`
  (exact arguments get confirmed by `terraform validate` in Phase 4).

## Still unverified / needs you

- Terraform, AWS CLI not installed; no AWS credentials on this machine.
- Which Bedrock **generation** model is enabled in your account (`GENERATION_MODEL_ID`).
- Cosine distance semantics and the right `MAX_DISTANCE` threshold: tuned by the
  eval set once we can call AWS. The default (0.9) is a judgment call (loose on purpose), not a measured value.

## Phases (status)

- [x] 0 Recon
- [x] 1 Skeleton + contracts (config, models, tests, lint, types)
- [x] 2 Core RAG (PDF → chunks → embeddings → vector store → retrieval → answer → citation check)
- [x] 3 API + document lifecycle
- [x] 4 Terraform (static checks only; never planned or applied)
- [x] 5 Frontend (demo mode checked in a browser; real Cognito/API path untested)
- [x] 6 CI/CD (written + actionlint only; never run on GitHub)
- [x] 7 Hardening + eval (offline only; live tests written but NOT run)
- [x] 8 README + release

## Defaults chosen (change any time)

Region us-east-2 · env dev · Python 3.14 · Titan V2 @ 1024 dims · cosine ·
top_k 5 · ~800-token chunks, 100 overlap · 10 MB / 200-page limit.
No compiled dependencies (no pydantic/numpy) so the Lambda zip is portable.

## Auth decision (needs your input before Phase 3/4)

"Scoped to the authenticated owner" needs real identity. Options:
(a) Cognito user pool + JWT authorizer on the HTTP API (proper, a bit more setup);
(b) single-user demo with an API key header (simple, **not** production-secure).
**Decided: (a) Cognito** (user chose it).

## Phase 2 notes (core RAG, locally tested with fakes only)

Modules in `backend/src/docqa/`: `pdf.py` (validate + per-page text), `chunking.py`
(page-aware, deterministic IDs), `embeddings.py` (Titan V2), `vectorstore.py`
(S3 Vectors put/query/delete), `answer.py` (prompt, Converse call, citation validation).

**Assumptions that only a live AWS call can confirm** (all unit tests use fakes):
1. `put_vectors` overwrites an existing key (our idempotency relies on it).
2. Filter syntax `{"$and":[{"k":{"$eq":v}}, ...]}` is accepted by `query_vectors`.
3. The index can declare `text` as a non-filterable metadata key and return it.
4. Cosine `distance` scale, so `MAX_DISTANCE=0.9` is a guess until tuned (was 0.75; loosened in Phase 7).
5. `bedrock-runtime` `converse` works with your chosen generation model.
Unit tests prove our logic; they do NOT prove these five. Phase 7 checks them live.

Note: ruff (py314 target) rewrote `except (A, B):` as `except A, B:`. That is valid
Python 3.14 syntax (PEP 758), not a bug.

## Phase 3 notes (API + lifecycle, locally tested with fakes only)

Three Lambdas (separate IAM roles in Phase 4): `handlers/docs_api.py` (S3 + DynamoDB +
vector delete + invoke ingest), `handlers/query_api.py` (DynamoDB read + vector read +
Bedrock), `handlers/ingest_handler.py` (S3 read + Bedrock + vector write + DynamoDB).

| Route | Lambda | Notes |
|---|---|---|
| POST /documents/upload-url | docs | returns presigned PUT, 5 min, key chosen by server |
| GET /documents, GET /documents/{id} | docs | owner-scoped; foreign id => 404 |
| DELETE /documents/{id} | docs | DELETING first, then vectors, S3 object, record; retryable |
| POST /documents/{id}/reprocess | docs | async-invokes ingest; READY or FAILED only |
| GET /documents/{id}/download-url | docs | presigned GET; frontend appends `#page=N` |
| POST /query | query | READY docs only |

Identity = Cognito `sub` from the JWT claims API Gateway already verified (Phase 4
configures the authorizer). Without that authorizer every call is a 401.

Known limits (documented, not hidden):
* Presigned PUT cannot cap upload size; oversize files are rejected and deleted at ingest.
* A transient failure during a *reprocess* of an identical PDF deletes its (good) vectors
  before the automatic retry rebuilds them; the doc is FAILED in between, so it can't be queried.
* Metrics use CloudWatch EMF log lines; alarms on them come in Phase 4.
* Mutation-checked: removing the duplicate-event guard or the owner filter makes tests fail.

## Phase 4 notes (Terraform: validated statically, NOT planned or applied)

Two roots:
* `infra/bootstrap` (run once by a human admin, local state): state bucket (versioned,
  encrypted, S3-native locking), GitHub OIDC provider, `plan` role, `deploy` role,
  permissions boundary.
* `infra/` (the app): buckets, S3 Vectors bucket + index, DynamoDB, Cognito, 3 Lambdas
  with 3 separate least-privilege roles, HTTP API + JWT authorizer, CloudFront frontend,
  alarms, optional budget.

Verified: `terraform fmt -check`, `validate` (both roots, provider v6.71.0), Checkov
(0 failed; every skip carries a written reason in the .tf file), Lambda zip is
byte-reproducible. Provider schema was read directly: `aws_s3vectors_index` supports
`metadata_configuration.non_filterable_metadata_keys` (assumption 3, Terraform side).

NOT verified (needs AWS credentials): `terraform plan`/`apply`, IAM actions the provider
needs at apply time (the deploy role may need an added action on first run: read the
AccessDenied in CloudTrail and add exactly that), model availability.

Design decisions worth knowing:
* HTTP APIs have no request-validator feature (REST APIs do). Validation is done in the
  Lambdas (8 KB body cap, schema checks); API Gateway only caps at 10 MB and throttles.
* Sign-up is disabled: an admin creates users, so strangers can't spend your Bedrock budget.
* Retention: PDFs/vectors/records live until the user deletes the document (one API call
  removes all three). Logs expire after 30 days. Only abandoned multipart uploads expire.
* Docs-bucket versioning is off on purpose so deletes are real.
* CloudFront's default certificate can't enforce TLS 1.2 minimum (needs custom domain).
* No WAF (about $5+/month); throttling + Cognito are the abuse controls.

Replacement/deletion risks to check in every plan: changing `embed_dimensions`
replaces the vector index (all vectors lost, every document must be reprocessed);
changing a bucket name replaces the bucket; Cognito `username_attributes` changes
replace the user pool (all users lost). `force_destroy=false` protects non-empty
buckets from `terraform destroy` until you opt in.

## Phase 5 notes (frontend)

`frontend/` = React 19 + TypeScript + Vite. Verified: eslint, `tsc`, 23 Vitest tests
(upload validation/progress/failure, list states, ask loading/error/not-found, citation
click opens `#page=N`, blocked pop-up, sign-in + first-login new-password, polling, API
client error mapping), production build, `npm audit` (0 vulnerabilities), and a manual
look at demo mode in a browser (desktop + 375px mobile, no horizontal scroll).

How it fits together:
* **Runtime config** `public/config.json` (apiUrl, userPoolId, clientId): CI overwrites it
  from Terraform outputs at deploy time. Nothing secret; no Vite env vars are used.
* **Demo mode** (`"demo": true`, the committed default) uses an in-browser fake backend with
  crude keyword matching. It is labelled as such on screen. Deploy must set it to false.
* **Auth**: Cognito SRP via amazon-cognito-identity-js; the ID token is sent as the bearer
  token. Admin-created users are asked to choose a password on first sign-in.
* **Citations**: click -> `GET /documents/{id}/download-url` -> opens the presigned URL
  with `#page=N` (PDF viewers in Chrome/Edge/Firefox honour it; Safari may ignore it).
* **CSP** is injected into the production build only (dev server needs inline scripts).

Bug found only by running it in a browser: the Cognito library needs `global`, which browsers
lack; fixed with `define: { global: "globalThis" }` in vite.config.ts. Tests and the build
did not catch it.

Not verified: sign-in against real Cognito, real presigned PUT upload (needs the S3 bucket
CORS from Terraform to actually apply), CSP against real AWS endpoints, Safari.

## Phase 6 notes (CI/CD: written and statically linted, NEVER run on GitHub)

See `docs/CICD.md` for the manual setup, protections and rollback. Verified locally:
actionlint (with shellcheck) passes on all 3 workflows and flags a deliberately broken one;
the plan-review `jq` guard was run against a synthetic plan (flags a replaced vector index,
ignores a replaced Lambda); action input names were checked against each pinned action's
`action.yml`; Python lock files resolve to Linux wheels for py3.14; `pip-audit` finds no
known vulnerabilities in the Lambda dependencies; 13 tests cover the smoke-test script.

Unverified until a real run: that the pinned actions behave as expected on GitHub, that
Python 3.14 + the tools lock install cleanly on the runner, that the OIDC role assumption
works, and that the deploy role has every permission Terraform needs.
