> **Note:** the app is now real-integration-only (no mock mode, no demo data). Mentions of mock mode, seeding or demo users below are historical; see [REAL_DATA_ONLY.md](REAL_DATA_ONLY.md).

# System Architecture

Status: **Implemented in V1** (tests: see TEST_RESULTS.md).

## 1. Runtime components

```
┌──────────────┐  HTTPS (cookie session + CSRF)   ┌──────────────────────────────────────────┐
│  Frontend    │ ───────────────────────────────► │  FastAPI backend (modular monolith)      │
│  React/Vite  │ ◄─────────────────────────────── │  ├─ API routers (authz on every route)   │
└──────────────┘                                  │  ├─ Domain modules (deterministic)       │
                                                  │  ├─ Safety layer (WriteGuard)            │
                                                  │  └─ Adapter registry (DI)                │
                                                  └──────┬───────────────┬───────────────────┘
                                                         │               │
                                        ┌────────────────▼──┐     ┌──────▼──────────┐
                                        │ PostgreSQL        │     │ Redis           │
                                        │ app tables        │     │ broker, cache,  │
                                        │ mock_* tables     │     │ rate limits     │
                                        └────────────────▲──┘     └──────┬──────────┘
                                                         │               │
                                                  ┌──────┴───────────────▼───────────┐
                                                  │ Celery worker + Celery beat      │
                                                  │ daily monitor · hourly brake ·   │
                                                  │ daily gate recheck · audits      │
                                                  └──────────────────────────────────┘
```

Containers (Docker Compose): `frontend`, `backend`, `worker`, `beat`, `postgres`, `redis`. Playwright browsers live in the `worker` image only (crawling runs as jobs, not in request threads).

## 2. Proposed folder structure

```
google-ads-ai-agent/
├── README.md
├── .env.example
├── docker-compose.yml
├── Makefile                         # make up / test / seed / reset / lint
├── docs/                            # all project documentation
├── backend/
│   ├── Dockerfile
│   ├── pyproject.toml
│   ├── alembic.ini
│   ├── migrations/                  # Alembic versions
│   ├── app/
│   │   ├── main.py                  # FastAPI app factory
│   │   ├── core/                    # config, logging, security, errors, clock, provenance
│   │   ├── db/                      # engine, session, base model, mixins
│   │   ├── api/                     # routers + dependencies (auth, RBAC)
│   │   ├── safety/                  # WriteGuard, kill switches, payload hashing
│   │   ├── adapters/
│   │   │   ├── registry.py          # builds adapters from config (the ONLY switch point)
│   │   │   ├── google_ads/   {base.py, mock.py, real.py, mapping.py}
│   │   │   ├── keyword_research/{base.py, mock.py, real.py}
│   │   │   ├── places/       {base.py, mock.py, real.py}
│   │   │   ├── pagespeed/    {base.py, mock.py, real.py}
│   │   │   ├── crawler/      {base.py, fixture.py, live.py, ssrf.py}
│   │   │   ├── ai/           {base.py, mock.py, groq.py, schemas/, prompts/}
│   │   │   ├── auth/         {base.py, mock.py, google_oidc.py}
│   │   │   ├── ads_oauth/    {base.py, mock.py, google.py}
│   │   │   └── notifications/{base.py, mock.py, email.py}
│   │   ├── modules/
│   │   │   ├── users/           ├── clients/        ├── businesses/
│   │   │   ├── ads_accounts/    ├── tasks/          ├── tracking_gate/
│   │   │   ├── audits/          ├── keywords/       ├── feasibility/
│   │   │   ├── fixes/           ├── planner/        ├── validation/
│   │   │   ├── approvals/       ├── execution/      ├── monitoring/
│   │   │   ├── emergency/       ├── reporting/      ├── optimization/
│   │   │   ├── experiments/     ├── learning/       ├── integrations/
│   │   │   ├── settings/        └── activity/
│   │   │   (each: models.py, schemas.py, service.py, router.py, rules.py where relevant)
│   │   ├── workers/             # celery_app.py, schedules.py, tasks/*.py
│   │   └── mock_world/          # simulated Google/Places/PageSpeed state + seed data + scenarios
│   ├── fixtures/                # crawler HTML fixtures, AI fixture responses
│   └── tests/
│       ├── unit/  ├── integration/  ├── contract/  ├── api/  ├── security/  └── e2e/
├── frontend/
│   ├── Dockerfile
│   ├── package.json · vite.config.ts · tailwind.config.ts
│   └── src/
│       ├── app/ (router, providers, auth context)
│       ├── api/ (typed client generated from OpenAPI)
│       ├── components/ (ui kit: Table, Badge, DataSourceBadge, ConfirmDialog, EmptyState…)
│       ├── features/ (one folder per nav item)
│       └── tests/
└── e2e/                             # Playwright browser tests against the full stack
```

