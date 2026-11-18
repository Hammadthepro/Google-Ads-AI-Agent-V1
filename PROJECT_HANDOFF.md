# Project Handoff — Google Ads AI Agent V1

Prepared 2026-11-18 for continuing this project in a new Claude Code session (company Claude account).
Repository: `github.com/Hammadthepro/google-ads-ai-agent-v1` · branch `main` · latest commit `321d5b8`.

Every statement below is marked when it could **not** be verified from this session. "Verified" means checked against the
repository, GitHub, the Supabase project or a test run on 2026-11-17/18. No secret values appear in this document.

---

## 0. Read this first (the five things most likely to bite)

1. **The development workflow is fixed by the owner** (in `CLAUDE.md`): work directly on `main`, no feature branches, no pull
   requests, `git pull --ff-only origin main` first, run all checks, then commit and push to `origin/main`. Never force-push or reset.
   Stop and report if the tree has changes you did not make. (See §11 for the full rule set.)
2. **GitHub CI has been red on `main` since commit `c78b30a`** (9 consecutive runs). Cause, verified: the app imports the `anthropic`
   SDK (`backend/app/adapters/ai/claude.py`) and two tests import `httpx2`, but **neither is declared in `backend/pyproject.toml` or
   `backend/requirements.lock`**. They exist only in the local `.venv`. CI result: 608 passed, 2 failed, 1 skipped. The same gap
   means the **Docker image has no `anthropic` package**, so a Claude-backed AI role will fail in Docker. This is the first fix to make
   (§12, step 1).
3. **Supabase is 4 migrations behind.** Verified: project `google-ads-ai-agent-v1` (ref `extfrctastbodivhiiei`) has migrations up to
   **0012**. The code is at **0016** (0013 AI run diagnostics, 0014 AI roles, 0015 token caps, 0016 Challenger resolutions and plan
   evidence). If the deployed app points at Supabase, it will fail on those tables and columns.
4. **Google Ads writes are disabled and must stay disabled** unless the owner explicitly says otherwise:
   `GOOGLE_ADS_WRITES_ENABLED=false`, `AUTO_NEGATIVES_MODE=shadow`, `EMERGENCY_BRAKE_WRITE_MODE=simulate`. Never publish campaigns,
   never change billing.
5. **Two external accounts block real keyword data today:** the DataForSEO account must be verified (it returns status 40104), and
   the Google Cloud project has only **Explorer** Google Ads API access, so Keyword Planner is refused. Until both are fixed, keyword
   volume, CPC and bids are honestly reported as "not measured". **Do not claim DataForSEO metrics work until tested with a verified
   account.**

---

## 1. Project overview and objectives

An AI-assisted, **safety-first Google Ads management platform** for a digital marketing agency (Napollo Software Design) managing
lead-generation **Google Search** campaigns for local-service clients, mainly in the USA.

It covers the whole lifecycle:

- Client onboarding (client profile, services, competitors, service areas, business hours).
- Audits: website, Business Profile, competitors, conversion-measurement audit.
- Keyword research with measured metrics only.
- Budget feasibility and financial modelling.
- AI campaign drafting with an independent **Challenger AI**.
- Deterministic validation, then human approval of the **exact payload**.
- **Paused-first** execution with read-back verification.
- Monitoring, emergency brake, weekly reviews, A/B experiments and a learning loop.
- A conversational per-client agent workspace.

**Core principles (owner requirements, enforced in code and tests):**
- Nothing changes in Google Ads without approval. Real writes are disabled in V1.
- **Real data only.** There is no mock mode and no demo data at runtime. Test fakes live only in `backend/tests/fakes`.
- **Never invent metrics.** A metric no provider returned is shown as unavailable or "not measured", with the reason.
- Every finding carries its source, retrieval time and verification status. AI suggestions alone never count as verified.

---

## 2. Architecture and technology stack

