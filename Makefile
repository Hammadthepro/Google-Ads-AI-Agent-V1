# Google Ads AI Agent V1 — common commands
.PHONY: up down migrate seed reset-mock dev-backend dev-worker dev-beat dev-frontend test test-backend test-frontend e2e lint

up:            ## build and start everything with Docker Compose (mock mode)
	docker compose up --build -d
down:
	docker compose down
migrate:
	cd backend && .venv/bin/alembic upgrade head
seed:          ## seed demo data + simulated world (only if empty)
	cd backend && .venv/bin/python -m app.cli seed
reset-mock:    ## wipe all data except users and reseed deterministically
	cd backend && .venv/bin/python -m app.cli reset-mock
dev-backend:
	cd backend && .venv/bin/uvicorn app.main:app --reload --port 8000
dev-worker:
	cd backend && .venv/bin/celery -A app.workers.celery_app worker -l info
dev-beat:
	cd backend && .venv/bin/celery -A app.workers.celery_app beat -l info
dev-frontend:
	cd frontend && npm run dev
test: test-backend test-frontend
test-backend:  ## needs a disposable Postgres database (TEST_DATABASE_URL); it is wiped and reseeded
	cd backend && .venv/bin/pytest
test-frontend:
	cd frontend && npx tsc -b && npx vitest run
e2e:           ## browser test against a running stack (default http://localhost:5173)
	NODE_PATH=$$(npm root -g) node e2e/ui-smoke.mjs
lint:
	cd backend && .venv/bin/ruff check app tests --select F,E9