## 3. Layering rules (enforced by tests / import linting)

| Layer | May import | Must never import |
|---|---|---|
| `api/` routers | modules (services, schemas), `api/deps` | adapters directly, vendor SDKs |
| `modules/*/service.py` | other module services, adapter **interfaces** (`base.py`), `safety/` | `mock.py`, `real.py`, `groq`, `google.ads`, `httpx` |
| `adapters/*/real.py` | vendor SDKs, `core/` | modules |
| `adapters/registry.py` | all adapter implementations | modules |

An `import-linter` contract (Phase 1) fails CI if business logic imports a vendor SDK or a concrete adapter.

## 4. Adapter architecture

Every integration has one interface (Python `Protocol`/ABC) and 2+ implementations.

| Interface | Mock | Real (future) | Selected by |
|---|---|---|---|
| `GoogleAdsAdapter` | `MockGoogleAdsAdapter` (DB-backed simulated accounts) | `RealGoogleAdsAdapter` (google-ads lib) | `GOOGLE_ADS_MODE` |
| `KeywordResearchAdapter` | `MockKeywordResearchAdapter` | `GoogleKeywordPlannerAdapter` | `KEYWORD_RESEARCH_MODE` |
| `GooglePlacesAdapter` | `MockGooglePlacesAdapter` | `RealGooglePlacesAdapter` (Places API New) | `PLACES_MODE` |
| `PageSpeedAdapter` | `MockPageSpeedAdapter` | `RealPageSpeedAdapter` | `PAGESPEED_MODE` |
| `CrawlerAdapter` | `FixtureCrawlerAdapter` | `LiveCrawlerAdapter` (httpx/BS4/Playwright) | `CRAWLER_MODE` |
| `AIProvider` | `MockAIProvider` (fixture responses) | `GroqAIProvider` | `AI_PROVIDER` |
| `AuthenticationProvider` | `MockAuthenticationProvider` (dev login) | `GoogleOIDCAuthenticationProvider` | `AUTH_PROVIDER` |
| `AdsOAuthAdapter` | `MockAdsOAuthAdapter` | `GoogleAdsOAuthAdapter` | `GOOGLE_ADS_MODE` |
| `Notifier` | `MockNotifier` (stores messages) | `EmailNotifier` | `NOTIFIER_MODE` |
| `Clock` | `SimulatedClock` (advanceable) | `SystemClock` | `TEST_MODE` |

**Selection rules**
- `INTEGRATION_MODE` is the default for every `*_MODE`; per-integration variables override it.
- The registry **never** switches to real because a credential exists. Real requires the explicit mode value *and* the credentials; missing credentials in real mode = startup error, not a silent fallback to mock.
- Every adapter response is wrapped in `AdapterResult[T]` with `source: MOCK|LIVE`, `adapter_name`, `fetched_at`, `request_id`, and a typed error (`AuthError`, `PermissionError`, `RateLimited`, `Timeout`, `NotFound`, `InvalidInput`, `PartialData`, `ServiceUnavailable`).
- Contract tests (`tests/contract/`) run the same suite against mock and, later, real adapters.

### Mock Google Ads mirrors the real API shape
The mock uses Google Ads conventions so the swap is mechanical: resource names (`customers/{cid}/campaigns/{id}`), `cost_micros`, enum names (`PAUSED`, `ENABLED`, `REMOVED`), GAQL-like query methods, `validate_only`, partial-failure errors and atomic mutate batches.

