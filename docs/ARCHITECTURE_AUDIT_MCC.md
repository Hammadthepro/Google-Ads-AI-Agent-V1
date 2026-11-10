# Audit: Supabase / Redis / Celery and MCC account management (2026-11-10)

## A. Database (Supabase vs local PostgreSQL)
* The app has **one** database connection: `DATABASE_URL` (SQLAlchemy). No module talks to Supabase through its own SDK/REST API and no
  module has a second connection. "Supabase" vs "local PostgreSQL" is purely which URL `DATABASE_URL` points at (local default, Docker
  Compose `postgres` service, or the Supabase Session Pooler with `sslmode=require`). Alembic runs on the same URL at start-up.
* Inconsistency found: the Supabase project (`extfrctastbodivhiiei`, Postgres 17) is empty and at Alembic **0003**; the local dev database is at **0005**.
  Supabase therefore lacks `integration_configs`, `audit_reports`, client-profile fields (0004, 0005). Migration 0006 (this change) is applied
  to Supabase together with 0004–0005 so both match. The app on this machine still points at local PostgreSQL; production must set
  `DATABASE_URL` to the Supabase pooler URL (a secret that is not available here).
* All tables have RLS enabled with no policies (the backend connects as owner), so the Supabase Data API cannot read them.

## B. Redis and Celery
| Question | Finding |
|---|---|
| Is Redis used by Celery? | Yes: `Celery(broker=REDIS_URL, backend=None)`; results are not stored (`task_ignore_result`). |
| Jobs registered | `gaa.run_task` (user-started tasks, dispatched from `POST /tasks/{id}/run`) and 8 scheduled jobs: daily_sync 06:17, recheck_tracking 06:47, optimize_all 07:13, hourly_brake :23, recheck_fixes 08:05, approvals_sweep :41, weekly_reports Mon 09:11, evaluate_predictions 10:03 (UTC). |
| Other Redis use | Fixed-window rate limiter (`INCR`+`EXPIRE` per IP per minute; in-memory fallback if Redis is down). `/readyz` reports it. |
| Business data in Redis? | **No.** Only broker messages (task ids) and short-lived rate-limit counters. Nothing is persisted that is not rebuildable. |
| Unused / duplicated dependencies | None: `celery` and `redis` are both imported at runtime; no second queue or cache library. |
| Recommendation | **Keep.** With 18+ client accounts the daily sync, hourly brake and weekly reports must run on a schedule outside the web process, and audits/campaign tasks run for minutes (crawl + AI + Google calls) so they must not block HTTP requests. Redis is the broker only; Supabase stays the single source of truth. |

## C. Google Ads accounts: current implementation
* One OAuth connection (`integration_configs`, encrypted refresh token) shared by the workspace. The Google Ads card required **two IDs**:
  `login_customer_id` (manager) and `customer_id` (one client) — from `.env` or the card; the connection could only be "Connected" after
  reading that single client. `GOOGLE_ADS_CUSTOMER_ID` is read by the connection test and Keyword Planner.
* `accounts_service.discover()` used `ListAccessibleCustomers` only (direct access, no hierarchy, no names/status).
* `RealGoogleAdsAdapter` builds ONE client with ONE `login-customer-id`; accounts under different managers could not be read in one process.
* No workspace/team concept; `google_ads_accounts.customer_id` is globally unique; `user_client_assignments` links users to `clients` (product clients).

## D. Required changes
1. Migration **0006** (additive): `workspaces`, manager selection on the workspace, hierarchy/status columns on `google_ads_accounts`,
   `user_account_selections`.
2. Adapter: `list_manager_candidates()`, `list_customer_clients(manager)` (`customer_client` query, walks sub-managers, pagination, per-branch error capture), per-account login-customer-id.
3. Service `mcc_service`: choose manager, discover/refresh (upsert, never delete, mark not-seen), search/filter, select/switch client.
4. Retire `GOOGLE_ADS_CUSTOMER_ID` (and the card's client ID field). Keep `GOOGLE_ADS_LOGIN_CUSTOMER_ID` only as an optional default manager.
5. API + UI: manager picker on the Google Ads card; account selector page (search, status filter, refresh, select, open workspace).
6. Tests (adapter, discovery, pagination, closed accounts, selection, persistence, expired/missing OAuth, Celery/Redis).

## E. Future team assignments (design only)
`workspaces` own accounts (`google_ads_accounts.workspace_id`). Later: `user_workspace_members` + `user_account_assignments(user_id, account_id)`;
every account query already goes through `mcc_service.accessible_accounts(db, user, workspace)`, the single place where the restriction will be applied.