| Layer | Technology (verified versions where shown) |
|---|---|
| API | FastAPI (Python; local venv 3.13, CI and Docker 3.12), Pydantic v2, pydantic-settings |
| Database | PostgreSQL 16 locally / in Docker; **Supabase Postgres 17** project exists. SQLAlchemy 2, Alembic (migrations `0001`–`0016`). Row-level security is enabled on all tables in Supabase with no policies, intentionally: the backend connects as table owner and the public Data API must not read app data. |
| Jobs | Celery worker and beat, Redis 7 broker |
| Google Ads | `google-ads` Python client (`backend/app/adapters/google_ads/real.py`). MCC hierarchy discovery, per-account login-customer-id, GAQL, `validate_only` mutate. |
| AI | Provider adapters in `backend/app/adapters/ai/providers.py`: Groq, Anthropic (Claude), OpenAI, Gemini, DeepSeek, Mistral, xAI, OpenRouter. Two configurable **roles**, **Main AI** (drafts) and **Challenger AI** (independent review), stored encrypted in the DB with per-role token caps. |
| Research | DataForSEO, SerpApi, Tavily, Brave Search, search suggestions (Google/Bing/DuckDuckGo autocomplete), OpenStreetMap (Nominatim/Overpass), Geoapify, Google Places, PageSpeed, a website crawler (optional Playwright rendering), and web fetch |
| Frontend | React 18.3.1, TypeScript 5.6, Vite 5.4, Tailwind 3.4, TanStack Query 5, three.js 0.169 (live 3D research and drafting scenes), Vitest 2.1 |
| Deployment | `docker-compose.yml` with 6 services (postgres, redis, backend, worker, beat, frontend on nginx). Codespaces dev container (`.devcontainer/`). GitHub Actions `CI` and `Dev container` workflows. |

**Request flow of a campaign build** (`backend/app/services/tasks_service.py`), one step after another:

1. Intake.
2. Audits (website, competitors with verification records, Business Profile/geo).
3. Measurement audit.
4. Intelligence record (ten modules, readiness decision).
5. Keyword research: Keyword Planner → discovery → DataForSEO ideas and metrics → classification → measurement coverage.
6. Feasibility (financial engine).
7. AI draft: Main AI, in staged batches when token limits require.
8. Challenger review.
9. Comparison with the human plan.
10. Payload build (`planner.build_campaign_payload`, including language, ad schedule and location criteria).
11. Deterministic validation (`validation.validate`).
12. Approval of the exact version.
13. Execution: paused-first, `validate_only` and read-back. Real writes are refused while disabled.

Every step emits **research events** (`research_events.emit`), persisted per run. The frontend's live views and each plan version's
evidence are built from them.

**Integration framework** (`backend/app/services/integrations_service.py`):
- A `PROVIDERS` spec per provider.
- Fernet-encrypted secrets in the `integration_configs` table, scoped to a workspace.
- Real **Test Connection** per provider, with per-step diagnostics.
- A dependency engine and honest display states.
- An `_ENV` fallback map from environment variables. **DataForSEO intentionally has no environment fallback**: its credentials live
  only encrypted in the DB.

**Adapter registry:** `backend/app/adapters/registry.py`. `registry.get(name)` raises `IntegrationNotConfigured` when a provider is
missing; there is never a silent mock fallback. Tests use `registry.override(...)`.

---

## 3. Existing features and completed work

63 commits on `main`. Grouped by area (newest areas last):

- **Foundation:**
  - Roles (Super Admin, Admin, Campaign Manager, Analyst); you cannot approve your own request.
  - Dev email sign-in (refused in production) and Google sign-in.
  - Activity log; approval workflow; emergency brake; safety rules.
- **Real-data-only transition:** mock adapters, mock world and demo seed moved to tests only. A `purge-demo-data` CLI removes old demo
  data (it never matches real clients).
- **MCC / accounts (migration 0006):**
  - Workspaces, Google Ads account hierarchy discovery and an account selector.
  - `GOOGLE_ADS_CUSTOMER_ID` retired.
  - Account binding is a single source of truth, and re-linking an account invalidates its approvals.
- **Integrations dashboard (0007):**
  - Per-field provenance and workspace-scoped credentials.
  - SMTP with test email; GA4 / Business Profile / Claude discovery.
  - Honest "Requires approval" states.
- **Intake V2:**
  - Conditional Business Profile URL; optional weekly target and budget.
  - Mandatory competitor name and website.
  - Preparing a campaign no longer needs a linked account; deployment does.
