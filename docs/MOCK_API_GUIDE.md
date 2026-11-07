# Mock API Guide

Status: **Implemented.** Code: `backend/app/mock_world/` (seed, simulator, fixtures, failures) and `backend/app/adapters/*`.

## 1. How the full app runs without credentials
- `INTEGRATION_MODE=mock` → registry loads every mock adapter. `AUTH_PROVIDER=mock` gives a dev login screen (pick a seeded user). `AI_PROVIDER=mock` returns schema-valid fixture JSON per purpose and scenario.
- Mock Google Ads, Places and PageSpeed are **stateful**, backed by `mock_*` tables, so created campaigns survive refreshes and restarts.
- The crawler runs in `CRAWLER_MODE=fixture`: HTML/robots/sitemap fixtures in `backend/fixtures/sites/<domain>/` are served through the *real parsing code* (BeautifulSoup/lxml/extruct), so the parsing logic is genuinely tested.
- `SimulatedClock` lets tests and admins advance time; the mock world generates deterministic daily metrics for elapsed days.

## 2. Determinism
- `MOCK_SEED` (default `42`) drives a seeded RNG per (seed, account, entity, date) — the same scenario yields identical numbers on every run.
- `make reset-mock` drops and reseeds `mock_*` and demo app data; `make seed` seeds only if empty. Users and system settings are preserved unless `--all`.

## 3. Failure injection
Rules stored in `mock_failure_injections` (and settable per test via fixture/header `X-Mock-Scenario` in TEST_MODE only):

| Failure | Behaviour |
|---|---|
| `auth_failure` | `AuthError` (expired/invalid token) |
| `permission_denied` | `PermissionError` for the account |
| `rate_limited` | `RateLimited(retry_after)` |
| `timeout` | Raises before response; **optionally after commit** (`timeout_after_commit`) to test reconciliation |
| `partial_data` | Some fields/rows missing, `PartialData` flag |
| `empty` | Valid empty result |
| `invalid_input` | Field-level validation error |
| `outage` | `ServiceUnavailable` |
| `missing_data` | e.g. no GBP listing, no PageSpeed report |
Scope: adapter + method + optional entity, with `times` / `probability` (seeded).

## 4. Seeded dataset (minimum)
| Client | Businesses | Accounts | Tracking | Scenarios |
|---|---|---|---|---|
| Northside Home Services (fictional) | Northside Plumbing; Northside HVAC | 1 account, 2 websites/phones | Plumbing GREEN, HVAC AMBER | Healthy campaign; campaign requiring approval; manual change made outside app |
| Brightsmile Dental Group (fictional) | Brightsmile Dental | 1 account | RED (duplicate GA-import conversion, no call min length) | Broken tracking; no conversions; irrelevant search terms ("dental assistant jobs", "diy teeth whitening") |
| Summit Legal Partners (fictional) | Summit Injury Law | 1 account + read-only PMax campaign | GREEN | Overspending campaign; landing page outage; insufficient-data new campaign |

All names, reviews and listings are fictional and labelled **SIMULATED**. Includes competitors (3 per business), reviews, website fixtures, keyword research results, search terms, approvals in each state and emergency alerts.

## 5. Mock Google Ads capabilities
Accessible customers, hierarchy, campaigns/budgets/ad groups/keywords/negatives/ads/assets/locations, conversion actions, daily metrics, impression share (search, budget-lost, rank-lost), search terms, call view, change events, recommendations, experiments + arms, `validate_only`, atomic batch mutate with temp IDs, partial-failure errors, and Google-style policy checks for common violations (excess capitalisation, punctuation, character limits).

## 6. What the mock cannot prove
It cannot prove real Google policy approval, real auction behaviour, Keyword Planner accuracy, real tag firing, or real API quotas. Results from mock mode are never presented as forecasts of real performance.

## 7. As built
- **Accounts:** MCC `1000000000`; `1110000001` Northside Home Services, `1110000002` Brightsmile Dental Group, `1110000003` Summit Legal Partners, `1110000004` *New Client Sandbox* (empty, unlinked: use it to onboard a brand-new client).
- **Storage:** simulated Google Ads resources live in one generic table, `mock_gads_resources` (resource_type + JSON data, using Google-style resource names), with daily metrics, search terms, calls and change events in their own `mock_gads_*` tables.
- **Scenario per campaign:** healthy · budget_limited (+ manual changes) · insufficient_data (new, learning) · broken_tracking (conversions stop 12 days ago) · irrelevant_terms · overspend (today's spend spike) · landing_outage (`/personal-injury` → 503) · Performance Max (read-only).
- **Tracking states at seed:** Northside Plumbing GREEN · Northside Heating & Air AMBER (no call length, too few leads) · Brightsmile Dental RED (no Ads tag, MANY_PER_CLICK counting, duplicate GA4 import, conversions stopped) · Summit Injury Law GREEN.
- **Conversion lag:** a day's conversions are reported at 55% / 70% / 85% / 100% on days 0–3, so the 30-day re-download changes recent numbers.
- **Failure kinds:** `auth_failure, permission_denied, rate_limited, timeout, timeout_after_commit, not_found, missing_data, invalid_input, outage, empty, partial_data, invalid_json, schema_mismatch`. Target writes only with entity `:write`, or validate-only calls with `:validate`.
- **Controls:** System Settings → advance time / reset / inject failures, or CLI `python -m app.cli advance N` and `reset-mock`.
