# Real API Integration Plan

Status: **Real adapters implemented, not verified live.** No real integration is enabled; real writes are disabled. Access requirements below must be re-verified against official documentation at the time each adapter is built (Phase 16); Google changes access tiers and procedures.

## 1. Accounts, credentials, subscriptions, permissions

### Required now (Phases 1–15, mock mode)
| Item | Why | Status |
|---|---|---|
| Nothing external | Mock mode runs with zero credentials | — |
| Groq API key (in local `.env`, never in chat) | Only for scenario 16 (real AI drafts) | You have one; optional until Phase 9 |
| A GitHub repository for this project | This cloud container is ephemeral; work must be pushed to persist | **Needed before Phase 1** |

### Required later (real integrations)
| Item | Used for | Notes / to verify |
|---|---|---|
| Google Cloud project | OAuth clients, API enablement, billing | One project can host all |
| OAuth consent screen + OAuth client (web) for app login | Sign in with Google (OIDC) | Scopes: `openid email profile` |
| OAuth client for Google Ads access | Google Ads OAuth (`https://www.googleapis.com/auth/adwords`) | Separate consent from login |
| Google Ads **manager account (MCC)** | Optional: only needed to manage several client accounts through one login (`login-customer-id`) | Normal accounts are linked under it |
| Google Ads API **access level** | Test / Explorer / Basic / Standard, granted to the Google Cloud project that owns the OAuth client | Apply on the Google Ads API Overview page in Cloud Console (developer tokens are sunset) |
| Google Places API (New) key + billing | Business Profile & competitor audits | Paid per request; restrict key by API + IP |
| PageSpeed Insights API key | Speed audits | Free with quota |
| Hosting (VPS/cloud), domain, TLS | Production | |
| Secrets manager | Production secrets | e.g. cloud provider secret store |
| Email provider (SMTP/transactional) | Alerts, approval reminders | Channel is D-10 |

### Optional
| Item | Value |
|---|---|
| Semrush API (paid API units) | Competitor bidding/ranking keywords. Note: a Semrush connector available inside Claude chats is **not** an app integration. |
| Error monitoring (e.g. Sentry) | Production error visibility |
| Slack webhook | Alert channel alternative |

## 2. Replacement procedure (every adapter)
1. Implement `Real*Adapter` behind the existing interface; no business-logic change.
2. Run the adapter **contract test suite** against the real adapter (read-only methods).
3. Test authentication failure, permission errors, rate limiting, timeouts, error mapping.
4. Verify returned data shape and units (micros, time zones, enums).
5. Switch only that integration's `*_MODE=real` in a staging environment.
6. Keep writes disabled; confirm `WriteGuard` blocks writes with real adapter loaded.
7. Record results in TEST_RESULTS.md; update integration status screen.
8. Real writes: separate written authorization from you, then Super Admin DB authorization, account allowlist, first write = a single paused resource on a sacrificial campaign.

## 3. Per-integration checklists
1. **Google authentication** — consent screen; client id/secret; redirect URI; backend ID-token verification (signature, `aud`, `iss`, `exp`, `email_verified`, optional `hd` domain restriction); first-admin bootstrap by email.
2. **Google Ads OAuth** — authorization + callback with `state`/PKCE; offline access for refresh token; encrypted storage; account discovery (`ListAccessibleCustomers`); permission status; disconnect (revoke + delete ciphertext).
3. **Google Ads API** — google-ads library pinned to a supported API version (35.x runs without a developer token); `login_customer_id` for MCC; read-only GAQL first (accounts, campaigns, metrics, search terms, change events); then `validate_only` mutates; writes last.
4. **Google Places API** — Text Search + Place Details (New); field masks to control cost; label LIVE; handle missing listings.
5. **PageSpeed API** — `runPagespeed` mobile + desktop separately; timeouts (it is slow); cache results.
6. **Live website crawling** — SSRF guard, robots.txt, depth 2, page cap, timeouts, Playwright only when content is JS-rendered.
7. **Groq API** — official SDK; configurable model; verify structured-output / JSON-mode support for the chosen model; rate-limit handling; cost logging.

