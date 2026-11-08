# Environment variables

Generated from `backend/app/core/env_audit.py` (the single registry; a unit test keeps it in sync with `Settings`, `.env.example` and every `os.environ` read).

**Check your own file at any time (prints no values):** `bash scripts/check-env.sh` (or `cd backend && python -m app.cli check-env`). Exit code 1 means an error. `--file-only` audits just the file, `--json` is machine-readable.

## How configuration is loaded
1. `<repo root>/.env`, then `./.env` (e.g. `backend/.env`); later files win. This works wherever the backend is started from.
2. Real environment variables (Docker Compose `env_file`, Codespaces/cloud secrets, your shell) override the files.
3. Blank values mean “use the default”. Quotes and surrounding spaces are trimmed. `GAA_ENV_FILE=<path>` loads only that file; `GAA_ENV_FILE=none` loads none (tests).
4. Credentials entered on the **Integrations** page are stored encrypted in the database and win over these variables. The page refuses to save credentials until `TOKEN_ENCRYPTION_KEY` (or a random 32+ character `APP_SECRET_KEY`) is set.

## Google sign-in is not Google Ads OAuth
| | App login (Google sign-in) | Google Ads / GA4 / Business Profile |
|---|---|---|
| Variables | `AUTH_PROVIDER=google`, `GOOGLE_CLIENT_ID`, `BOOTSTRAP_ADMIN_EMAIL`, `ALLOWED_LOGIN_DOMAINS` | `GOOGLE_ADS_DEVELOPER_TOKEN`, `GOOGLE_ADS_CLIENT_ID`, `GOOGLE_ADS_CLIENT_SECRET`, `GOOGLE_ADS_REFRESH_TOKEN`, `GOOGLE_ADS_REDIRECT_URI`, `GOOGLE_ADS_LOGIN_CUSTOMER_ID`, `GOOGLE_ADS_CUSTOMER_ID` |
| Flow | Google Identity Services returns an ID token; the backend verifies signature, issuer, audience, expiry, verified email and domain. **Only the client ID is needed** — no secret, no redirect URI. | Authorization-code flow with PKCE; needs client ID + secret + redirect URI and yields a refresh token. |
| Grants | Access to this app only. Never grants Google Ads access. | Access to Google Ads data (read-only unless writes are separately enabled). |
| Google Cloud setup | OAuth *Web* client, **Authorised JavaScript origins** = your app origin | OAuth client, **Authorised redirect URI** = `<app origin>/api/v1/integrations/google/callback`; Google Ads API enabled; developer token from the manager account |
Google sign-in is optional for V1: with `AUTH_PROVIDER=mock` (development only) a demo-user picker is shown. `AUTH_PROVIDER=mock` is refused when `APP_ENV=production`.

## Google Ads IDs
`GOOGLE_ADS_LOGIN_CUSTOMER_ID` is the **manager (MCC)** account, sent as the `login-customer-id` header. `GOOGLE_ADS_CUSTOMER_ID` is a **client** account under it, used by the connection test. Dashes are accepted and removed. Putting the manager ID in both makes the app query the manager itself, which has no campaigns (the audit warns).

## Groq
Groq is used only if **all** of these hold: `GROQ_ENABLED=true`, `AI_PROVIDER=groq`, `GROQ_API_KEY`, `GROQ_MODEL` (or it is enabled on the Integrations page). `GROQ_MODEL` drafts; `GROQ_CHALLENGER_MODEL` critiques (blank = same model). The code uses Groq JSON-object mode, which Groq supports on its chat models. Model names are not hard-coded; production chat models listed at https://console.groq.com/docs/models on 2026-11-08: `llama-3.3-70b-versatile`, `llama-3.1-8b-instant`, `openai/gpt-oss-120b`, `openai/gpt-oss-20b`. A different model family for the challenger (e.g. `GROQ_MODEL=llama-3.3-70b-versatile`, `GROQ_CHALLENGER_MODEL=openai/gpt-oss-120b`) gives a more independent critique. Test Connection on the Integrations page checks the exact name against your key.

## Checklist

### Core

| Variable | Needed | Default | Purpose | Read by |
|---|---|---|---|---|
| `APP_ENV` | optional | `development` | development | test | staging | production. production switches on the safety guards. | core/config.py, main.py |
| `INTEGRATION_MODE` | optional | `mock` | Default mode for every integration: mock (simulated) or real. Real reads still need the Integrations page. | core/config.py (mode_for) |
| `TEST_MODE` | optional | `true` | Enables the simulated clock and mock controls. Must be false in production. | core/clock.py, api/routers/ops.py |
| `LOG_LEVEL` | optional | `INFO` | Python log level. | main.py |

### Safety

