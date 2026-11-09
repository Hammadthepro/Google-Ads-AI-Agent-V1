# Environment variables

Generated from `backend/app/core/env_audit.py` by `python -m app.cli docs-env` (a unit test fails if this file is out of date).

**Check your own file at any time (prints no values):** `bash scripts/check-env.sh` (or `cd backend && python -m app.cli check-env`). Exit code 1 means an error. `--file-only` audits just the file, `--json` is machine-readable.
**Fill blank values from your environment variables / Codespaces secrets:** `cd backend && python -m app.cli populate-env` (names only are printed; existing values are kept; safety switches are never loosened).

## How configuration is loaded
1. `<repo root>/.env`, then `./.env` (e.g. `backend/.env`); later files win. This works wherever the backend is started from.
2. Real environment variables (Docker Compose `env_file`, Codespaces/cloud secrets, your shell) override the files. If a variable is set in both, the environment wins — change it where it is set.
3. Blank values mean “use the default”. Quotes and surrounding spaces are trimmed. `GAA_ENV_FILE=<path>` loads only that file; `GAA_ENV_FILE=none` loads none (tests).
4. Credentials entered on the **Integrations** page are stored encrypted in the database and win over these variables. The page refuses to save credentials until `TOKEN_ENCRYPTION_KEY` (or a random 32+ character `APP_SECRET_KEY`) is set.

## Google sign-in is not Google Ads OAuth
| | App login (Google sign-in) | Google Ads / GA4 / Business Profile |
|---|---|---|
| Variables | `AUTH_PROVIDER=google`, `GOOGLE_CLIENT_ID`, `BOOTSTRAP_ADMIN_EMAIL`, `ALLOWED_LOGIN_DOMAINS` | `GOOGLE_ADS_CLIENT_ID`, `GOOGLE_ADS_CLIENT_SECRET`, `GOOGLE_ADS_REFRESH_TOKEN`, `GOOGLE_ADS_REDIRECT_URI`, `GOOGLE_ADS_LOGIN_CUSTOMER_ID`, `GOOGLE_ADS_CUSTOMER_ID` |
| Flow | Google Identity Services returns an ID token; the backend verifies signature, issuer, audience, expiry, verified email and domain. **Only the client ID is needed** — no secret, no redirect URI. | Authorization-code flow with PKCE; needs client ID + secret + redirect URI and yields a refresh token. |
| Grants | Access to this app only. Never grants Google Ads access. | Access to Google Ads data (read-only unless writes are separately enabled). |
| Google Cloud setup | OAuth *Web* client, **Authorised JavaScript origins** = your app origin | OAuth client, **Authorised redirect URI** = `<app origin>/api/v1/integrations/google/callback`; Google Ads API enabled; API access for the Cloud project |
Google sign-in is optional for local use: with `AUTH_PROVIDER=dev` (development only) users that already exist can sign in by email. `AUTH_PROVIDER=dev` is refused when `APP_ENV=production`.