## 4. Things never done automatically
Switching to real because credentials appear; enabling real writes; enabling real emergency writes; leaving shadow mode for auto-negatives.

## 5. Step-by-step: connecting real credentials (when you are ready)
Do these in order. Each step is reversible by setting its `*_MODE` back to `mock`. Writes stay off throughout.

**A. App login (Sign in with Google)**
1. Google Cloud Console → APIs & Services → OAuth consent screen (Internal if you use Google Workspace).
2. Credentials → Create OAuth client ID → *Web application*. Add your app origin (e.g. `https://ads.yourdomain.com`) to *Authorized JavaScript origins*.
3. In `.env`: `AUTH_PROVIDER=google`, `GOOGLE_CLIENT_ID=…`, `ALLOWED_LOGIN_DOMAINS=yourdomain.com`, `BOOTSTRAP_ADMIN_EMAIL=you@yourdomain.com`.
4. Restart. Sign in with the bootstrap email (it becomes Super Admin once), then invite the team under **Users & Roles**.

**B. Groq (AI)** — needed first, so you can test real AI drafts on mock data
1. Put the key in `.env` (never in chat or code): `GROQ_API_KEY=…`, `GROQ_ENABLED=true`, `AI_PROVIDER=groq`, `GROQ_MODEL=<a current Groq model that supports JSON mode>` (optionally `GROQ_CHALLENGER_MODEL`).
2. Run `pytest tests/integration/test_tracking_audits_ai.py -k live_groq` and the regression cases (**Learning → Re-run all cases**). Every draft must pass schema validation.

**C. Google Ads API — read-only first**
1. Create or use a Google Ads **manager account (MCC)** and link the client accounts under it.
2. Google Cloud Console → **Google Ads API Overview** for the project that owns your OAuth client: check the API access level. Production accounts need **Explorer** access or higher (apply there; no developer token — they were sunset on 2026-09-09).
3. Google Cloud → enable the *Google Ads API* → create an OAuth client for Google Ads (redirect URI `https://<host>/api/v1/ads-oauth/callback`).
4. `.env`: `GOOGLE_ADS_CLIENT_ID`, `GOOGLE_ADS_CLIENT_SECRET`, `GOOGLE_ADS_REDIRECT_URI`, `GOOGLE_ADS_LOGIN_CUSTOMER_ID` (MCC, digits only), `TOKEN_ENCRYPTION_KEY` (Fernet key).
5. Set `GOOGLE_ADS_MODE=real` and `KEYWORD_RESEARCH_MODE=real`, keep **`GOOGLE_ADS_WRITES_ENABLED=false`**, restart.
6. **Integrations → Connect Google Ads (OAuth)** as Super Admin → **Test** → **Google Ads Accounts → Discover** → link accounts → **Sync (read)**. Compare campaigns/metrics with the Google Ads UI.
7. Run **Audit only** tasks and the daily sync for a week. Validation now calls Google's `validate_only` (creates nothing).

**D. Places & PageSpeed**
`GOOGLE_PLACES_API_KEY` (restrict it to the Places API, and by IP), `PAGESPEED_API_KEY`, then `PLACES_MODE=real`, `PAGESPEED_MODE=real`. For live website crawling, set `CRAWLER_MODE=live`.

**E. Before ANY real write (needs your explicit written authorization)**
1. Close the real-adapter gaps in KNOWN_ISSUES.md (criterion resource names, geo targets, experiments) and run the contract tests against the real adapter.
2. Choose one sacrificial test campaign and account. Set that account's `writes_allowed=true`; all others stay false.
3. Set `GOOGLE_ADS_WRITES_ENABLED=true` (key 1), then as Super Admin → **System Settings → Authorize real writes** with a written reason (key 2).
4. Run one *Add an ad* task → approve → execute. Confirm it was created **PAUSED** and read back as verified in Google Ads. Then revoke authorization again until the rollout plan is agreed.
5. Real emergency-brake writes and ACTIVE auto-negatives are separate decisions (D-09, D-21) and stay off.
