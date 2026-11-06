# Deployment Guide

Status: **Phase 0 — plan.** Commands below do not work yet.

## Local (Phase 1+)
1. Copy `.env.example` → `.env`; generate `APP_SECRET_KEY`.
2. `docker compose up --build` → frontend :5173, backend :8000, Postgres, Redis, worker, beat.
3. `make migrate && make seed` → mock world + demo users.
4. Open the app, use the dev login.

Note: in the Claude Code cloud container the Docker daemon is not running, so development tests here will use natively installed PostgreSQL 16 and Redis 7. Compose will be verified on a machine with Docker.

## Production (Phase 16 plan)
- Single VPS (D-23) with Docker Compose, reverse proxy (Caddy/Nginx) with TLS, managed or backed-up Postgres.
- Secrets from a secrets manager / root-only env file; never in images.
- `APP_ENV=production` refuses to start with `AUTH_PROVIDER=mock`, `TEST_MODE=true` or missing `APP_SECRET_KEY`/`TOKEN_ENCRYPTION_KEY`.
- Real writes remain off until explicitly authorized (GOOGLE_ADS_SAFETY_RULES.md §2).
- Daily DB backups, restore test before go-live; health checks `/healthz`, `/readyz`.
