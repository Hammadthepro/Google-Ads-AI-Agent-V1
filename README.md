# Google Ads AI Agent — V1

An AI-assisted, **safety-first** Google Ads management platform for marketing teams. It covers client onboarding, a conversion-tracking gate, website / Business Profile / competitor audits, keyword research, budget feasibility, AI campaign drafting with an independent AI challenger, deterministic validation, human approval of the exact payload, paused-first execution with read-back verification, monitoring, an emergency brake, weekly reviews, A/B experiments and a learning loop.

> **Core rule:** nothing changes in Google Ads without approval, except safe negative keywords after every check passes. V1 runs those in shadow mode too. **Real Google Ads writes are disabled.**

## Status
The application runs **only on real integrations**: there is no mock mode and no demo data. Anything that needs Google Ads, Groq, Claude, Places, PageSpeed or SMTP says so until you configure it on the **Integrations** page. Test fixtures live only under `backend/tests/fakes`. GitHub Actions CI (backend, frontend, Docker image build) and a dev-container workflow that builds `.devcontainer` and runs the Codespaces startup sequence. See [docs/TEST_RESULTS.md](docs/TEST_RESULTS.md) and [docs/KNOWN_ISSUES.md](docs/KNOWN_ISSUES.md).

## Run it in GitHub Codespaces (recommended)
1. On the repository page, click the green **Code** button → **Codespaces** tab → **Create codespace on main**.
   For a bigger machine: **⋯ → New with options…** → 4-core or larger.
2. Wait for the codespace to start. It uses the official `mcr.microsoft.com/devcontainers/universal:6-noble` image, which already contains Docker Engine, Docker Compose v2, Python and Node, so nothing is installed while the Codespace is created. Then `scripts/start.sh` runs automatically. It waits for the Docker daemon, creates `.env` (writes disabled), builds and starts the six services (postgres, redis, backend with automatic database migrations, worker, beat, frontend), waits for the API health check and makes sure the first Super Admin exists (`BOOTSTRAP_ADMIN_EMAIL` in `.env`). No data is created. **The first start takes about 5–10 minutes**; follow it in the terminal ("postStartCommand").
3. When the terminal prints **"Ready."**, open the **Ports** tab (bottom panel) and click the 🌐 globe icon next to **5173 – Web app (frontend)**. It usually opens automatically.
4. Set `BOOTSTRAP_ADMIN_EMAIL=you@example.com` in `.env` (or run `docker compose exec backend python -m app.cli create-super-admin you@example.com`), then sign in with that email. Open **Integrations** and enter your credentials: Google Ads (OAuth), Groq, Claude, Places, PageSpeed, SMTP. Each has a **Test Connection** button that makes a real request.

If the automatic start did not run, or you stopped it, start it manually in the codespace terminal:
```bash
bash scripts/start.sh
```

### Sign-in
Local development uses an email-only sign-in for users that already exist (`AUTH_PROVIDER=dev`; refused when `APP_ENV=production`, where Google sign-in is required). Invite teammates from **Users**; roles are Super Admin, Admin, Campaign Manager and Analyst. Approving your own request is blocked.

### Useful commands (codespace terminal)
```bash
docker compose ps                                        # all 6 services should be running/healthy
curl -s localhost:8000/readyz                            # {"status":"ready",...}
docker compose logs -f backend                           # API logs (also: worker, beat, frontend)
docker compose exec backend python -m app.cli purge-demo-data          # list leftover demo data from older versions
docker compose exec backend python -m app.cli purge-demo-data --apply  # delete it (real clients are never matched)
docker compose down                                      # stop (data kept in the pgdata volume)
docker compose down -v && bash scripts/start.sh     # start completely fresh
```
API docs: open port **8000** and go to `/api/docs`.

### Check that the Codespace is healthy
```bash
docker version && docker compose version      # Docker CLI, daemon and Compose v2 are present
docker compose ps                             # 6 services: postgres, redis, backend, worker, beat, frontend
curl -s localhost:8000/readyz                 # {"status":"ready","checks":{"database":"ok","redis":"ok"}}
docker compose exec backend alembic current   # 0003 (head)
```

### Troubleshooting
| Symptom | Fix |
|---|---|
| Port 5173 shows "502 Bad Gateway" or doesn't load | The stack is still starting. Wait for "Ready." or run `bash scripts/start.sh`; check `docker compose ps`. |
| Codespace opens in **recovery mode** / `docker: command not found` | The dev container failed to build, so Codespaces started a minimal fallback container without Docker. F1 → **Codespaces: View Creation Log** shows why. Make sure the codespace was created from the latest `main` (older commits used a Docker-in-Docker feature that failed on a Yarn APT key, `NO_PUBKEY 62D54FD4003F6525`), then F1 → **Codespaces: Full Rebuild Container**, or delete the codespace and create a new one. |
| "Cannot connect to the Docker daemon" | Docker starts with the container; `start.sh` waits up to 3 minutes. If it still fails: F1 → **Codespaces: Rebuild Container**. |
| API never becomes ready | `docker compose logs --tail=100 backend`. Usually Postgres is still starting; rerun `bash scripts/start.sh`. |
| Sign-in page says "This account has not been invited" | Create the first admin: `docker compose exec backend python -m app.cli create-super-admin you@example.com`. |
| "Too many requests" | The sign-in rate limit is 30/min per IP. Wait a minute, or raise `AUTH_RATE_LIMIT_PER_MINUTE` in `.env` and run `docker compose up -d`. |
| Changed `.env` but nothing happened | `docker compose up -d --force-recreate backend worker beat` |
| Build fails with "no space left on device" | `docker system prune -af`, then start again (or use a larger machine). |
| You want a clean slate | `docker compose down -v && bash scripts/start.sh` |

Forwarded ports are **private** by default: only your GitHub account can open them. Keep them private; the development sign-in has no password.

## Run it locally with Docker
```bash
git clone https://github.com/Hammadthepro/Google-Ads-AI-Agent-V1.git && cd Google-Ads-AI-Agent-V1
bash scripts/start.sh          # then open http://localhost:5173
```
Development without Docker, Supabase configuration and production notes: [docs/DEPLOYMENT_GUIDE.md](docs/DEPLOYMENT_GUIDE.md).

## First 10 minutes
1. Sign in → **Integrations**. Save Groq (API key + model) and run **Test Connection**; save Google Ads (OAuth client ID/secret, then **Authorise** to obtain the refresh token) and run **Test Connection**. The card shows the exact step that failed, the HTTP status and what to do.
2. **Clients** → add a client (business, services, website, budget, targets). Link its Google Ads customer ID. Until Google Ads is connected, pages that need account data show *"Google Ads integration is not configured. Please configure and test your connection on the Integrations page."*
3. Run the audit and a campaign task. Every result states its data source, period and retrieval time; metrics that could not be retrieved are listed as unavailable, never estimated silently.
4. Real Google Ads **writes** stay impossible unless both safety keys are on (`GOOGLE_ADS_WRITES_ENABLED=true` and a Super Admin authorisation) and the operating mode is *Validated execution*. Campaigns are created **PAUSED**; enabling needs its own approval.

## Architecture
React + TypeScript + Vite + Tailwind → FastAPI (modular monolith) → PostgreSQL / Supabase, with Celery + Redis for background jobs. Every external service sits behind an adapter interface with a real implementation; `backend/app/adapters/registry.py` builds only real adapters and raises `IntegrationNotConfigured` (HTTP 424) when credentials are missing. There is no fallback to simulated data or to another provider. AI uses Groq and Claude, and every AI output is validated against strict schemas. Metrics, budgets, gates and approvals are computed by code, never by AI.

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
