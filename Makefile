# Google Ads AI Agent V1 — common commands
.PHONY: lock wheelhouse up down migrate purge-demo-data dev-backend dev-worker dev-beat dev-frontend test test-backend test-frontend e2e lint live-tests

# CPU architecture of the Docker image (override with WHEEL_ARCH=aarch64 or x86_64 if your Docker runs a different one than this computer).
WHEEL_ARCH ?= $(shell uname -m | sed -e 's/arm64/aarch64/' -e 's/amd64/x86_64/')

up:            ## build and start everything with Docker Compose 
	docker compose up --build -d
down:
	docker compose down
lock:          ## regenerate backend/requirements.lock after changing dependencies in backend/pyproject.toml (needs uv)
	cd backend && uv pip compile pyproject.toml docker/build-requirements.in --extra dev --python-platform linux --python-version 3.12 \
		--generate-hashes --no-header -q -o requirements.lock
	cd backend && sed -i.bak '1i # Locked dependencies for the Docker image (Linux, Python 3.12): runtime + dev + the build backend, with hashes.\n# Generated from pyproject.toml. Do not edit by hand; run `make lock` after changing dependencies in pyproject.toml.' requirements.lock && rm -f requirements.lock.bak
wheelhouse:    ## download every locked wheel into backend/wheelhouse with THIS computer's connection; the next image build then needs no package index
	python3 -m pip download --prefer-binary --require-hashes --no-deps --python-version 3.12 --implementation cp --abi cp312 --abi abi3 --abi none \
		$(foreach p,manylinux_2_34 manylinux_2_28 manylinux_2_27 manylinux_2_26 manylinux_2_24 manylinux_2_17 manylinux_2_12 manylinux_2_5 manylinux2014 manylinux2010 manylinux1,--platform $(p)_$(WHEEL_ARCH)) \
		--timeout 30 --retries 3 -r backend/requirements.lock -d backend/wheelhouse
migrate:
	cd backend && .venv/bin/alembic upgrade head
purge-demo-data: ## list leftover demo data from older versions (add ARGS=--apply to delete it)
	cd backend && .venv/bin/python -m app.cli purge-demo-data $(ARGS)
dev-backend:
	cd backend && .venv/bin/uvicorn app.main:app --reload --port 8000
dev-worker:
	cd backend && .venv/bin/celery -A app.workers.celery_app worker -l info
dev-beat:
	cd backend && .venv/bin/celery -A app.workers.celery_app beat -l info
dev-frontend:
	cd frontend && npm run dev
test: test-backend test-frontend
test-backend:  ## needs a disposable Postgres database (TEST_DATABASE_URL); it is wiped and loaded with test fixtures
	cd backend && .venv/bin/pytest
test-frontend:
	cd frontend && npx tsc -b && npx vitest run
e2e:           ## browser test against a running stack (default http://localhost:5173)
	NODE_PATH=$$(npm root -g) node e2e/ui-smoke.mjs
lint:
	cd backend && .venv/bin/ruff check app tests --select F,E9

live-tests:    ## real-API tests (read-only); report PASSED / NOT CONFIGURED / BLOCKED
	bash scripts/live-tests.sh
