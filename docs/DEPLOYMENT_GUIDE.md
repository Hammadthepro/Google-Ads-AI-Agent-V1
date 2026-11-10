> **Note:** the app is now real-integration-only (no mock mode, no demo data). Mentions of mock mode, seeding or demo users below are historical; see [REAL_DATA_ONLY.md](REAL_DATA_ONLY.md).

# Deployment & Setup Guide

Everything runs in **mock mode** with no external credentials. Real Google Ads writes stay disabled.

## 1. Quick start — Docker Compose (recommended)
```bash
git clone https://github.com/Hammadthepro/Google-Ads-AI-Agent-V1.git
cd Google-Ads-AI-Agent-V1
cp .env.example .env            # set APP_SECRET_KEY (any long random string)
docker compose up --build -d    # postgres, redis, backend (runs migrations), worker, beat, frontend
docker compose exec backend python -m app.cli seed
```
Open **http://localhost:5173** and pick a demo user on the development login screen.

| Demo user | Role |
|---|---|
| superadmin@napollo.example | Super Admin |
| admin@napollo.example, backup.admin@napollo.example | Admin |
| manager@napollo.example | Campaign Manager (Northside, Brightsmile) |
| analyst@napollo.example | Analyst (read-only) |

## 2. Local development (no Docker)
Requirements: Python 3.11+, Node 20+, PostgreSQL 14+, Redis 6+.
```bash
cd backend && python -m venv .venv && .venv/bin/pip install -e ".[dev]"
export DATABASE_URL=postgresql+psycopg://app:app@localhost:5432/google_ads_agent
.venv/bin/alembic upgrade head
.venv/bin/python -m app.cli seed
.venv/bin/uvicorn app.main:app --reload --port 8000
.venv/bin/celery -A app.workers.celery_app worker -l info     # separate terminal
.venv/bin/celery -A app.workers.celery_app beat -l info       # separate terminal
cd ../frontend && npm install && npm run dev                  # http://localhost:5173 (proxies /api to :8000)
```
Without a Celery worker, set `CELERY_TASK_ALWAYS_EAGER=true`. Tasks then run in a background thread of the API process.

Useful commands: `make reset-mock` (deterministic reseed), `python -m app.cli advance 7` (advance the simulated clock 7 days and run the daily jobs), `python -m app.cli create-super-admin you@company.com`.

## 3. Supabase as the primary database
Project: **google-ads-ai-agent-v1** (ref `extfrctastbodivhiiei`, us-east-1). Schema already applied (Alembic head `0003`).

1. In Supabase → Project Settings → Database, **reset the database password** (the old one was shared in chat).
2. Put the Session Pooler URL in your `.env` / server secrets, never in code. Percent-encode special characters (`@` → `%40`):
   ```
   DATABASE_URL=postgresql+psycopg://postgres.extfrctastbodivhiiei:<url-encoded-password>@aws-0-us-east-1.pooler.supabase.com:5432/postgres?sslmode=require
   ```
3. Verify connectivity and migration state from your machine:
   ```bash
   cd backend && .venv/bin/alembic current        # expect: 0003 (head)
   .venv/bin/alembic check                        # expect: No new upgrade operations detected.
   curl -s localhost:8000/readyz                  # after starting the API: database ok
   ```
4. Seed demo data into Supabase only if you want it there: `python -m app.cli seed`. Do **not** run `reset-mock` against a database holding real data, because it truncates the application tables.
5. Future schema changes: `alembic revision --autogenerate -m "..."`, review it, then `alembic upgrade head`. Migrations never drop existing objects unless a reviewed migration says so.

Security model on Supabase: RLS is enabled on every table with no policies, and the `anon`/`authenticated` roles have no grants. The public Data API cannot read anything. The backend connects as the table owner and enforces authorization itself.

## 4. Production checklist
- `APP_ENV=production`, `TEST_MODE=false`, `AUTH_PROVIDER=google`, strong `APP_SECRET_KEY`, `TOKEN_ENCRYPTION_KEY` (Fernet key), `SESSION_COOKIE_SECURE=true`, `CORS_ORIGINS` set to your domain. The app refuses to start with mock login or TEST_MODE in production.
- TLS reverse proxy (Caddy/Nginx) in front of the frontend container; keep the backend unexposed.
- Secrets in a secrets manager or a root-only env file; never baked into images.
- Daily database backups (Supabase provides them on paid plans), restore-tested.
- Keep `GOOGLE_ADS_WRITES_ENABLED=false` until REAL_API_INTEGRATION_PLAN.md §4 is complete.

## 5. Live preview inside Claude Code (this session)
While the Claude Code session is running, the dev server is on port **5173** and reported to the app's preview panel. It disappears when the session ends; use §1 for a lasting install.
