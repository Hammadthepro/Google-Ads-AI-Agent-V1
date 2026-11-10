# Database Schema — Entity Plan

Status: **Implemented** — `backend/app/models/*.py`, migrations `backend/migrations/versions/0001_initial_schema.py` and `0002_row_level_security.py`. 50 application tables + `alembic_version`; RLS enabled on all (Supabase Data API denied). Applied to Supabase project `google-ads-ai-agent-v1`.

Differences from the plan: the simulated Google Ads entities are stored in one generic `mock_gads_resources` table; `job_runs` and `alerts` record background jobs and notifications; `user_client_assignments` handles row-level client scoping.

## Conventions (every table unless noted)

| Column | Purpose |
|---|---|
| `id` UUID PK | |
| `created_at`, `updated_at` (timestamptz, UTC) | |
| `created_by_id` → users | Ownership |
| `client_id` → clients | On every client-scoped table (row-level authorization) |
| `integration_mode` enum `MOCK/REAL` | Which adapter produced/acted |
| `source_type` enum `MOCK/LIVE/MIXED` | Data provenance |
| `status` enum | Per-entity lifecycle |
| `external_resource_name` | e.g. `customers/123/campaigns/456` |

Money stored as integer **micros** (as Google Ads does) plus `currency_code`. No secrets in any normal column.

## Identity & access
- **users** — email (unique), name, google_sub (nullable until Google login), role, is_active, last_login_at, is_bootstrap_admin.
- **sessions** — user_id, session_hash, csrf_token_hash, expires_at, ip, user_agent, revoked_at.
- **user_client_assignments** — user_id, client_id (Admin "assigned users"; Campaign Manager/Analyst scope).

## Clients & businesses
- **clients** — name, owner_user_id, backup_owner_user_id, tag_access_contact (name/email/phone), kill_switch_on, override_active.
- **businesses** — client_id, name, business_type, website_url, gbp_url, phone, address, lead_form_url, thank_you_url, default_radius_miles, zip_codes[], business_hours (JSON), verified_claims[], brand_rules[], competitor_ads_text.
- **business_services** — business_id, name, landing_page_url, is_excluded.
- **competitors** — business_id, name, website_url, place_id (max 3 per business, enforced in service + check).

## Google Ads mirror (our records of external resources)
- **google_ads_accounts** — client_id, customer_id, login_customer_id, descriptive_name, currency, time_zone, is_manager, parent_account_id, permission_status, writes_allowed (bool, default false), kill_switch_on, auto_tagging_enabled, last_synced_at.
- **integration_connections** — provider, account ref, status, scopes, encrypted_refresh_token (ciphertext only), token_fingerprint, connected_by_id, last_test_at, last_error.
- **campaigns** — business_id, google_ads_account_id, name, goal (LEADS), campaign_type (SEARCH; others read-only), status, monthly_budget_micros, weekly_conversion_target, bidding_strategy, network_settings (JSON), geo_target_type, landing_page_url, services_covered[], is_read_only.
- **campaign_locations** — campaign_id, geo_target_constant, radius, zip, negative.
- **ad_groups** — campaign_id, name, service, landing_page_url, status.
- **ads** — ad_group_id, type (RSA), headlines (JSON ≤15), descriptions (JSON ≤4), path1, path2, final_url, status, replaced_by_ad_id.
- **keywords** — ad_group_id, text, match_type (EXACT/PHRASE/BROAD), status, label (SERVICE/GENERIC/IRRELEVANT), avg_monthly_searches, low/high_bid_micros, competition.
- **negative_keywords** — level (CAMPAIGN/AD_GROUP/SHARED_LIST), campaign_id/ad_group_id/shared_list_id, text, match_type, origin (MANUAL/AUTO/PROPOSED), applied_operation_id.
- **shared_negative_lists** — google_ads_account_id, name.
- **assets** — type (CALL/SITELINK/CALLOUT/SNIPPET), payload JSON, linked level.
- **conversion_actions** — google_ads_account_id, name, type, origin, category, primary_for_goal, counting_type (ONE/MANY), call_min_duration_seconds, status, is_duplicate_suspect.

## Tracking, audits, research
- **tracking_gate_results** — business_id, account_id, state (GREEN/AMBER/RED), checks JSON (each check: pass/fail/unknown + evidence), fix_list JSON, evaluated_at, next_check_at.
- **tracking_overrides** — business_id, admin_user_id, reason (required), created_at, cleared_at, cleared_reason.
- **audits** — business_id, task_id, type (WEBSITE/GBP/COMPETITOR/KEYWORD/ACCOUNT), status, started/finished_at, result JSON, summary, source_type.
- **crawl_pages** — audit_id, url, status_code, title, meta_description, h1–h3, phones, forms, schema JSON, tags_detected (GTAG/GTM ids), robots_allowed, depth.
- **keyword_research_items** — audit_id, keyword, cluster, proposed_ad_group, label, match_type, volume, bids, competition, intent, overlap_flags.
- **feasibility_results** — campaign or task, inputs JSON, scenarios JSON (conservative/expected/optimistic), daily/weekly budget, est_clicks, est_conversions, est_cpl, required_cvr, budget_gap, status (FEASIBLE/PARTIALLY_FEASIBLE/UNREALISTIC), bottlenecks JSON. All flagged `is_estimate=true`.
- **fix_tracker_items** — client_id, business_id, issue, category, priority, owner, status, evidence, source (audit/gate/feasibility), created_at, resolved_at, auto_recheck_type.

