PY=.venv/bin

.PHONY: test lint typecheck check
test:
	$(PY)/pytest

lint:
	$(PY)/ruff check backend scripts && $(PY)/ruff format --check backend scripts

typecheck:
	$(PY)/mypy backend/src

check: lint typecheck test

.PHONY: build-lambda tf-check
build-lambda:
	$(PY)/python scripts/build_lambda.py

# Static infra checks: no AWS credentials needed.
tf-check: build-lambda
	terraform -chdir=infra fmt -check -recursive
	terraform -chdir=infra init -backend=false -input=false >/dev/null
	terraform -chdir=infra validate
	terraform -chdir=infra/bootstrap init -backend=false -input=false >/dev/null
	terraform -chdir=infra/bootstrap validate
	.venv-tools/bin/checkov -d infra --framework terraform --quiet --compact

.PHONY: frontend-check
frontend-check:
	cd frontend && npm ci && npm run lint && npm run typecheck && npm test && npm run build

.PHONY: workflows-check smoke-test
workflows-check:
	.venv-tools/bin/actionlint

# make smoke-test OUTPUTS=outputs.json   (after a deploy: terraform -chdir=infra output -json > outputs.json)
smoke-test:
	$(PY)/python scripts/smoke_test.py $(OUTPUTS)