## Google Ads: no developer token
Google **sunset developer tokens on 2026-09-09** ([official guide](https://developers.google.com/google-ads/api/docs/api-policy/developer-token)). Sending one is optional and ignored, so the app neither requires nor sends `GOOGLE_ADS_DEVELOPER_TOKEN`. What matters now is the **API access level of the Google Cloud project that owns your OAuth client** (Test, Explorer, Basic or Standard), shown on the Google Ads API Overview page in Cloud Console:
- Test access works with Google Ads *test accounts* only. Calling a production account returns `CLOUD_PROJECT_NOT_APPROVED_FOR_PRODUCTION`; the Integrations page then tells you to apply for **Explorer access** (automatic) for that project.
- The Google Ads API must be enabled in that project; the connection test says so if it is not.
- The Google API does not expose the access level, so the dashboard shows what the real responses prove (API enabled; test-only / production account readable) and links to the Overview page.

## Google Ads IDs
`GOOGLE_ADS_LOGIN_CUSTOMER_ID` is the **manager (MCC)** account, sent as the `login-customer-id` header; a manager account is no longer required unless you manage several accounts through it. `GOOGLE_ADS_CUSTOMER_ID` is a **client** account (the one the connection test must read). Dashes are accepted and removed. Putting the manager ID in both makes the app query the manager itself, which has no campaigns (the audit warns).

## Groq
Groq is used as soon as `GROQ_API_KEY` and `GROQ_MODEL` are set — here or on the Integrations page (recommended, then run Test Connection). `GROQ_MODEL` drafts; `GROQ_CHALLENGER_MODEL` critiques (blank = same model). Model names are not hard-coded and **which models you can use depends on your Groq plan**: the Integrations page lists what your key can access and sends a real JSON-mode request to the primary and the challenger model. Use a different model family for the challenger so the critique is independent. See https://console.groq.com/docs/models.

## Checklist

### Core

| Variable | Needed | Default | Purpose | Read by |
|---|---|---|---|---|
| `APP_ENV` | optional | `development` | development | test | staging | production. production switches on the safety guards. | core/config.py, main.py |
| `LOG_LEVEL` | optional | `INFO` | Python log level. | main.py |

### Safety

| Variable | Needed | Default | Purpose | Read by |
|---|---|---|---|---|
| `GOOGLE_ADS_WRITES_ENABLED` | optional | `false` | Key 1 of 2 for real Google Ads writes. Keep false until you are ready; a Super Admin must also authorise. | services/safety.py, integrations_service.py |
| `AUTO_NEGATIVES_MODE` | optional | `shadow` | shadow = negatives are only proposed; active = safe negatives may be applied (also needs a system setting). | services/safety.py, optimization.py |
| `AUTO_NEGATIVES_DAILY_LIMIT` | optional | `25` | Maximum automatic negatives per day. | services/optimization.py |
| `EMERGENCY_BRAKE_WRITE_MODE` | optional | `simulate` | simulate | disabled | real — what the emergency brake does to Google Ads. | services/emergency.py, safety.py |

### Login

| Variable | Needed | Default | Purpose | Read by |
|---|---|---|---|---|
| `AUTH_PROVIDER` | optional | `dev` | dev = email login for users that already exist (local development only; never allowed in production); google = Google sign-in. | adapters/registry.py, auth_users.py |

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
| `SMTP_HOST` | when: alert emails are wanted | — | SMTP server for alert emails. | adapters/notifications |
| `SMTP_PORT` | optional | `587` | SMTP port (STARTTLS). | adapters/notifications |
| `SMTP_USER` | optional | — | SMTP username (blank = no login). | adapters/notifications |
| `SMTP_PASSWORD` 🔒 | optional | — | SMTP password. | adapters/notifications |
| `SMTP_FROM` | optional | `alerts@localhost` | From address for alert emails. | adapters/notifications |

### Mock & crawler

| Variable | Needed | Default | Purpose | Read by |
|---|---|---|---|---|
| `CRAWLER_MAX_PAGES` | optional | `40` | Pages crawled per website. | services/crawler_service.py |
| `CRAWLER_MAX_DEPTH` | optional | `2` | Link depth crawled. | services/crawler_service.py |
| `CRAWLER_TIMEOUT_SECONDS` | optional | `10` | Per-request crawler timeout. | adapters/crawler/__init__.py |

🔒 = secret: never commit, never printed by the audit.

### Retired — do nothing, remove from `.env`

| Variable | Why |
|---|---|
| `GOOGLE_ADS_DEVELOPER_TOKEN` | Not needed: Google sunset developer tokens on 2026-09-09. API access is now tied to the Google Cloud project that owns your OAuth client (see the Google Ads API overview page in Cloud Console). The app no longer sends or requires it. Remove it from .env. |
| `GOOGLE_CLIENT_SECRET` | Not used: sign-in verifies Google ID tokens with the client ID only (no authorization-code exchange). Remove it from .env. Do not confuse it with GOOGLE_ADS_CLIENT_SECRET. |
| `GOOGLE_REDIRECT_URI` | Not used: Google sign-in has no redirect (the Google button returns a token to the page). The Ads redirect is GOOGLE_ADS_REDIRECT_URI. |
| `KEYWORD_RESEARCH_MODE` | Not used: Keyword Planner follows the Google Ads connection on the Integrations page. |
| `INTEGRATION_MODE` | Removed: there is no mock mode any more. Every integration is real; configure it on the Integrations page. |
| `GOOGLE_ADS_MODE` | Removed: the app always talks to the real Google Ads API once it is configured; writes stay gated by the two safety keys. |
| `PLACES_MODE` | Removed: Places is used as soon as its API key is saved on the Integrations page. |
| `PAGESPEED_MODE` | Removed: PageSpeed is used as soon as it is configured on the Integrations page. |
| `NOTIFIER_MODE` | Removed: alert emails are sent whenever SMTP_HOST is set. |
| `CRAWLER_MODE` | Removed: the website crawler always fetches real pages (SSRF-guarded). |
| `AI_PROVIDER` | Removed: Groq and Claude are used as soon as they are configured on the Integrations page. |
| `GROQ_ENABLED` | Removed: configure Groq on the Integrations page (or GROQ_API_KEY / GROQ_MODEL). |
| `MOCK_SEED` | Removed with the simulated world. |
| `TEST_MODE` | Removed with the simulated clock and mock controls. |

### Read outside the backend

| Variable | Purpose |
|---|---|
| `VITE_API_TARGET` | Vite dev server: where /api is proxied (default http://localhost:8000). |
| `CODESPACES` | Set by GitHub Codespaces; read by scripts/start-demo.sh. |
| `GAA_ENV_FILE` | Overrides which .env file the backend loads (none = no file; used by tests). |
