# Known Issues & Limitations

Honest list of what is NOT done, NOT verified, or deliberately limited in V1. Nothing below is hidden behind a fake success.

## Not verified against live services (no credentials were available)
| Area | State |
|---|---|
| `RealGoogleAdsAdapter` (official `google-ads` library) | Implemented (GAQL reads, atomic `GoogleAdsService.Mutate`, `validate_only`, error mapping, in-adapter write lock). Unit-tested with fakes only. Field mappings (e.g. `contains_eu_political_advertising`, asset field names) must be confirmed against the API version in use. |
| `GoogleKeywordPlannerAdapter` | Implemented, not run live. ZIP → location lookup (`GeoTargetConstantService.SuggestGeoTargetConstants`, postal codes preferred) implemented and tested with a fake client. |
| Real experiments (still open) | `ExperimentService` / `ExperimentArmService` are **not implemented** in the real adapter (raises a clear error). Experiments work end-to-end in mock mode only. |
| `RealGooglePlacesAdapter`, `RealPageSpeedAdapter` | Implemented; tested with fake HTTP transports only. |
| `GroqAIProvider` | Implemented with the official SDK (JSON-object mode + strict Pydantic validation). The live Groq test is **SKIPPED** (no `GROQ_API_KEY`/`GROQ_MODEL` in this environment). Tested with a fake client. |
| Google sign-in (OIDC) | Backend verification implemented and unit-tested with signed-claim fakes; not exercised with a real Google client ID. |
| Google Ads OAuth | Authorization URL (PKCE, offline access), code exchange, encrypted storage, revoke — mock flow tested; real flow not exercised. |
| Live website crawling | Implemented with SSRF protection; tests use mock transports. Playwright rendering is optional (`pip install ".[browser]"`) and not included in the Docker image. |

## Supabase
- The schema (migrations 0001–0003, RLS on all 51 tables) was applied to project `google-ads-ai-agent-v1` through the Supabase connector and verified there.
- The application's **runtime connection** to Supabase could **not** be tested from the build environment: the sandbox only allows outbound HTTPS, and the pooler host does not resolve there. Verify from your machine/server (see DEPLOYMENT_GUIDE.md §3).
- The database password was shared in chat. **Reset it in Supabase** (Project Settings → Database) and update your local `.env` / server secret.
- Supabase's advisor reports "RLS enabled, no policy" (INFO) for every table. This is intentional: the public Data API must not read app data; the backend connects as the table owner.

## Codespaces / dev container
- **Fixed:** Codespaces fell into recovery mode because `ghcr.io/devcontainers/features/docker-in-docker` (on `devcontainers/python:1-3.12-bookworm`) ran `apt-get update` against a Yarn APT source with a rotated key (`NO_PUBKEY 62D54FD4003F6525`). The dev container now uses `mcr.microsoft.com/devcontainers/universal:6-noble`, which bundles Docker Engine, CLI and Compose, with **no features**, so no apt step runs during creation.
- I could not create a GitHub Codespace from the build environment. The `Dev container (Codespaces config)` GitHub Actions workflow builds the same `devcontainer.json` with the Dev Containers CLI and runs `scripts/start.sh` plus end-to-end checks. See TEST_RESULTS.md for its result.

## Docker
- The images could not be built inside the development sandbox (Docker Hub blocked there), but the **GitHub Actions `docker` job builds them successfully** (`docker compose build`) on every push. A full `docker compose up` in Codespaces has not been observed by me; the same start sequence was rehearsed natively (TEST_RESULTS.md).

## Functional limitations (by design or deferred)
- **Location changes** add new targets only. Removing old location criteria would be a delete, which the app never performs; the approval card lists the existing targets to remove manually in Google Ads.
- ~~Keyword pause resource names~~ — fixed: the keyword mirror stores the adapter's resource name (migration 0003), which is the real `{adGroupId}~{criterionId}` form in real mode.
- **Mobile above-the-fold check** is a deterministic DOM heuristic (phone link, form, H1), not a rendered-screenshot analysis (decision D-12).
- **Emergency brake**: real Google Ads emergency writes are intentionally not executed. Pause/lower-budget actions are recorded as SIMULATED; freezing automation is applied internally.
- **Auto-negatives** run in SHADOW mode. ACTIVE mode needs both `AUTO_NEGATIVES_MODE=active` and the `negatives.mode` setting, plus GREEN tracking and the enough-data gate. ACTIVE mode was tested only against the mock.
- **Thresholds** (enough-data, emergency, budget approval, wasted spend, tracking GREEN criteria) are documented TEST VALUES from DECISIONS_REQUIRED.md, editable in System Settings. They are not final policy.
- **Notifications** are stored as alerts (MOCK_DELIVERED). Real email needs `NOTIFIER_MODE=real` and SMTP variables.
- **Mock simulator** is deterministic but simplified: conversion lag curve, calls, impression share jitter and experiment effects are modelled, not realistic forecasts.
- **Mock validate-only** applies a subset of Google editorial policy (length, punctuation, capitalisation, phone numbers in text). Real policy review can still disapprove ads.
- **Rate limiting** uses Redis when reachable, otherwise per-process memory.
- **Live preview** in the Claude Code session exists only while the session runs. Use Docker Compose for a persistent install.
- Web font (Inter) loads from Google Fonts. Where blocked, the UI falls back to the system font.
- Frontend tested in Chromium (Playwright) only; no i18n.

