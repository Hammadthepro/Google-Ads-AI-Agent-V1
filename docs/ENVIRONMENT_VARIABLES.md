# Environment Variables

Status: **Phase 0 — plan.** `.env.example` will be created in Phase 1. Never commit `.env`.

| Variable | Default (dev) | Required in mock | Notes |
|---|---|:-:|---|
| `APP_ENV` | `development` | ✔ | `development` / `test` / `staging` / `production` |
| `INTEGRATION_MODE` | `mock` | ✔ | Default for every `*_MODE` |
| `TEST_MODE` | `true` | ✔ | Enables SimulatedClock + mock scenario controls; forbidden in production |
| `GOOGLE_ADS_WRITES_ENABLED` | `false` | ✔ | Key 1 of 2 for real writes |
| `GOOGLE_ADS_MODE` / `KEYWORD_RESEARCH_MODE` / `PLACES_MODE` / `PAGESPEED_MODE` / `CRAWLER_MODE` / `NOTIFIER_MODE` | inherit / `fixture` for crawler | | Per-integration override (`mock`/`real`) |
| `AUTH_PROVIDER` | `mock` | ✔ | `mock` / `google`; mock rejected in production |
| `AI_PROVIDER` | `mock` | ✔ | `mock` / `groq` |
| `GROQ_ENABLED` | `false` | | Must be true with `AI_PROVIDER=groq` |
| `GROQ_API_KEY` | — | | Secret |
| `GROQ_MODEL` | — | | Required when Groq enabled; no hardcoded default |
| `GROQ_CHALLENGER_MODEL` | = `GROQ_MODEL` | | Optional different model for independence |
| `DATABASE_URL` | `postgresql+psycopg://…` | ✔ | |
| `REDIS_URL` | `redis://redis:6379/0` | ✔ | |
| `APP_SECRET_KEY` | — | ✔ | Session/CSRF signing; generate locally |
| `TOKEN_ENCRYPTION_KEY` | — | real only | Encrypts OAuth refresh tokens (separate from APP_SECRET_KEY) |
| `CORS_ORIGINS` | `http://localhost:5173` | ✔ | Comma list |
| `SESSION_COOKIE_SECURE` | `false` dev / `true` prod | | |
| `BOOTSTRAP_ADMIN_EMAIL` | — | | First Super Admin |
| `ALLOWED_LOGIN_DOMAINS` | — | | D-22 |
| `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` / `GOOGLE_REDIRECT_URI` | — | | App login (OIDC) |
| `GOOGLE_ADS_CLIENT_ID` / `GOOGLE_ADS_CLIENT_SECRET` / `GOOGLE_ADS_REDIRECT_URI` | — | | Ads OAuth |
| `GOOGLE_ADS_DEVELOPER_TOKEN` | — | | **Added** — required by Google Ads API |
| `GOOGLE_ADS_REFRESH_TOKEN` | — | | Dev/staging only; production tokens live encrypted in DB |
| `GOOGLE_ADS_LOGIN_CUSTOMER_ID` / `GOOGLE_ADS_CUSTOMER_ID` | — | | MCC id / default account, digits only |
| `GOOGLE_PLACES_API_KEY` | — | | |
| `PAGESPEED_API_KEY` | — | | |
| `MOCK_SEED` | `42` | | Deterministic mock data |
| `CRAWLER_MAX_PAGES` / `CRAWLER_MAX_DEPTH` / `CRAWLER_TIMEOUT_SECONDS` | `40` / `2` / `10` | | |
| `AUTO_NEGATIVES_MODE` | `shadow` | | `shadow` / `active` |
| `AUTO_NEGATIVES_DAILY_LIMIT` | `25` | | Per client per day |
| `EMERGENCY_BRAKE_WRITE_MODE` | `simulate` | | `simulate` / `disabled` / `real` (real needs D-09) |
| `LOG_LEVEL` | `INFO` | | Tokens always redacted |

Business thresholds (enough-data, emergency, approvals) live in `system_settings` (editable, audited), seeded from documented test defaults — not in env.
