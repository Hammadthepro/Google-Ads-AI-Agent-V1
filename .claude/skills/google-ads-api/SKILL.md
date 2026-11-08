---
name: google-ads-api
description: Use when working on this repo's Google Ads API integration — the RealGoogleAdsAdapter, OAuth / refresh tokens, developer token and access levels, GAQL queries, account discovery (MCC / login-customer-id), campaign creation via GoogleAdsService.Mutate, validate_only, and mapping API errors. Google Search campaigns only.
---

# Google Ads API (this project)

## Where the code lives
| Concern | File |
|---|---|
| Adapter interface (contract every implementation follows) | `backend/app/adapters/google_ads/base.py` |
| Real adapter (official `google-ads` Python library) | `backend/app/adapters/google_ads/real.py` |
| Mock adapter (DB-backed simulated accounts, same shapes) | `backend/app/adapters/google_ads/mock.py` |
| Adapter selection (mode → mock/real); never inferred from credentials | `backend/app/adapters/registry.py` |
| Credentials store (encrypted) + connection tests | `backend/app/services/integrations_service.py` |
| OAuth start/callback | `backend/app/api/routers/integrations.py` |
| Single write checkpoint | `backend/app/services/safety.py` (WriteGuard) → `execution.py` |
| Keyword Planner + geo targets | `backend/app/adapters/keyword_research/__init__.py` |

## Non-negotiable safety rules (enforced in code — never weaken)
1. Business logic never imports `google.ads`; only adapters do (tested in `tests/security`).
2. Writes require: operating mode `execution` **and** `GOOGLE_ADS_WRITES_ENABLED=true` **and** Super Admin authorization **and** `writes_allowed` on the account **and** an APPROVED approval whose payload hash matches. The real adapter re-checks key 1/2 itself.
3. Creates are `PAUSED`; enabling is a separate ENABLE approval; every write is read back (`execution.verify_read_back`).
4. Timeouts = unknown outcome → `execution.reconcile` reads the account; **never blindly retry a create**.
5. No `remove` operations. Read-only mode may call `validate_only=true` (Google creates nothing).
6. Never log tokens; use `core.util.redact` and `fingerprint`.

## Authentication facts (official docs)
- Every request needs a **developer token** (from a manager account's API Center) plus **OAuth 2.0** credentials for a Google account with access to the customer. Docs: https://developers.google.com/google-ads/api/docs/oauth/overview
- Developer token **access levels** (Test / Explorer / Basic / Standard) limit which accounts and how many operations you can use. Check the current rules before promising anything: https://developers.google.com/google-ads/api/docs/api-policy/access-levels
- When the authenticated user reaches a client **through a manager account**, send `login-customer-id` = the manager's ID (digits only). Docs: https://developers.google.com/google-ads/api/docs/concepts/call-structure
- OAuth scope: `https://www.googleapis.com/auth/adwords`. Use `access_type=offline` + `prompt=consent` to receive a refresh token; validate `state`; PKCE is supported by Google's OAuth server.

## GAQL workflow
1. Build/verify fields with the official query builder: https://developers.google.com/google-ads/api/fields/latest/overview_query_builder
2. Query language reference: https://developers.google.com/google-ads/api/docs/query/overview
3. Use `search_stream` for reports; segment by `segments.date` for daily data; money is in **micros**.
4. Common resources used here: `customer`, `customer_client` (hierarchy), `campaign`, `campaign_budget`, `ad_group`, `ad_group_ad`, `ad_group_criterion`, `campaign_criterion`, `keyword_view`, `search_term_view`, `call_view`, `change_event`, `conversion_action`, `recommendation`.
5. `change_event` requires a date filter and a `LIMIT` (see its field reference), so keep the existing query shape.

## Mutate workflow (campaign creation)
1. Build operations as dicts (`planner.build_*_payload`): budget → campaign → criteria → shared set → ad groups → keywords → ads → assets.
2. Cross-reference with **temporary resource names** (negative IDs, e.g. `customers/123/campaignBudgets/-1`) inside **one** `GoogleAdsService.Mutate` request, with `partial_failure=false` so it's atomic. Docs: https://developers.google.com/google-ads/api/docs/mutating/overview
3. Run `validate_only=true` first (validation engine does this), then the real call through `ExecutionService`.
4. Read back each `resource_name`, compare against the payload, and only then create the ENABLE approval.

## Error handling checklist
- [ ] Map `GoogleAdsException` failures to adapter errors (`AuthError`, `PermissionDenied`, `RateLimited`, `AdapterTimeout`, `InvalidInput`, `ServiceUnavailable`) in `real.py::_map_error`
- [ ] Surface `request_id` and the failing field path (never the token) in diagnostics
- [ ] `AUTHENTICATION_ERROR` / `invalid_grant` → status **Authorization Expired** → reconnect via OAuth
- [ ] `DEVELOPER_TOKEN_NOT_APPROVED` / access-level errors → explain the access level needed; don't retry
- [ ] `USER_PERMISSION_DENIED` → usually a missing `login-customer-id` or no access to that customer
- [ ] `RESOURCE_EXHAUSTED` / quota → back off; don't hammer
- [ ] Policy violations on ads → show the policy topic; human edit; never auto-exempt

## Before you change the real adapter
- [ ] Confirm the API version the installed `google-ads` library targets (`pip show google-ads`) and read that version's release notes
- [ ] Add or extend a contract test in `tests/contract/` using a fake client (no network)
- [ ] Keep read methods returning the same dict shapes as the mock (the UI and services depend on them)
- [ ] Don't claim a live capability works until a real request succeeded in **read-only** mode

## Not implemented / known gaps
Real `ExperimentService` (A/B in real mode) isn't implemented yet. See `docs/KNOWN_ISSUES.md`.