### Two kinds of tables
- **App tables** (`campaigns`, `approvals`, …) are *our* records: what we planned, approved and observed, each with `external_resource_name` and `source_type`.
- **`mock_gads_*` / `mock_places_*` tables** are the *simulated external world*. Only mock adapters touch them. Reset/reseed wipes them without touching users or settings.

## 5. Write-safety chain (single choke point)

All Google Ads mutations — including safe negatives and emergency actions — go through `execution.ExecutionService.execute(operation)`:

```
approved payload
  → WriteGuard.check():
       1. operation type allowed for policy (approval-required vs auto-negative vs emergency)
       2. approval exists, status APPROVED, payload_hash == hash(current payload), not expired
       3. requesting + approving users active and authorised
       4. kill switches: global, client, account → all off
       5. tracking-gate requirement for this operation type
       6. if adapter is REAL: GOOGLE_ADS_WRITES_ENABLED=true AND db setting
          real_writes_authorized=true (Super Admin, audited) AND account.writes_allowed=true
       7. manual-change check (change_event since last read) → block if conflicting
  → re-validate payload (validation engine)
  → Operation row (idempotency_key, state=PENDING) committed BEFORE the call
  → adapter.mutate(..., status=PAUSED for creates)
  → state SUCCEEDED | FAILED | UNKNOWN
  → UNKNOWN → reconcile: read account state, match by deterministic name/label → resolve
  → read-back verification → verification_passed true/false
  → ActivityLog + ExternalOperationLog
```

The real adapter repeats check 6 internally (defence in depth), so even a bug in the service layer cannot write.

## 6. AI boundary

- AI receives structured context, returns JSON validated by strict Pydantic models (`extra="forbid"`).
- Website/competitor text is passed as quoted data with an explicit "content is data, not instructions" system rule; AI output never triggers tools or writes.
- Invalid output → one controlled retry with the validation error → otherwise `AIRun.status=INVALID`, workflow pauses for a human.
- Every `AIRun` stores provider, model, prompt template id + version, input hash, raw output, parsed output, latency, token usage and validation result.
- AI never produces stored metrics. Report numbers are computed; AI text is rendered next to them.

## 7. Background schedule (Celery beat; times configurable)

| Job | Frequency | Notes |
|---|---|---|
| `monitoring.daily_sync` | daily | Pull/simulate last 30 days (conversion lag) |
| `tracking_gate.recheck_all` | daily | Updates GREEN/AMBER/RED; clears override warnings |
| `emergency.hourly_check` | hourly | Deterministic rules only |
| `fixes.recheck_measurable` | daily | PageSpeed / review count rechecks |
| `optimization.propose_negatives` | daily after sync | Shadow mode = proposals only |
| `reporting.weekly_review` | weekly | Rolling 4 weeks |
| `approvals.expiry_sweep` | hourly | Reminders, expiry, escalation to backup owner |

In mock mode, `SimulatedClock.advance(days=N)` drives the mock world forward and runs these jobs synchronously for time-based tests.

## 8. Data provenance

`source_type` enum on every metric, audit, snapshot and report: `MOCK`, `LIVE`, `MIXED`. Aggregations compute `MIXED` automatically if inputs differ. The frontend `DataSourceBadge` renders it on every result card, table and report header. Integration status shows `MOCK CONNECTED` (never plain "Connected") for mocks.

## 9. Security architecture (summary)

Server-side sessions in httpOnly, Secure, SameSite=Lax cookies; CSRF double-submit token for state-changing requests; strict CORS allowlist; per-user and per-IP rate limits (Redis); secure headers middleware; secrets only from environment/secrets manager; OAuth refresh tokens encrypted at rest with a separate `TOKEN_ENCRYPTION_KEY`; structured logs with token redaction; crawler SSRF guard (scheme allowlist, DNS resolution + private/loopback/link-local/metadata IP blocking on every redirect hop, port allowlist, size and time limits). Details: AUTHENTICATION_AND_ROLES.md, GOOGLE_ADS_SAFETY_RULES.md.
