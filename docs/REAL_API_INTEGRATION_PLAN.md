# Real API Integration Plan

Status: **Phase 0 — plan.** No real integration is enabled. Access requirements below must be re-verified against official documentation at the time each adapter is built (Phase 16); Google changes access tiers and procedures.

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
| Google Ads **manager account (MCC)** | Required to apply for a developer token; manage client accounts | Normal accounts are linked under it |
| Google Ads API **developer token** | Required on every Google Ads API call (read and write) | Applied for in the MCC's API Center; access level determines whether production accounts can be used — verify current levels and approval process |
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
3. **Google Ads API** — google-ads library pinned to a supported API version; developer token; `login_customer_id` for MCC; read-only GAQL first (accounts, campaigns, metrics, search terms, change events); then `validate_only` mutates; writes last.
4. **Google Places API** — Text Search + Place Details (New); field masks to control cost; label LIVE; handle missing listings.
5. **PageSpeed API** — `runPagespeed` mobile + desktop separately; timeouts (it is slow); cache results.
6. **Live website crawling** — SSRF guard, robots.txt, depth 2, page cap, timeouts, Playwright only when content is JS-rendered.
7. **Groq API** — official SDK; configurable model; verify structured-output / JSON-mode support for the chosen model; rate-limit handling; cost logging.

## 4. Things never done automatically
Switching to real because credentials appear; enabling real writes; enabling real emergency writes; leaving shadow mode for auto-negatives.
