# Integrations audit (2026-11-10)

Evidence is from the code at commit `84882a6` and from real requests made with the saved credentials in the development database (secrets are never
printed). Official-documentation facts come from search results for developers.google.com / platform.claude.com; the pages themselves could not be fetched
from this sandbox (the domains are not on its network allow-list), so anything below marked *(docs search)* should be re-checked on the live page.

## Deliverable A — per-integration audit

Legend: **Live** = a real request to the provider was made from this environment; **Unit** = covered only by tests with a stand-in HTTP transport/client.

| Integration | Current implementation | Missing configuration / gaps found | Actual blocker | Required action |
|---|---|---|---|---|
| Google Ads API | Real REST + `google-ads` library. OAuth refresh (live ✔), `listAccessibleCustomers` (live ✔), per-account `customer` query (live ✔), `customer_client` walk with sub-managers/pagination (Unit only; live run returns just the closed test manager), campaigns/ad groups/keywords/search terms/metrics (REST/library code; Unit; live read blocked), writes (`mutate`, validate_only, guarded by 2 keys; never run live). | Default-manager field accepts any 10 digits (a **client** ID, `4433432999`, was saved as manager); no check that it is a manager. Field/secret source (saved vs environment) is only visible for secrets. A saved-secret decryption failure is silently treated as "missing". | (1) Dashboard "Missing: client_id" could not be reproduced from the code (see B). (2) The Google user reaches no ENABLED manager: `9952730299` is a CLOSED test manager, `4929726198` CUSTOMER_NOT_ENABLED, `4433432999` is a non-test account (project has Test access → CLOUD_PROJECT_NOT_APPROVED_FOR_PRODUCTION). | Validate manager ID is a manager; show per-field source; surface decrypt failures. User: create an ENABLED test manager + test clients (works with Test access) or raise the project's access level. |
| Google Analytics 4 | `accountSummaries` (Admin API v1beta) + `runReport` (Data API v1beta) tests, scope `analytics.readonly`, shares the OAuth client of the Google Ads card, own refresh token. Not used by any workflow (no code reads GA4 data outside the test) — **partially implemented**. | No property picker (user must type the property ID); tests do not state that BOTH Admin API and Data API must be enabled; no dependency display. | Not authorised yet (no refresh token with the Analytics scope). | Property discovery + selection endpoint/UI; explain both APIs; dependency on the shared OAuth client. |
| Google Business Profile | `mybusinessaccountmanagement` accounts test only. No location listing, no review retrieval anywhere in the app — **test-only**. | No account/location selection; 403/429 is only a hint, not a distinct "Requires approval" state; a successful OAuth is not distinguished from API access. | GBP API access must be approved for the Cloud project (quota 0 until approved *(docs search)*). | Account + location discovery; state "Requires approval"; never "Connected" until accounts/locations are listed. |
| Keyword Planner | Real `KeywordPlanIdeaService.GenerateKeywordIdeas` through the Google Ads authorisation (REST test + library adapter). | Dependency chain not shown; explains only "no client account discovered". Needs Basic/Standard access *(docs search: Explorer excludes the planning services)*. | Blocked by Google Ads: no enabled manager → no client account. | Explicit dependency panel: Google Ads connected → manager chosen → client account discovered → access level; "Connected" only after real ideas return. |
| Groq | Real (`groq` SDK + REST test, JSON mode, rate-limit handling, tokens stored in `ai_runs`). | None functional. | none (live ✔: primary + challenger models). | Keep; regression-test. |
| Claude (Anthropic) | `anthropic` SDK provider + REST test (`GET /v1/models`, model must be in the list). Independent from Groq (different vendor, separate key/model, same inputs). | Model is a free-text box (not selectable from the API list); token usage not shown; **`competition._providers()` raises when Claude is unconfigured, so a missing Claude breaks the whole dual-AI plan instead of falling back to the single-AI mode the code already supports.** | Not configured (no key). | Model dropdown from the validated list; usage summary from `ai_runs`; graceful single-AI fallback with an explicit "Claude unavailable" note. |
| Google Places API (New) | Real `places:searchText` with `X-Goog-FieldMask`; adapter + test. Used by the audit (GBP/competitor sections). | Hint does not name the exact service/key restriction/billing. | Not configured (no key). | Exact activation instructions: enable *Places API (New)*, billing, key restricted to that API. |
| Website crawler | Real, SSRF-guarded (`check_url_safe` resolves DNS, blocks private/reserved ranges, re-checks every redirect hop; tests for rebinding and redirect-to-private). Used by every audit. | Test fetched `example.com` through DNS only; no timeout/size/content-type summary on the card. | none; in this sandbox DNS is unavailable, so the live fetch is BLOCKED here. | Card states the guarantees (limits) and tests a safe public URL. |
| SMTP | `EmailNotifier` using `SMTP_HOST/PORT/USER/PASSWORD/FROM` from **environment only**; STARTTLS always; card is read-only. | No DB-backed config, no from-name, no TLS mode choice, no test email. | Not configured. | Super-Admin form (secrets encrypted), TLS mode, connection test + explicit "send test email". |
| Google Sign-in | `GoogleOIDCAuthenticationProvider` verifies ID token signature, issuer, audience, email_verified, domain. `GOOGLE_CLIENT_ID` + `AUTH_PROVIDER=google` are **deployment settings** by design (changing them at runtime could lock everyone out). Separate from the Ads OAuth client. | Card gave no guidance on where the ID goes; no verification at all. | Not configured (deployment). | Mark "Deployment-managed", show exact steps, add a real check (client-ID format + Google's public keys reachable). |
| PostgreSQL / Supabase | One SQLAlchemy URL (`DATABASE_URL`); Alembic at start-up. Test = `select version()` + revision. | No SSL / read-write / pool / "is at head" checks; target not shown (host kind only is safe). | none (local DB fine; Supabase at 0006). | Deeper real checks; deployment-managed lock. |
| Redis | Celery broker + rate-limit counters. Test = PING only. | No broker/queue/worker/beat visibility. | none here. | PING + queue length + worker heartbeat (`inspect ping`) + last scheduled-job run. |

## Deliverable B — root cause report

**Why does Google Ads say `Missing: client_id` while the field shows a value?**
* The backend decides "missing" with `integrations_service.missing()` → `effective()` = environment (`GOOGLE_ADS_CLIENT_ID`) overlaid by saved settings; the
  field value is produced by the same `effective()`. Reproduced in a scratch database with the reported setup (secret + refresh token from the environment,
  client ID entered in the dashboard): before saving → `missing: ['client_id']`, field empty; after `PUT` → `missing: []`, field populated, test proceeds to the
  OAuth call. So the **backend merge logic is correct** for this scenario and I could not reproduce a state where the saved/merged value is present but "missing".
* What *can* produce exactly the screenshot: the field is a controlled input showing the **unsaved draft** (`settings[k] ?? f.value`). The reported manager ID
  `443-343-2999` has dashes, but the backend stores digits (`4433432999`) — so that text was typed, not loaded from the server. The draft looks identical to a
  saved value; if "Save" had not succeeded (error shown only below the buttons) or was not pressed, the server still has no client ID. Two further silent causes
  exist: saved secrets that fail to decrypt after `TOKEN_ENCRYPTION_KEY` changes are swallowed (treated as absent), and a client ID saved for one deployment
  database is not visible when the container points at another (COMPOSE_DATABASE_URL vs DATABASE_URL).
* Fixes implemented: per-field provenance (saved / environment / missing) shown on every field, explicit "Unsaved changes" state and an inline save result,
  decrypt-failure detection, and regression tests for the environment/database merge in every direction.

**Why do some integrations have no fields?** `PROVIDERS[...]["settings"]/["secrets"]` is the single source for the form. SMTP, Google Sign-in, Database and Redis are
`readonly` (environment only) and the crawler/Keyword Planner genuinely need no credentials. SMTP is the one that should be user-configurable → implemented.

**Credential sources today.** Environment: any `GOOGLE_ADS_*`, `GROQ_*`, `PAGESPEED_API_KEY`, `GOOGLE_PLACES_API_KEY`, `ANTHROPIC_API_KEY`, `SMTP_*`, `GOOGLE_CLIENT_ID`,
`DATABASE_URL`, `REDIS_URL`, `TOKEN_ENCRYPTION_KEY`, `APP_SECRET_KEY`. Database (`integration_configs`, Fernet-encrypted `secrets_ciphertext`, settings JSON):
whatever was saved on the page or obtained by the OAuth callback.

**Precedence (now documented and tested):** saved database value > environment value > built-in default; blank/empty values never override a non-empty one
in either direction; infrastructure settings (`DATABASE_URL`, `REDIS_URL`, keys, `AUTH_PROVIDER`, `GOOGLE_CLIENT_ID`) are environment-only and cannot be set from the page.

**Frontend/backend schema alignment.** Aligned for settings/secrets (`PUT /integrations/{p}` accepts free dicts; the service rejects unknown keys). Gaps: no per-field
state in the response (fixed), no dependency information (added).

**Do connection tests call the provider?** Yes for Google Ads (OAuth token endpoint, `listAccessibleCustomers`, `customer`, `customer_client`), GA4, GBP, Groq
(real JSON-mode completion), Claude (`/v1/models`), PageSpeed, Places, crawler, SMTP (socket + login), DB (query), Redis (PING). None returns a hard-coded success.
Weakness: Claude only lists models (no completion), so "Connected" there means key + model exist, not that a completion works → a 1-token completion was added.

## Deliverable C — architecture and implementation plan (implemented in stages below)

1. **Schema (migration 0007):** `integration_configs.workspace_id` (FK → workspaces, backfilled to the default workspace, unique per provider+workspace) so one
   workspace's credentials never apply to another; all service reads/writes are workspace-scoped.
2. **Config service:** per-field provenance; `deployment_managed` flag; decrypt-failure flag; SMTP moved to DB-backed settings + secret (env fallback);
   manager-ID validation; precedence tests.
3. **Dependency engine:** `DEPENDS_ON` graph evaluated live → `blocked_by` list and the new display states (*Credentials present, Configuration saved,
   Blocked by dependency, Requires approval, Connected, Connection failed, Disabled, Not configured*). Dependents are never "Connected" while a dependency fails.
4. **Discovery endpoints:** GA4 properties, GBP accounts/locations, Claude models (all real API calls, read-only), with selection stored in the integration settings.
5. **Claude:** completion-level test, model list, usage summary; competition falls back to single-AI cleanly.
6. **System tests:** deeper DB and Redis/Celery checks; SMTP test + explicit test email; Google Sign-in verification; deployment-managed lock in the UI.
7. **UI:** field provenance, unsaved/saved state, dependency panel, property/location/model pickers, SMTP form, deployment lock, role restrictions.
8. **Security/audit:** secrets encrypted with `TOKEN_ENCRYPTION_KEY` (no fallback key for saving; startup banner when unsafe), masked in all responses, audit
   entries for every change (field names only, never values), Super-Admin-only mutation endpoints, tests for leakage and permissions.
9. **Safety:** untouched — `GOOGLE_ADS_WRITES_ENABLED=false`, `AUTO_NEGATIVES_MODE=shadow`, `EMERGENCY_BRAKE_WRITE_MODE=simulate`; no campaign calls in this work.

### Google documentation points *(docs search; re-verify on the live pages)*
* Google Ads: the developer-token sunset took effect 2026-09-09; access levels now belong to the **Cloud project**: Test (test accounts only) → Explorer
  (test + production, 2,880 production operations/day, automated review) → Basic (15,000/day) → Standard (no cap, review). The Google Ads API must be enabled in the
  project (that grants Test access). Keyword Planner services (`KeywordPlanIdeaService`, `KeywordPlanService`) need Basic or Standard — Explorer is not enough.
  Campaign writes need production access (Explorer or above) plus the app's own two-key authorisation; reporting needs Explorer or above for production accounts.
  A known migration issue returns `CLOUD_PROJECT_NOT_APPROVED_FOR_PRODUCTION` when an approved token is used with a project it was never used with before the sunset.
* GA4: enable **both** Google Analytics Admin API (`analyticsadmin.googleapis.com`) and Analytics Data API (`analyticsdata.googleapis.com`); scope `analytics.readonly`;
  the user needs Viewer on the property.
* Business Profile: quota 0 = access not granted; apply for Basic API access (website domain must match the email domain) — a quota increase is not the fix.
* Places API (New): enable the API and billing; restrict the key to *Places API (New)*; every request needs `X-Goog-FieldMask`.
* Anthropic: `GET /v1/models` lists available models (lifecycle active/deprecated); usage is returned per Messages response, not by the models endpoint.