| Variable | Needed | Default | Purpose | Read by |
|---|---|---|---|---|
| `GOOGLE_ADS_WRITES_ENABLED` | optional | `false` | Key 1 of 2 for real Google Ads writes. Keep false until you are ready; a Super Admin must also authorise. | services/safety.py, integrations_service.py |
| `AUTO_NEGATIVES_MODE` | optional | `shadow` | shadow = negatives are only proposed; active = safe negatives may be applied (also needs a system setting). | services/safety.py, optimization.py |
| `AUTO_NEGATIVES_DAILY_LIMIT` | optional | `25` | Maximum automatic negatives per day. | services/optimization.py |
| `EMERGENCY_BRAKE_WRITE_MODE` | optional | `simulate` | simulate | disabled | real — what the emergency brake does to Google Ads. | services/emergency.py, safety.py |

### Modes

| Variable | Needed | Default | Purpose | Read by |
|---|---|---|---|---|
| `GOOGLE_ADS_MODE` | optional | — | Legacy switch: real = real READ-ONLY Google Ads (never writes). The Integrations page operating mode is preferred. | integrations_service.py (operating_mode) |
| `PLACES_MODE` | optional | — | real = use the Places API even if not enabled on the Integrations page. | adapters/registry.py |
| `PAGESPEED_MODE` | optional | — | real = use the PageSpeed API even if not enabled on the Integrations page. | adapters/registry.py |
| `NOTIFIER_MODE` | optional | — | real = send alert emails through SMTP. | adapters/registry.py |
| `CRAWLER_MODE` | optional | `fixture` | fixture (bundled demo pages) or live (fetch real websites, SSRF-guarded). | adapters/registry.py |

### Login

| Variable | Needed | Default | Purpose | Read by |
|---|---|---|---|---|
| `AUTH_PROVIDER` | optional | `mock` | mock = pick a demo user (never allowed in production); google = Google sign-in. | adapters/registry.py, auth_users.py |

### AI

| Variable | Needed | Default | Purpose | Read by |
|---|---|---|---|---|
| `AI_PROVIDER` | optional | `mock` | mock or groq. groq also needs GROQ_ENABLED=true. (Claude and Groq can also be enabled on the Integrations page.) | adapters/registry.py |

### Infrastructure

| Variable | Needed | Default | Purpose | Read by |
|---|---|---|---|---|
| `DATABASE_URL` 🔒 | **required** | — | PostgreSQL connection string (local Postgres or the Supabase Session Pooler). | db/session.py, migrations |
| `COMPOSE_DATABASE_URL` 🔒 | optional | — | Docker Compose / Codespaces only: DATABASE_URL for the containers (blank = the bundled postgres). | docker-compose.yml |
| `REDIS_URL` | **required** | `redis://localhost:6379/0` | Redis for Celery and rate limits. | main.py, workers/celery_app.py |
| `CELERY_TASK_ALWAYS_EAGER` | optional | `false` | true runs background tasks inline (tests / no worker). | workers/celery_app.py, workflow.py |

### Security

| Variable | Needed | Default | Purpose | Read by |
|---|---|---|---|---|
| `APP_SECRET_KEY` 🔒 | when: APP_ENV is not development/test, or you save credentials on the Integrations page | — | Signs sessions and, if TOKEN_ENCRYPTION_KEY is blank, derives the credential-encryption key. Required (32+ random chars) outside development. | adapters/ads_oauth, config.py |
| `TOKEN_ENCRYPTION_KEY` 🔒 | when: production, or saving credentials on the Integrations page | — | Fernet key that encrypts credentials saved on the Integrations page. Required in production. | adapters/ads_oauth/__init__.py |
| `CORS_ORIGINS` | optional | `http://localhost:5173` | Comma-separated browser origins allowed to call the API. | main.py (cors_origins) |
| `SESSION_COOKIE_SECURE` | optional | `false` | true sends the session cookie over HTTPS only. Must be true in production. | main.py, auth_users.py |
| `SESSION_TTL_HOURS` | optional | `12` | Login session lifetime. | services/auth_service.py |
| `RATE_LIMIT_PER_MINUTE` | optional | `300` | API requests per minute per client. | main.py |
| `AUTH_RATE_LIMIT_PER_MINUTE` | optional | `30` | Login attempts per minute per client. | main.py |

### App login (Google)

| Variable | Needed | Default | Purpose | Read by |
|---|---|---|---|---|
| `BOOTSTRAP_ADMIN_EMAIL` | when: AUTH_PROVIDER=google and no Super Admin exists yet | — | The first Google account allowed to sign in; it becomes Super Admin. Everyone else must be invited. | services/auth_service.py |
| `ALLOWED_LOGIN_DOMAINS` | optional | — | Comma-separated email domains allowed to sign in (blank = any invited account). | adapters/auth (allowed_login_domains) |
| `GOOGLE_CLIENT_ID` | when: AUTH_PROVIDER=google | — | OAuth Web client ID for Google SIGN-IN (ID-token flow). The only Google sign-in credential the app uses. | adapters/auth/__init__.py, auth_users.py |

### Google Ads

