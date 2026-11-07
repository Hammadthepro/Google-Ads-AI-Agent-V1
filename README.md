# Google Ads AI Agent — V1

An AI-assisted, **safety-first** Google Ads management platform for marketing teams. It covers client onboarding, a conversion-tracking gate, website / Business Profile / competitor audits, keyword research, budget feasibility, AI campaign drafting with an independent AI challenger, deterministic validation, human approval of the exact payload, paused-first execution with read-back verification, monitoring, an emergency brake, weekly reviews, A/B experiments and a learning loop.

> **Core rule:** nothing changes in Google Ads without approval, except safe negative keywords after every check passes. V1 runs those in shadow mode too. **Real Google Ads writes are disabled.**

## Status
V1 is implemented and tested in **mock mode**. Backend: 81 passed, 1 skipped (live Groq, not configured). Frontend: 13 passed. Browser E2E: passed. See [docs/TEST_RESULTS.md](docs/TEST_RESULTS.md) and [docs/KNOWN_ISSUES.md](docs/KNOWN_ISSUES.md).

## Quick start
```bash
cp .env.example .env                 # set APP_SECRET_KEY
docker compose up --build -d
docker compose exec backend python -m app.cli seed
# open http://localhost:5173 and choose a demo user
```
Development without Docker, Supabase configuration and production notes: [docs/DEPLOYMENT_GUIDE.md](docs/DEPLOYMENT_GUIDE.md).

## A 5-minute tour (mock data)
1. Sign in as **manager@napollo.example** → **Tasks** → *Northside Home Services* → *Build a new campaign* → **Run**. Watch the tracking gate, parallel audits, keyword research, feasibility, AI draft, challenger and validation run, ending at an approval.
2. Sign in as **admin@napollo.example** → **Approvals** → open the card → **Approve this version** → **Execute**. The campaign is created **PAUSED** in the simulated account and verified by reading it back.
3. A separate **Enable** approval appears. Enabling is impossible until it is approved.
4. Try *Brightsmile Dental* (tracking **RED**: the build is blocked until an admin override), the **Monitor** (wasted spend, emergency-brake events, manual changes), and the **Weekly Review**.
5. As **superadmin@napollo.example** → **System Settings**, you can advance the simulated clock, inject failures (timeouts, outages, invalid AI JSON) and flip kill switches.

## Architecture
React + TypeScript + Vite + Tailwind → FastAPI (modular monolith) → PostgreSQL / Supabase, with Celery + Redis for background jobs. Every external service sits behind an adapter interface with mock and real implementations (`backend/app/adapters/registry.py` is the only switch point; real mode is never inferred from credentials). AI uses Groq (or the deterministic MockAIProvider), and every AI output is validated against strict schemas. Metrics, budgets, gates and approvals are computed by code, never by AI.

## Documentation
| | |
|---|---|
| [PROJECT_OVERVIEW](docs/PROJECT_OVERVIEW.md) | Scope, flow, conflicts resolved |
| [SYSTEM_ARCHITECTURE](docs/SYSTEM_ARCHITECTURE.md) | Components, adapters, write-safety chain |
| [MODULE_BREAKDOWN](docs/MODULE_BREAKDOWN.md) | Modules and task-type flows |
| [DATABASE_SCHEMA](docs/DATABASE_SCHEMA.md) | Entities |
| [API_ENDPOINTS](docs/API_ENDPOINTS.md) | REST API (OpenAPI at `/api/docs`) |
| [MOCK_API_GUIDE](docs/MOCK_API_GUIDE.md) | Simulated world, scenarios, failure injection |
| [REAL_API_INTEGRATION_PLAN](docs/REAL_API_INTEGRATION_PLAN.md) | **How to connect real credentials** |
| [AUTHENTICATION_AND_ROLES](docs/AUTHENTICATION_AND_ROLES.md) | Login, sessions, permissions |
| [GOOGLE_ADS_SAFETY_RULES](docs/GOOGLE_ADS_SAFETY_RULES.md) | Non-negotiable safety rules |
| [APPROVAL_WORKFLOW](docs/APPROVAL_WORKFLOW.md) | Approval state machine |
| [TESTING_STRATEGY](docs/TESTING_STRATEGY.md) / [TEST_RESULTS](docs/TEST_RESULTS.md) | Tests |
| [DEPLOYMENT_GUIDE](docs/DEPLOYMENT_GUIDE.md) / [ENVIRONMENT_VARIABLES](docs/ENVIRONMENT_VARIABLES.md) | Setup |
| [DECISIONS_REQUIRED](docs/DECISIONS_REQUIRED.md) | Defaults applied (test values) |
| [KNOWN_ISSUES](docs/KNOWN_ISSUES.md) | Limitations, unverified integrations |
| [IMPLEMENTATION_PROGRESS](docs/IMPLEMENTATION_PROGRESS.md) | Phase tracker |

All clients, businesses, people, reviews and `*.example` domains in the mock data are fictional.