## Tasks, AI and planning
- **tasks** — client_id, business_id, task_type, target ids, fields JSON, human_plan, note, status, current_step, error.
- **ai_runs** — task_id, purpose (DRAFT/CHALLENGE/EXTRACT/LABEL/REPORT/EXPLAIN), provider, model, prompt_template_id, prompt_version, input_hash, raw_output, parsed_output, validation_ok, error, latency_ms, tokens_in/out, attempt.
- **campaign_plans** — task_id, version, draft JSON, challenger_objections JSON, human_plan_comparison JSON (agreed / AI-only / human-only / disagreements), resolution JSON, playbook_rule_ids[].

## Validation, approvals, execution
- **validation_runs** — plan_id or proposal_id, payload_hash, results JSON (errors/warnings per rule), passed, adapter_validate_only_result.
- **approvals** — type (CREATE/ENABLE/UPDATE/PAUSE/BUDGET/NEGATIVES/EXPERIMENT_START/EXPERIMENT_WINNER/CONVERSION_ACTION), subject ids, payload JSON, payload_version, payload_hash (SHA-256 of canonical JSON), current_state JSON, proposed_state JSON, reason, expected_effect, risks, validation_run_id, ai_explanation, source_data_refs, requested_by_id, status, decided_by_id, decided_at, decision_reason, expires_at, escalated_to_id, superseded_by_id.
- **approval_events** — append-only history (created, viewed, edited→new version, approved, rejected, changes_requested, invalidated, expired, escalated).
- **operations** — approval_id (nullable only for auto-negatives with policy ref), idempotency_key (unique), adapter_name, integration_mode, operation_type, request_payload, state (PENDING/SENT/SUCCEEDED/FAILED/UNKNOWN/RECONCILED), external_response (redacted), external_resource_names[], verification_passed, verification_diff, attempts, reconciled_at.

## Monitoring, emergency, reporting
- **performance_snapshots** — date, level (ACCOUNT/CAMPAIGN/AD_GROUP/KEYWORD/AD/ASSET), entity ref, impressions, clicks, cost_micros, conversions, conversions_value, calls, search_IS, budget_lost_IS, rank_lost_IS, fetched_at, source_type. Unique (level, entity, date, source_type) — upsert on 30-day refresh.
- **search_terms** — date range, campaign/ad_group, term, matched_keyword, clicks, cost_micros, conversions, last_conversion_date.
- **call_records** — date, campaign, duration_seconds, answered/missed (no caller number).
- **change_events** — account, resource, change type, user_email, changed_at, made_outside_app.
- **emergency_rules / emergency_events / alerts** — rule config, trigger values, simulated/real action, notification status.
- **kill_switches** — scope (GLOBAL/CLIENT/ACCOUNT), scope_id, is_on, set_by_id, reason.
- **recommendations** — source (RULES/AI/BOTH), category, entity, proposal JSON, enough_data_result JSON, decision (GO/HOLD), hold_reason, approval_id.
- **weekly_reports** — client_id, week_start, metrics JSON (computed), diagnosis JSON, ai_summary, source_type.
- **experiments** — campaign_id, variable, hypothesis, arms JSON, split (50/50), status, started/ended_at, results JSON, winner_selected_by_id, winner_arm.

## Learning & settings
- **playbook_rules** — text, scope (global/business type/client), is_active, author_id, version.
- **predictions** — subject (feasibility/change), predicted JSON, actual JSON, evaluated_at.
- **regression_cases** — name, inputs JSON, expected constraints JSON, last_run_result.
- **system_settings** — key, value JSON, updated_by_id (thresholds, real_writes_authorized, shadow mode).
- **activity_logs** — actor_id, action, entity, entity_id, before/after JSON (redacted), ip, request_id.

## Mock-world tables (simulated external services, isolated)
`mock_gads_customers`, `mock_gads_campaigns`, `mock_gads_budgets`, `mock_gads_ad_groups`, `mock_gads_criteria`, `mock_gads_ads`, `mock_gads_assets`, `mock_gads_conversion_actions`, `mock_gads_metrics_daily`, `mock_gads_search_terms`, `mock_gads_change_events`, `mock_gads_experiments`, `mock_places`, `mock_pagespeed`, `mock_failure_injections`, `mock_clock`.


## Migration 0006 — workspaces and MCC accounts
* `workspaces(id, name, slug UNIQUE, google_ads_manager_customer_id, google_ads_manager_name, accounts_discovered_at, discovery_status, discovery_message)` — one default workspace today.
* `user_account_selections(id, user_id→users, workspace_id→workspaces, customer_id, UNIQUE(user_id, workspace_id))` — the account a user is working in.
* `google_ads_accounts` + `workspace_id→workspaces`, `status`, `level`, `is_test_account`, `is_hidden`, `discovery_state` (ACTIVE/NOT_SEEN), `discovery_error`, `last_discovered_at`; existing `login_customer_id` is the manager an account is reached through and `parent_customer_id` its direct parent.
* Customer IDs are stored as 10 digits (API format). RLS is enabled on both new tables. Additive only; nothing is dropped or rewritten.