| Variable | Needed | Default | Purpose | Read by |
|---|---|---|---|---|
| `GOOGLE_ADS_DEVELOPER_TOKEN` 🔒 | when: real Google Ads (or enter it on the Integrations page) | — | Google Ads API developer token (from the manager account's API Center). | integrations_service.py |
| `GOOGLE_ADS_CLIENT_ID` | when: real Google Ads | — | OAuth client ID used to authorise Google Ads (and GA4 / Business Profile). NOT the sign-in client. | integrations_service.py |
| `GOOGLE_ADS_CLIENT_SECRET` 🔒 | when: real Google Ads | — | OAuth client secret for the Google Ads client above. | integrations_service.py |
| `GOOGLE_ADS_REDIRECT_URI` | optional | — | OAuth redirect URI registered for the Ads client. Blank = <app origin>/api/v1/integrations/google/callback. | integrations_service.py (oauth_redirect_uri) |
| `GOOGLE_ADS_REFRESH_TOKEN` 🔒 | when: real Google Ads | — | Refresh token for the Google user that can access the accounts (or use Connect with Google on the Integrations page). | integrations_service.py |
| `GOOGLE_ADS_LOGIN_CUSTOMER_ID` | optional | — | Manager (MCC) account ID, 10 digits. Sent as the login-customer-id header when the accounts sit under a manager. | integrations_service.py, adapters/google_ads/real.py |
| `GOOGLE_ADS_CUSTOMER_ID` | optional | — | Default CLIENT account ID, 10 digits, used for the connection test. Not the manager ID. | integrations_service.py |

### Website research

| Variable | Needed | Default | Purpose | Read by |
|---|---|---|---|---|
| `GOOGLE_PLACES_API_KEY` 🔒 | optional | — | Places API (New) key for competitor and Business Profile research. | integrations_service.py |
| `PAGESPEED_API_KEY` 🔒 | optional | — | PageSpeed Insights key (works without one at a low quota). | integrations_service.py |

### AI (Groq)

| Variable | Needed | Default | Purpose | Read by |
|---|---|---|---|---|
| `GROQ_ENABLED` | optional | `false` | true lets the env path use Groq. Needs AI_PROVIDER=groq, GROQ_API_KEY and GROQ_MODEL. | adapters/registry.py |
| `GROQ_API_KEY` 🔒 | when: Groq is used | — | Groq API key (starts with gsk_). | integrations_service.py |
| `GROQ_MODEL` | when: Groq is used | — | Groq model that writes drafts and explanations. Exact Groq model ID. | adapters/ai/__init__.py |
| `GROQ_CHALLENGER_MODEL` | optional | — | Groq model that critiques plans. Blank = GROQ_MODEL (no independent second opinion). | adapters/ai/__init__.py |

### AI (Claude)

| Variable | Needed | Default | Purpose | Read by |
|---|---|---|---|---|
| `ANTHROPIC_API_KEY` 🔒 | optional | — | Fallback key for the Claude integration (model is chosen on the Integrations page; default claude-opus-5-5). | integrations_service.py |

### Email

| Variable | Needed | Default | Purpose | Read by |
|---|---|---|---|---|
| `SMTP_HOST` | when: NOTIFIER_MODE=real | — | SMTP server for alert emails. | adapters/notifications |
| `SMTP_PORT` | optional | `587` | SMTP port (STARTTLS). | adapters/notifications |
| `SMTP_USER` | optional | — | SMTP username (blank = no login). | adapters/notifications |
| `SMTP_PASSWORD` 🔒 | optional | — | SMTP password. | adapters/notifications |
| `SMTP_FROM` | optional | `alerts@localhost` | From address for alert emails. | adapters/notifications |

### Mock & crawler

| Variable | Needed | Default | Purpose | Read by |
|---|---|---|---|---|
| `MOCK_SEED` | optional | `42` | Seed for the deterministic simulated world. | mock_world/simulator.py |
| `CRAWLER_MAX_PAGES` | optional | `40` | Pages crawled per website. | services/crawler_service.py |
| `CRAWLER_MAX_DEPTH` | optional | `2` | Link depth crawled. | services/crawler_service.py |
| `CRAWLER_TIMEOUT_SECONDS` | optional | `10` | Per-request crawler timeout. | adapters/crawler/__init__.py |

🔒 = secret: never commit, never printed by the audit.

### Retired — do nothing, remove from `.env`

| Variable | Why |
|---|---|
| `GOOGLE_CLIENT_SECRET` | Not used: sign-in verifies Google ID tokens with the client ID only (no authorization-code exchange). Remove it from .env. Do not confuse it with GOOGLE_ADS_CLIENT_SECRET. |
| `GOOGLE_REDIRECT_URI` | Not used: Google sign-in has no redirect (the Google button returns a token to the page). The Ads redirect is GOOGLE_ADS_REDIRECT_URI. |
| `KEYWORD_RESEARCH_MODE` | Not used: Keyword Planner follows the Google Ads operating mode on the Integrations page. |

### Read outside the backend

| Variable | Purpose |
|---|---|
| `VITE_API_TARGET` | Vite dev server: where /api is proxied (default http://localhost:8000). |
| `CODESPACES` | Set by GitHub Codespaces; read by scripts/start-demo.sh. |
| `GAA_ENV_FILE` | Overrides which .env file the backend loads (none = no file; used by tests). |