- **Financial engine:** deterministic formulas, three scenarios, budget recommendation (`/finance/analyse`).
- **Intelligence record (0008):** ten versioned, evidence-bearing modules and a readiness decision.
- **Agent workspace (0009):**
  - A conversational per-client thread with rules-first intent routing.
  - Run / reuse / skip planning, confirmations and resumable runs.
- **Monitoring:**
  - Anomalies, pacing, shortfall, search terms and recommendations.
  - A weekly report and a scheduled insights job.
- **Measurement audit (0010):** replaced the old tracking-status gate with findings and evidence.
- **Live research (0011–0012):** research events, provider cache and usage ledger; Tavily; free keyword and geo research; write-readiness model.
- **Frontend V2:** the "Ink & Petrol" design system and a live 3D research workspace (PR #1, merged).
- **AI roles (0013–0015):**
  - Main and Challenger roles across 8 providers.
  - Groq per-minute token fitting, staged drafting and Retry Draft.
  - Enforced manual token caps with an atomic ledger; usage per client.
- **SerpApi:** Google results, Maps, Ads Transparency; Keyword Planner diagnostics fixed.
- **Draft versions:** revise, regenerate, restore and compare; immutable versions; a new approval for each version.
- **Production lifecycle (0016):**
  - Challenger findings can be applied or dismissed.
  - Re-run actions; an approved, unpublished campaign can be expanded with a new ad group as its next version.
  - Token limits shown in the UI; the evidence appendix is captured per plan version; live drafting scene.
- **DataForSEO (`977be6b`):**
  - Seven endpoints, Basic auth, a 12 requests/minute throttle for Google Ads Live endpoints.
  - Per-keyword cache; competitor verification by search overlap.
- **Research and report overhaul (`321d5b8`, the ABC regression):**
  - DataForSEO 40104 handled as "account verification required", and a run stops after a blocking failure.
  - Keyword Planner access level recorded (Explorer, API disabled, project not authorized).
  - Ten-class keyword classification with geo filtering against service areas, selection status, near-duplicates and measurement coverage.
  - Competitor verification records; search engines, directories and platforms rejected.
  - A research-source verification record.
  - Language and ad-schedule criteria; blocking construction validation.
  - The report rebuilt as 10 collapsible sections with searchable data tables.

**Project Claude Code skills** in `.claude/skills/`: `ads-optimization`, `campaign-auditor`, `campaign-strategy`,
`conversion-analysis`, `frontend-design`, `google-ads-api`. They load automatically in a session opened on this repository.

---

## 4. Important technical decisions and why

| Decision | Reason |
|---|---|
| No mock or demo mode at runtime; fakes only in tests | The owner requires real data. Earlier versions mixed mock data into reports. |
| Missing metrics stay missing (null, "not measured"), never 0 or estimated | Fabricated metrics mislead clients. The AI receives `[keyword, volume, CPC]` triples where null means unknown, and the prompts say "Never invent or change these numbers". |
| Main AI and a separate Challenger AI; the final plan is assembled by rules, not by either AI | Independent review; agreement between two models is a signal, not proof. |
| Approval is bound to the exact payload version; any edit makes a new version that needs a new approval | Audit trail; no silent changes after approval. |
| Campaigns are created PAUSED; enabling needs its own approval; writes are disabled by flags plus a Super Admin authorisation | Safety-first V1. |
| Preparing a draft does not need a Google Ads account; deployment does | Agencies plan before account access exists. |
| Keyword Planner runs under a separately configured research customer, never a client account | Avoids borrowing client accounts for research. |
| DataForSEO credentials live only encrypted in the DB, never in `.env` or the frontend, and are never logged; the login is never returned by the API | The owner's security requirement. |
| DataForSEO connection test = free User Data API **plus** a free Keywords Data `locations` probe | User Data succeeds even for an unverified account, which made it show "Connected" wrongly (ABC case). |
| A blocking provider failure (verification, funds, auth) stops further calls of that provider for the rest of the run | The same refusal would repeat and waste paid or limited requests. |
| Out-of-area, job-seeking, excluded-service and competitor-name keywords are not sent for paid measurement | Measurement budget goes to campaign-eligible keywords. |
| Competitor status ladder (Discovered → SERP / Website / Market Verified, or Rejected) | "23 verified" when they were only discovered was misleading. |
| A missing ad schedule or location is a **blocking** validation error | It used to show as a dash in the report and was silently omitted from the payload. |
| Evidence is persisted per plan version (`CampaignPlan.evidence`, format version 2) | A version always shows what it was built from. |
| Direct-to-`main` workflow, no PRs | The owner's explicit, permanent instruction. |

More decisions: `docs/DECISIONS_REQUIRED.md`, `docs/GOOGLE_ADS_SAFETY_RULES.md`, `docs/REAL_DATA_ONLY.md`.

---

## 5. Repository structure and key files

```
CLAUDE.md                      owner's workflow rules (permanent)
README.md                      run instructions (some parts stale, see §8)
Makefile                       common commands
docker-compose.yml             6-service stack
.env.example                   every environment variable name (values are placeholders)
.devcontainer/                 Codespaces config
.github/workflows/ci.yml       backend (ruff + pytest), frontend (tsc, vitest, build), docker build
.github/workflows/devcontainer.yml
.claude/skills/                6 project skills
scripts/start.sh               creates .env, builds, starts, waits, bootstraps admin
scripts/live-tests.sh          read-only real-API tests (PASSED / NOT CONFIGURED / BLOCKED)
scripts/check-env.sh, create-env.sh
e2e/ui-smoke.mjs               browser smoke test against a running stack
docs/                          ~27 design and ops documents (start with SYSTEM_ARCHITECTURE, KNOWN_ISSUES, GOOGLE_ADS_SAFETY_RULES)
backend/
  app/main.py                  FastAPI app; API under /api/v1, docs at /api/docs
  app/cli.py                   create-super-admin, purge-demo-data, check-env
  app/core/                    config.py (settings), clock.py (time; tests shift it), env_audit.py, errors.py
  app/api/routers/             agent, ai_config, auth_users, core, integrations, mcc, ops, profiles, workflow
  app/adapters/                base.py (error classes), registry.py, google_ads/real.py, ai/{providers,claude,prompts,schemas}.py,
                               research/{dataforseo,serpapi,tavily,brave,suggest,geo,http}.py, crawler, places, pagespeed, notifications
  app/services/                business logic; key ones:
    tasks_service.py           campaign/task pipeline orchestration
    audits.py                  competitor audit, keyword research, build_context
    keyword_classify.py        10 keyword classes, geo filtering, near-duplicates
    competitor_verify.py       competitor verification records and exclusion list
    campaign_settings.py       location / language / ad schedule resolution and criteria
    dataforseo_research.py     DataForSEO research service (cache, blocking stop)
    planner.py                 draft + build_campaign_payload
    validation.py              deterministic validation, including construction blockers
    research_appendix.py       per-version evidence (source ledger, classification, settings)
    integrations_service.py    provider specs, connection tests, display states
    ai_service.py, ai_roles.py, competition.py, challenger*.py, drafting.py, token_caps.py
    safety.py, write_readiness.py, execution.py, approvals.py, emergency.py
  app/workers/                 celery_app.py, jobs.py
  migrations/versions/         0001 … 0016
  tests/                       unit, integration, api, contract, security, e2e, fakes; conftest.py
  live_tests/                  real-API tests (skipped unless configured)
  requirements.lock            hash-locked deps for the Docker image (regenerate with `make lock`)
frontend/
  src/pages/                   Agent, Approvals, Audits, Clients, Integrations, Planner, Tasks, Monitor, Weekly, …
  src/components/ResearchAppendix.tsx   the 10-section Campaign report
  src/components/report/DataTable.tsx   search / sort / filter / paginate table
  src/components/research/, campaignBuild/   live explorers and 3D scenes
  src/test/                    vitest suites
```

**Branches (verified):**
- `main` is the only active branch.
- Remote also has `fix/research-integrations`, `fix/research-integrations-v2` and `redesign/frontend-v2` (the last one merged via PR #1).
- PR #2 was **closed without merging** at the owner's request.
- The owner said to leave the leftover branches untouched; do not delete them without asking.

---

## 6. API integrations and environment variables (names only)

**From `.env.example` (verified list):**
- **App and safety:** `APP_ENV`, `LOG_LEVEL`, `GOOGLE_ADS_WRITES_ENABLED`, `AUTO_NEGATIVES_MODE`, `AUTO_NEGATIVES_DAILY_LIMIT`,
  `EMERGENCY_BRAKE_WRITE_MODE`, `AUTH_PROVIDER`.
- **Infrastructure:** `DATABASE_URL`, `COMPOSE_DATABASE_URL` (the database used inside Docker, e.g. the Supabase session pooler),
  `REDIS_URL`, `CELERY_TASK_ALWAYS_EAGER`.
- **Security:** `APP_SECRET_KEY`, `TOKEN_ENCRYPTION_KEY`, `CORS_ORIGINS`, `SESSION_COOKIE_SECURE`, `SESSION_TTL_HOURS`,
  `RATE_LIMIT_PER_MINUTE`, `AUTH_RATE_LIMIT_PER_MINUTE`, `BOOTSTRAP_ADMIN_EMAIL`, `ALLOWED_LOGIN_DOMAINS`, `GOOGLE_CLIENT_ID`.
- **Google Ads:** `GOOGLE_ADS_CLIENT_ID`, `GOOGLE_ADS_CLIENT_SECRET`, `GOOGLE_ADS_REDIRECT_URI`, `GOOGLE_ADS_REFRESH_TOKEN`,
  `GOOGLE_ADS_LOGIN_CUSTOMER_ID`. The code also reads `GOOGLE_ADS_RESEARCH_CUSTOMER_ID` for the Keyword Planner research customer; it
  is not in `.env.example`.
- **Research:** `GOOGLE_PLACES_API_KEY`, `PAGESPEED_API_KEY`, `TAVILY_API_KEY`, `SERPAPI_API_KEY`, `BRAVE_SEARCH_API_KEY`, `GEOAPIFY_API_KEY`.
- **AI:** `GROQ_API_KEY`, `GROQ_MODEL`, `GROQ_CHALLENGER_MODEL`, `GROQ_TPM_LIMIT`, `AI_DRAFT_MAX_OUTPUT_TOKENS`, `AI_STAGE_MAX_OUTPUT_TOKENS`,
  `AI_OUTPUT_TOKENS_BASE`, `AI_OUTPUT_TOKENS_PER_AD_GROUP`, `AI_CHARS_PER_TOKEN`, `AI_TOKEN_SAFETY_MARGIN`, `ANTHROPIC_API_KEY`.
- **Email:** `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`, `SMTP_FROM`.
- **Crawler:** `CRAWLER_MAX_PAGES`, `CRAWLER_MAX_DEPTH`, `CRAWLER_TIMEOUT_SECONDS`.
- **Tests only:** `TEST_DATABASE_URL`, a **disposable** database that the test suite wipes.
- **Supabase helper:** `backend/.env.supabase` holds `SUPABASE_DATABASE_URL`. The file is git-ignored.

**Configured in the app, not in env:**
- **DataForSEO** (API login and password): Integrations page only, stored encrypted.
- **AI role keys and models** (Main AI, Challenger AI, any of the 8 providers) and token caps: AI configuration page.
- Most providers can be configured on the Integrations page; that overrides the env fallback, and provenance is shown per field.

**Local secret files:** `.env`, `.env.bak` and `backend/.env.supabase` exist in the old environment and are git-ignored. **They
are not in the repository and will not transfer.** Recreate them from `.env.example` in the new environment.

The old local `.env` also has stale legacy names that the app no longer uses: `GOOGLE_ADS_DEVELOPER_TOKEN`, `GOOGLE_ADS_CUSTOMER_ID`,
`*_MODE` variables and `MOCK_SEED`. Do not copy them.

**Status of each external integration:**

| Integration | Status |
|---|---|
| Google Ads API | Code complete. The Cloud project has **Explorer** access, so Keyword Planner and some methods are refused. *Not verified in this session against the live account.* |
| DataForSEO | Code complete and tested with documented-shape fakes. Real API seen only with invalid credentials (HTTP 401). The **account needs verification (40104)**. *Metrics are unverified.* |
| Groq / Claude / other AI | Code complete. Live status depends on keys in the DB. *Not verified in this session.* Claude is broken in Docker and CI because `anthropic` is undeclared (§0.2). |
| SerpApi, Tavily, Brave, Geoapify, Places, PageSpeed, SMTP | Code complete with connection tests. *Live status not verified in this session.* |
| Supabase | Project `google-ads-ai-agent-v1` (ref `extfrctastbodivhiiei`, us-east-1) is ACTIVE_HEALTHY, with migrations through 0012. KNOWN_ISSUES says the runtime connection from the build sandbox could not be tested, and that a database password was once shared in chat and **should be reset**. *Whether that reset happened is unknown.* |
| Cloudflare | The owner asked about migrating to Cloudflare. A plan was given, but the Cloudflare connector was not installed and **no migration work was done**. |

---

## 7. Current development status

- `main` = `321d5b8`, pushed; local and remote match; working tree clean (before this file was added).
- **Local verification on 2026-11-17 (old environment):**
  - Backend: `ruff` clean, full `pytest` passed.
  - Frontend: `tsc`, `vitest` (141 tests) and `vite build` passed.
- **GitHub CI:**
  - Backend job fails (2 tests, cause in §0.2).
  - Frontend and Docker build jobs pass.
  - The Dev container workflow passes.
- Write safety unchanged: writes disabled, auto-negatives in shadow mode, emergency brake simulated.
- Last completed work: the research pipeline, campaign quality and report overhaul driven by the **ABC Home & Commercial Services**
  campaign failure (details in §11).

---

## 8. Known bugs, issues and limitations

**Bugs and gaps:**
1. **`anthropic` and `httpx2` are not declared as dependencies.** CI is red, and Claude fails in Docker.
   `tests/integration/test_ai_providers.py` imports `httpx2` directly.
2. **Supabase migrations 0013–0016 are not applied.**
3. **Stale documentation:**
   - README says `alembic current` shows `0003 (head)`; head is `0016`.
   - The README lists Groq and Claude as separate integrations; they are now AI roles.
   - Parts of `docs/TEST_RESULTS.md` and `docs/IMPLEMENTATION_PROGRESS.md` are probably out of date (*not reviewed line by line*).
4. **Test order sensitivity:** the ledger uses `clock.now()`, which some fixtures time-shift, so "newest row" assertions depend on
   test order. Two DataForSEO tests were fixed for this; similar latent cases may exist.
5. **Plans created before `321d5b8`** have evidence format v1. Their report lacks classification, competitor records and settings,
   and says so. Research must be re-run, for example the real ABC task.
6. **New blocking rule:** a client with no business hours cannot get a campaign approved until hours are added, or the task sets
   `ad_schedule: "all_hours"`. Intended, but it affects existing clients. *There is no UI control yet for choosing "all hours"
   explicitly; it can only be set as a task field.*

**Limitations:**
- The keyword geo filter uses a built-in gazetteer (US states, ~150 major US cities, ~45 foreign places) plus the client's service
  areas. Small towns outside the area are not caught unless they are in the list. Ambiguous names (Reading, Mobile, Lincoln) were
  removed on purpose.
- Competitor "Website Verified" needs a crawl or page open. Discovered candidates are only opened for the first three web results, so
  many stay at SERP Verified or Discovered.
- The language criterion defaults to English when no language is set for the task or business.
- Real Google Ads writes have never been exercised. Execution has been tested only against the fake Google Ads world and `validate_only`.
- The ABC campaign's original data was never uploaded to the session. The regression test reconstructs it from the owner's description
  (0/662 measured, 40104, Explorer access, google.com as a competitor, missing location and schedule).
- More items in `docs/KNOWN_ISSUES.md`.

---

## 9. Pending tasks and priorities

| Priority | Task |
|---|---|
| P0 | Declare `anthropic` (and fix or remove the `httpx2` test dependency) in `pyproject.toml`, run `make lock`, make CI green |
| P0 | Apply migrations 0013–0016 to Supabase (if Supabase is the deployment database). Confirm with the owner first. |
| P1 | Owner actions: verify the DataForSEO account; apply for Basic/Standard Google Ads API access; confirm the Supabase password was reset |
| P1 | After DataForSEO verification: Test Connection, then one real keyword run, and confirm "Research access: Ready" and measured rows |
| P1 | Re-run research on the real ABC task and review the new report with the owner |
| P2 | UI control for the explicit "run all days and hours" schedule choice, and a language selector, in the task or client form |
| P2 | Update README, TEST_RESULTS and KNOWN_ISSUES for 0016, AI roles, DataForSEO and the new report |
| P2 | Old task-list items never closed: provenance in agents and reports for Lead Generation / Outreach; live integration tests reporting NOT CONFIGURED / BLOCKED; a final audit for mock dependencies and hard-coded metrics. *Their current state is not verified; parts were likely covered by later work.* |
| P3 | Cloudflare migration, only if the owner confirms; needs the Cloudflare connector |
| P3 | Extend the geo gazetteer or use a geocoder to detect out-of-area towns |

---

## 10. Commands to run and test

**Full stack (Docker):**
```bash
bash scripts/start.sh
```
```bash
docker compose exec backend python -m app.cli create-super-admin you@example.com
```
Then open http://localhost:5173. API docs are at http://localhost:8000/api/docs.

**Without Docker** (the old cloud container ran Postgres and Redis locally):
```bash
pg_ctlcluster 16 main start
```
```bash
redis-server --daemonize yes
```
```bash
cd backend && .venv/bin/alembic upgrade head
```
```bash
make dev-backend
```
```bash
make dev-worker
```
```bash
make dev-frontend
```

**Required checks before every commit (from CLAUDE.md):**
```bash
cd backend && .venv/bin/ruff check app tests --select F,E9 && .venv/bin/pytest
```
```bash
cd frontend && npx tsc --noEmit && npx vitest run && npx vite build
```
- The backend tests need `TEST_DATABASE_URL`, a disposable database (default `postgresql+psycopg://app:app@localhost:5432/google_ads_agent_test`).
  The suite wipes it. Never point it at a real database.
- The full backend suite takes about 25–40 minutes locally (CI: ~3.5 minutes). Run it in the background.

**Other commands:**
```bash
make live-tests
```
```bash
make e2e
```
```bash
make lock
```
- `make live-tests` runs read-only real-API tests and needs credentials.
- `make e2e` runs the browser smoke test against a running stack.
- Run `make lock` after changing `backend/pyproject.toml`. It needs `uv`; the Docker build fails if the lock does not match.

**Setting up a fresh environment:**
```bash
cd backend && python3.12 -m venv .venv && .venv/bin/pip install -e ".[dev]"
```
```bash
cd frontend && npm ci
```

---

## 11. Context from previous conversations that is not in the code

**Owner rules (standing instructions):**
- **Workflow (permanent):** Request → Inspect → Implement → Test → Commit → Push to `main`.
  - No feature branches, no PRs, no merge workflow, and never ask the owner to merge.
  - Pull with `--ff-only` first; if there are conflicts or uncommitted changes you did not make, stop and report.
  - Never discard, stash, reset or overwrite the owner's work.
  - Never force-push or `git reset --hard`.
  - Do not deploy unless asked.
  - If branch protection rejects a push, stop and report the exact restriction.
- **Security:**
  - Never print secrets; never ask the owner to paste API keys into chat; never commit `.env` or keys.
  - Keep the owner's email address private.
  - DataForSEO credentials stay encrypted in the DB: never in `.env` or the frontend, never logged.
- **Safety:**
  - Keep `GOOGLE_ADS_WRITES_ENABLED=false`, `AUTO_NEGATIVES_MODE=shadow` and `EMERGENCY_BRAKE_WRITE_MODE=simulate`.
  - Do not enable Google Ads writes, publish campaigns or change billing.
  - Never run `docker compose down -v` against data you want to keep.
- **Data:**
  - Preserve existing Supabase data. Do not reset or seed Supabase.
  - Add no demo data, no mock fallbacks and no fabricated research data.
  - Do not remove existing integrations or change unrelated architecture.
  - Do not claim DataForSEO metrics work until tested with a verified real account.
- **Commit trailer used so far:** `Co-Authored-By: Claude …` plus a `Claude-Session:` link. A new account's session will give its own
  attribution lines; follow those.

**The ABC Home & Commercial Services case** (the latest failure the owner reported):
- 0 of 662 keywords were measured.
- DataForSEO returned 40104 on every request.
- Keyword Planner was refused because of Explorer access.
- google.com was listed as a competitor, and the client's own competitors were not reviewed.
- Keywords for other cities were kept.
- The report showed dashes for location and schedule.
- All causes were fixed in `321d5b8`. **The real ABC task has not been re-run since.**

**Owner preferences:**
- Senior marketing-director level analysis; professional, concise, human tone; challenge weak assumptions.
- Presentation-ready deliverables for strategies and reports.
- The owner works in SEO, PPC and social for international clients (mainly USA).

**Beta database:** browser verification used a separate local database, `google_ads_agent_beta` (old environment only). It does not
transfer.

**Cloudflare:** the owner asked to connect the repository to Cloudflare and migrate. Only a plan was discussed; it is waiting on the
owner and needs the Cloudflare connector.

---

## 12. Exact recommended next steps

1. **Set up the new environment and confirm the baseline:**
   - Clone, create the venv and `npm ci`.
   - Start Postgres and Redis, create the disposable test database, then run both check commands from §10.
   - Expected: the frontend passes. The backend passes only if `anthropic` and `httpx2` are installed; otherwise the 2 tests from §0.2 fail.
2. **Fix CI (P0):**
   - Add `anthropic` to `[project].dependencies` in `backend/pyproject.toml`. Pin a version compatible with the code in
     `app/adapters/ai/claude.py`, which uses `client.beta.messages.create(... fallbacks=...)` for `claude-opus-5*` models.
   - Make `tests/integration/test_ai_providers.py` build the mock transport without importing `httpx2`, or declare `httpx2` as a dev
     dependency only if the chosen `anthropic` version really needs it. *I could not verify which `anthropic` versions are built on
     `httpx2`.*
   - Run `make lock` and rebuild the Docker image. Push, and confirm the GitHub `CI` run is green.
3. **Supabase (P0, ask the owner first):**
   - Confirm which database production uses.
   - If it is Supabase, apply 0013–0016 with Alembic against the Supabase URL, or through the Supabase connector as done before.
     Name them in the same `gaa_v1_00NN_…` style.
   - Verify with `list_migrations`. Do not reset or seed anything.
4. **Ask the owner for status** on the DataForSEO verification, the Google Ads API access application and the Supabase password reset.
   Once DataForSEO is verified:
   - Run Test Connection; "Research access: Ready" means it works.
   - Run one BUILD_CAMPAIGN on a real client and check that keyword rows show `metric_source` `dataforseo:*` with timestamps.
   - Only then report DataForSEO as working.
5. **Re-run research on the real ABC task** and go through the new 10-section report with the owner:
   - Overview blockers; Settings (add business hours); keyword coverage; competitor verification.
6. **Add UI for the schedule and language choices** (P2) so a missing schedule can be fixed without editing task fields.
7. **Refresh the docs** (README, TEST_RESULTS, KNOWN_ISSUES, DATABASE_SCHEMA for 0013–0016) in the same commit style.

---

## 13. What I could not verify

- Live behaviour of any real external API in this session (Google Ads, Groq, Claude, SerpApi, Tavily, Brave, Places, PageSpeed,
  Geoapify, SMTP). DataForSEO was seen only returning 401 for invalid credentials.
- Whether the deployed or production app (if any) uses Supabase or the bundled Postgres, and whether it is running at all.
- Whether the Supabase database password was reset after it was exposed in an earlier chat.
- Which AI provider keys and models are currently configured in the DB for the Main and Challenger roles.
- The real ABC campaign data (never uploaded) and the current state of that task in the owner's database.
- Whether the three old open task-list items (§9, P2) are already covered by later commits.
- The `anthropic` / `httpx2` version relationship needed for the CI fix.
- The contents of git-ignored local files (`.env`, `.env.bak`, `backend/.env.supabase`). Only variable names were read, never values.
