# CI/CD guide

Status: workflows are written and **statically validated only** (actionlint). They have
**never run on GitHub** and nothing has been deployed. Expect to fix small things on the
first real run.

## The three workflows

| File | Trigger | Does | AWS access |
|---|---|---|---|
| `ci.yml` | every PR, push to `main`, and called by deploy | ruff, mypy, pytest · eslint, tsc, vitest, build, npm audit · terraform fmt/validate · Checkov, pip-audit, actionlint | **none** |
| `deploy.yml` | push to `main` (once `DEPLOY_ENABLED=true`) or manual | gate → CI → plan → **approval** → apply → frontend → smoke tests | OIDC: *plan* role (read-only), then *deploy* role |
| `teardown.yml` | manual only, typed confirmation | `terraform destroy` of the dev app | OIDC: deploy role via separate `dev-teardown` environment |

How AWS access works without keys: GitHub signs a short-lived token for each job saying
*"I am repo X, environment Y"*. AWS trusts GitHub as an identity provider and only lets the
token assume a role if those claims match the role's trust policy (set in
`infra/bootstrap`). Nothing secret is stored in GitHub; there are no repository *secrets*
at all, only non-secret *variables*.

## One-time setup (in this order)

1. **Push this repo to GitHub** (needs your account/permission):
   `gh repo create <owner>/document-qa-assistant --private --source . --push`
2. **Run the bootstrap once with admin credentials** (a human, locally; CI never does this):
   ```bash
   terraform -chdir=infra/bootstrap init
   terraform -chdir=infra/bootstrap plan  -var github_repository=<owner>/<repo>
   terraform -chdir=infra/bootstrap apply -var github_repository=<owner>/<repo>
   ```
   If your AWS account already has a GitHub OIDC provider add `-var create_oidc_provider=false`.
   Keep the local state file of this folder safe (it is git-ignored).
3. **Choose a Bedrock text model** and make sure it is enabled for your account in your
   Region (Bedrock console → Model access). Write down its ID.
4. **Repository variables** (Settings → Secrets and variables → Actions → *Variables*):

   | Variable | Value |
   |---|---|
   | `AWS_REGION` | e.g. `us-east-1` |
   | `TF_STATE_BUCKET` | bootstrap output `state_bucket` |
   | `AWS_PLAN_ROLE_ARN` | bootstrap output `plan_role_arn` |
   | `AWS_DEPLOY_ROLE_ARN` | bootstrap output `deploy_role_arn` |
   | `PERMISSIONS_BOUNDARY_ARN` | bootstrap output `permissions_boundary_arn` (**required**: the deploy role can only create roles that carry it) |
   | `GENERATION_MODEL_ID` | the model from step 3 |
   | `DEPLOY_ENABLED` | set to `true` last, to let pushes to `main` deploy |

5. **GitHub Environments** (Settings → Environments):
   * `dev`: *Required reviewers* = you (this is the approval gate); *Deployment branches* = `main` only.
   * `dev-teardown`: its own required reviewers; `main` only.
6. **Branch protection / ruleset on `main`**: require a pull request; require the status
   check **CI passed**; require branches to be up to date; block force pushes and
   deletion. (Rulesets/protection on private repos may need a paid GitHub plan.)
7. **First deploy**: Actions → *Deploy (dev)* → *Run workflow* on `main`. Read the plan in
   the run summary, then approve the `dev` environment when prompted.
8. **Create your first user** (sign-up is disabled on purpose):
   ```bash
   aws cognito-idp admin-create-user --user-pool-id <cognito_user_pool_id> \
     --username you@example.com --user-attributes Name=email,Value=you@example.com Name=email_verified,Value=true
   ```
   You get a temporary password by email and choose a real one at first sign-in.

## What protects the pipeline

* **Least privilege tokens**: workflow default is `contents: read`; only the plan, deploy
  and teardown jobs get `id-token: write`.
* **Pinned actions**: every third-party action is a full commit SHA (version in a comment).
  Dependabot proposes updates as PRs.
* **Trust conditions**: the deploy role only accepts tokens from *this repo* and the
  `dev` / `dev-teardown` environments; the plan role only from this repo's PRs and `main`.
  A fork or another repo cannot assume them.
* **Reviewed = applied**: the apply job runs the *saved plan file* and the exact Lambda zip
  from the plan job, not a fresh plan.
* **Destructive-change guard**: a plan that deletes or replaces the vector index/bucket,
  S3 buckets, DynamoDB table or Cognito pool fails unless you start a manual run with
  `allow_destructive=true`.
* **No credential leakage**: no secrets exist; values are passed via `env:` (never
  interpolated into scripts); checkouts use `persist-credentials: false`.
* The plan artifact (`tfplan-<run id>`) is a Terraform plan file. It holds no secrets in this
  project but is readable by anyone with repo read access; kept 30 days.

## Smoke tests (`scripts/smoke_test.py`)

After deploy it checks: site serves the app; `config.json` points at the real backend
(`demo` is false); security headers; API rejects anonymous calls (401); CORS allows only the
frontend origin; neither bucket is publicly readable. It retries for ~2 minutes because
CloudFront/API changes propagate slowly. **It does not prove uploads, ingestion or answers
work**: that needs a signed-in user and paid model calls (live integration test, Phase 7).
If a check fails the job fails and the run is red. The logic is unit-tested against a fake
deployment (`backend/tests/test_smoke_script.py`).

## Rollback

* **Code/infra**: Actions → *Deploy (dev)* → *Run workflow* → set `git_ref` to the last
  good commit SHA → approve. The plan will show what reverts. (CI checks run on the current
  branch head, not on the old ref: only roll back to commits that already passed CI.)
  If the rollback would delete/replace data-bearing resources the guard stops it unless you
  tick `allow_destructive`.
* **Frontend only**: same, the frontend is rebuilt from `git_ref`.
* **Data is not rolled back**: PDFs, vectors and records are not versioned. Deleting a
  vector index loses its vectors: documents must be *reprocessed* (API `reprocess`).
* **Corrupt state**: the state bucket is versioned; restore a previous version of
  `document-qa-assistant/dev/terraform.tfstate` in S3.
* Each run keeps `deployment-<run id>` (outputs + deployed SHA) for 30 days as evidence of
  what was live.

## Not covered

* Multi-environment (staging/prod): only `dev` exists.
* Authenticated end-to-end smoke test in CI (would need a test user's credentials stored
  somewhere; deliberately avoided).
* Running the Python lock-file updates automatically.