## Fixed during development (found by tests)
Duplicate simulated search terms; Decimal aggregates; validation of status-only ad updates; integer conversion truncation; SSRF IP literals; per-campaign tracking-outage detection; anchor-based reconciliation; failure-injection counter rollback; login rate-limit scope; JSON step-status persistence; duplicate campaign names; competitor brand headings parsed as services; checkbox accessibility.


## V2 additions — status and limits (honest list)
- **No real provider has been contacted from the development sandbox** (network policy blocks googleapis.com, Groq and Anthropic). Connection tests, OAuth and the Claude provider are verified only against fake HTTP transports. A card shows *Connected* only after a genuine request succeeds in your environment.
- Google Business Profile needs Google's Business Profile API access approval for the Cloud project; the test reports the 403 honestly until then.
- Google Ads real reads/writes use the `google-ads` library; the connection test uses the REST API (v26). Real writes remain doubly gated (env key + Super Admin authorization) and only in *Validated execution* mode.
- GA4 is connection-tested only; no GA4 data is used in reports yet.
- Keyword Planner shares the Google Ads authorization; its card checks that connection rather than generating ideas.
- Claude requests use `fallbacks: "default"` (server-side refusal fallback, beta `server-side-fallback-2026-07-01`) for Opus 5.x models.
- Accounts page: multiple accounts and MCC discovery exist from V1; the per-account `access_level` column was added but is not yet editable in the UI.
- Playwright smoke covers the main flow and the Integrations page; the new plan editor and budget-decision screens are covered by API tests, not yet by browser tests.

## Client profile and audit report (V2.1)
- The audit's website, Business Profile and competitor evidence comes from the mock adapters until real integrations are connected; such findings are labelled "simulated data".
- Competitor discovery uses a Google Places text search (listings, not rankings); who actually advertises or ranks needs the Ads Transparency Center or a paid tool (not connected).
- Services marked "do not advertise" and per-service landing pages are edited on the Business page, not in the new-client form (the form keeps them intact on edit).
- Multi-business clients: the form edits the first business unless `business_id` is passed; the Business page still edits the others.

## Integrations dashboard (V2.2)
- **Google Ads access level is not readable through the API.** The dashboard shows what real responses prove (API enabled; Test-only if Google answers `CLOUD_PROJECT_NOT_APPROVED_FOR_PRODUCTION`; production account readable) and links to the Overview page in Cloud Console.
- Google Ads is **Connected only after the client account is read**; a manager-only test never makes it Connected. Without `GOOGLE_ADS_CUSTOMER_ID` it stays "Not tested".
- Google sign-in has no automatic test (it is verified by signing in). The crawler in fixture mode is never "Connected".
- Which Groq models work depends on the Groq plan; the dashboard lists the chat models your key can use and sends a real JSON-mode request to the primary and challenger.
- Credentials from environment variables win over `.env`; if `AI_PROVIDER` is set in the environment, change it there. Enabling Groq on the Integrations page activates it regardless.

## Real-integration transition (2026-11)
* **Google Ads data retrieval is BLOCKED for this project's Google Cloud project**: it only has Test access to the Google Ads API
  (`CLOUD_PROJECT_NOT_APPROVED_FOR_PRODUCTION`). OAuth, token refresh and the accessible-customer list work; reading client-account data
  (campaigns, metrics, search terms, Keyword Planner) needs Explorer access or higher. The real adapter is covered by contract tests with a
  fake client, but a live read of production data has **not** been verified.
* Not configured on the verified setup: Claude, Places, GA4, Business Profile, SMTP (live tests report NOT CONFIGURED).
* Lead Generation Agent, Outreach Agent, a separate Market Research Agent and a GA4 analytics view are not implemented (nothing was faked).
* Legacy `mock_gads_*` tables remain in the schema (no destructive migration); nothing reads them.
* PageSpeed without an API key can hit the keyless quota (live test reports BLOCKED).
