# Google Ads AI Agent — V1

An AI-assisted, safety-first Google Ads management platform for a digital marketing team: client onboarding, conversion-tracking gate, website / Business Profile / competitor audits, keyword research, budget feasibility, AI campaign drafting with an independent AI challenger, deterministic validation, human approval, paused-first campaign execution, monitoring, emergency brake, weekly reporting and a learning loop.

## Current status

**Phase 0 — Architecture & planning. No application code exists yet.**

See [`docs/IMPLEMENTATION_PROGRESS.md`](docs/IMPLEMENTATION_PROGRESS.md) for the live phase tracker and [`docs/DECISIONS_REQUIRED.md`](docs/DECISIONS_REQUIRED.md) for open decisions that block later phases.

## Core safety rule

> Nothing changes in Google Ads without approval, except specifically approved safe negative-keyword operations after all safety checks pass.

V1 starts in **MOCK** mode. Real Google Ads writes are disabled in backend code (`GOOGLE_ADS_WRITES_ENABLED=false`) and additionally require a database-level authorization by a Super Admin. See [`docs/GOOGLE_ADS_SAFETY_RULES.md`](docs/GOOGLE_ADS_SAFETY_RULES.md).

## Source documents and precedence

1. **Master Project Prompt** (mock-first development brief) — highest precedence.
2. **Google Ads AI Agent — V1 Technical Architecture** (PDF, step-by-step flow 1–14).

Where they conflict, the Master Prompt wins and the conflict is recorded in `docs/PROJECT_OVERVIEW.md` §5.

## Planned stack

| Layer | Technology |
|---|---|
| Frontend | React, TypeScript, Vite, Tailwind CSS |
| Backend | Python, FastAPI, Pydantic v2, SQLAlchemy 2, Alembic |
| Jobs | Celery + Redis |
| Database | PostgreSQL |
| Crawler | httpx, BeautifulSoup + lxml, Playwright, urllib.robotparser, extruct |
| Google Ads | Official `google-ads` Python library (real adapter only) |
| AI | Groq SDK (`groq`) + MockAIProvider |
| Deployment | Docker, Docker Compose |

Architecture style: **modular monolith** with adapter interfaces and dependency injection. No microservices.

## Documentation index

| Document | Purpose |
|---|---|
| [PROJECT_OVERVIEW](docs/PROJECT_OVERVIEW.md) | Scope, objectives, conflicts resolved |
| [SYSTEM_ARCHITECTURE](docs/SYSTEM_ARCHITECTURE.md) | Components, folder structure, adapters, safety chain |
| [MODULE_BREAKDOWN](docs/MODULE_BREAKDOWN.md) | Every module, its responsibilities and phase |
| [DATABASE_SCHEMA](docs/DATABASE_SCHEMA.md) | Entity plan |
| [API_ENDPOINTS](docs/API_ENDPOINTS.md) | Planned REST surface |
| [MOCK_API_GUIDE](docs/MOCK_API_GUIDE.md) | Mock adapters, seeded scenarios, failure injection |
| [REAL_API_INTEGRATION_PLAN](docs/REAL_API_INTEGRATION_PLAN.md) | Accounts, credentials, replacement checklists |
| [AUTHENTICATION_AND_ROLES](docs/AUTHENTICATION_AND_ROLES.md) | Login, sessions, permission matrix |
| [GOOGLE_ADS_SAFETY_RULES](docs/GOOGLE_ADS_SAFETY_RULES.md) | Non-negotiable write safety |
| [APPROVAL_WORKFLOW](docs/APPROVAL_WORKFLOW.md) | Approval state machine and payload hashing |
| [TESTING_STRATEGY](docs/TESTING_STRATEGY.md) | Test layers and the 32 required scenarios |
| [TEST_RESULTS](docs/TEST_RESULTS.md) | Actual test runs (none yet) |
| [DEPLOYMENT_GUIDE](docs/DEPLOYMENT_GUIDE.md) | Local and production deployment (planned) |
| [ENVIRONMENT_VARIABLES](docs/ENVIRONMENT_VARIABLES.md) | Every configuration variable |
| [DECISIONS_REQUIRED](docs/DECISIONS_REQUIRED.md) | Open business/technical decisions + risks |
| [IMPLEMENTATION_PROGRESS](docs/IMPLEMENTATION_PROGRESS.md) | Phase tracker |

## Running the project

Not yet runnable. Phase 1 will add `docker-compose.yml`, `.env.example`, Dockerfiles and documented commands.
