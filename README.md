# Google Ads AI Agent — V1

An AI-assisted, **safety-first** Google Ads management platform for marketing teams. It covers client onboarding, a conversion-tracking gate, website / Business Profile / competitor audits, keyword research, budget feasibility, AI campaign drafting with an independent AI challenger, deterministic validation, human approval of the exact payload, paused-first execution with read-back verification, monitoring, an emergency brake, weekly reviews, A/B experiments and a learning loop.

> **Core rule:** nothing changes in Google Ads without approval, except safe negative keywords after every check passes. V1 runs those in shadow mode too. **Real Google Ads writes are disabled.**

## Status
V1 is implemented and tested in **mock mode**. Backend: 83 passed, 1 skipped (live Groq, not configured). Frontend: 13 passed. Browser E2E: passed. See [docs/TEST_RESULTS.md](docs/TEST_RESULTS.md) and [docs/KNOWN_ISSUES.md](docs/KNOWN_ISSUES.md).

## Run it in GitHub Codespaces (recommended)
1. On the repository page, click the green **Code** button → **Codespaces** tab → **Create codespace on main**.
   For a bigger machine: **⋯ → New with options…** → 4-core or larger.
2. Wait for the codespace to finish building. The dev container installs Docker, Python 3.12 and Node 20. Then `scripts/start-demo.sh` runs automatically: it creates `.env` (mock mode, writes disabled), builds and starts all containers, waits for the API and seeds the demo data. **The first start takes about 5–10 minutes**; watch the progress in the terminal ("postStartCommand").
3. When the terminal prints **"Ready."**, open the **Ports** tab (bottom panel) and click the 🌐 globe icon next to **5173 – Web app (frontend)**. It usually opens automatically.
4. On the sign-in page, click a demo user.

If the automatic start did not run, or you stopped it, start it manually in the codespace terminal:
```bash
bash scripts/start-demo.sh
```

### Demo logins (development sign-in, no password, mock mode only)
| User | Role | Can |
|---|---|---|
| `superadmin@napollo.example` | Super Admin | everything, incl. settings, kill switches, mock controls |
| `admin@napollo.example` | Admin | approve / reject / execute changes |
| `backup.admin@napollo.example` | Admin | backup approver (escalations) |
| `manager@napollo.example` | Campaign Manager | run tasks and submit approvals (Northside, Brightsmile only) |
| `analyst@napollo.example` | Analyst | read-only |

Approving your own request is blocked, so run a task as the manager and approve as the admin.

### Useful commands (codespace terminal)
```bash
docker compose ps                                        # all 6 services should be running/healthy
curl -s localhost:8000/readyz                            # {"status":"ready",...}
docker compose logs -f backend                           # API logs (also: worker, beat, frontend)
docker compose exec backend python -m app.cli seed       # seed demo data (no-op if already seeded)
docker compose exec backend python -m app.cli reset-mock # wipe & deterministically reseed (keeps users)
docker compose exec backend python -m app.cli advance 7  # move the simulated clock 7 days, run daily jobs
docker compose down                                      # stop (data kept in the pgdata volume)
docker compose down -v && bash scripts/start-demo.sh     # start completely fresh
```
API docs: open port **8000** and go to `/api/docs`.

### Troubleshooting
| Symptom | Fix |
|---|---|
| Port 5173 shows "502 Bad Gateway" or doesn't load | The stack is still starting. Wait for "Ready." or run `bash scripts/start-demo.sh`; check `docker compose ps`. |
| `docker: command not found` or "Cannot connect to the Docker daemon" | The dev container is still provisioning. Wait, or run **Codespaces: Rebuild Container** from the command palette (F1). |
| API never becomes ready | `docker compose logs --tail=100 backend`. Usually Postgres is still starting; rerun `bash scripts/start-demo.sh`. |
| Sign-in page says "This account has not been invited" | Seed didn't run: `docker compose exec backend python -m app.cli seed`. |
| "Too many requests" | The sign-in rate limit is 30/min per IP. Wait a minute, or raise `AUTH_RATE_LIMIT_PER_MINUTE` in `.env` and run `docker compose up -d`. |
| Changed `.env` but nothing happened | `docker compose up -d --force-recreate backend worker beat` |
| Build fails with "no space left on device" | `docker system prune -af`, then start again (or use a larger machine). |
| You want a clean slate | `docker compose down -v && bash scripts/start-demo.sh` |

Forwarded ports are **private** by default: only your GitHub account can open them. Keep them private; the development sign-in has no password.

## Run it locally with Docker
```bash
git clone https://github.com/Hammadthepro/Google-Ads-AI-Agent-V1.git && cd Google-Ads-AI-Agent-V1
bash scripts/start-demo.sh          # then open http://localhost:5173
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
