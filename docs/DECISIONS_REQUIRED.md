# Decisions Required & Risk Register

Status: **Defaults applied as configurable test values** (you authorised using the recommended defaults). Change them anytime in System Settings; none is final production policy.

Previous status: Open. Each decision has a recommended default. Phases that depend on a decision will use the recommended default as a *configurable test value* until you confirm — it will never be treated as final production policy.

## 1. Decisions

| ID | Decision | Recommended default | Needed by |
|---|---|---|---|
| D-01 | Retired. The tracking gate and its override were removed (migration 0010); measurement is an audit with findings | n/a | n/a |
| D-02 | When is conversion volume sufficient for conversion-based bidding? | ≥ 3 primary lead conversions recorded in the last 30 days (`measurement.min_conversions_30d`, configurable) | Phase 6 |
| D-03 | Minimum call length for call conversions | 60 seconds default; per business type in playbook | Phase 6 |
| D-04 | Approval timing: reminder, expiry, escalation | Reminder 24h, expire 72h, escalate to client backup owner | Phase 10 |
| D-05 | Competitors left blank — what happens? | Proceed, mark competitor audit "skipped — no competitors provided"; suggest top 3 from mock/Places as *candidates* for human confirmation | Phase 7 |
| D-06 | Competitor GBP posts / Q&A / services (no API) | Skip in V1, mark "not available via API" | Phase 7 |
| D-07 | May a user approve their own request? | No for CREATE/ENABLE/BUDGET; Super Admin can configure | Phase 10 |
| D-08 | Budget increase threshold requiring Super Admin | > 20% or > $500/month increase (test value) | Phase 10 |
| D-09 | Real emergency-brake write policy | Disabled in V1; only freeze-automation + alert. Separate policy later | Phase 12 |
| D-10 | Alert channel | Email (mock notifier in V1); Slack optional later | Phase 12 |
| D-11 | Combined "create paused + enable after verification" single approval card? | No — two approvals in V1 (Master Prompt). Revisit after shadow period | Phase 11 |
| D-12 | Above-the-fold mobile check method | Deterministic Playwright DOM check; Groq vision only if a supported model is configured | Phase 7 |
| D-13 | Feasibility default conversion-rate assumptions by business type (new accounts) | Configurable table; conservative/expected/optimistic e.g. 3% / 6% / 10% as **test values** | Phase 8 |
| D-14 | Broad match test ad group in V1? | Not in V1 builds; allowed only as an approved experiment with reliable conversion data | Phase 9 |
| D-15 | Review-adjusted forecast size | Reduce expected CVR by up to 20% when rating < competitor median or reviews < 25% of median (test value) | Phase 8 |
| D-16 | Wasted-spend window | Last 30 days, clicks ≥ 10 and 0 conversions (test value) | Phase 12 |
| D-17 | Who can create playbook rules? | Super Admin + Admin | Phase 14 |
| D-18 | Enough-data thresholds | ≥ 100 clicks or spend ≥ 2× target CPL; conversion delay 7 days; 7 days since last change; not learning; conversions recorded and counted once (test values) | Phase 13 |
| D-19 | Emergency thresholds | Alert at today's spend > 1.5× avg daily budget before 18:00 account time; freeze at > 2× (Google can legitimately spend up to 2× daily budget on a single day) — test values | Phase 12 |
| D-20 | "Change target" task | Internal weekly conversion target only — no Google Ads write | Phase 5 |
| D-21 | Shadow mode exit criteria for auto-negatives | ≥ 4 weeks shadow, ≥ 95% of proposals approved by humans, written authorization | Phase 13 |
| D-22 | Allowed Google login domains | Restrict to the agency's Workspace domain(s) | Phase 2 |
| D-23 | Hosting target for production | Single VPS with Docker Compose + managed Postgres backups | Phase 16 |
| D-24 | Default location radius | 20 miles (from PDF) | Phase 4 |
| D-25 | Data retention for logs and AI runs | Keep 24 months; raw AI outputs 12 months | Phase 14 |
| D-26 | Ad schedule from business hours, or 24/7 with call assets scheduled? | Ads 24/7 (forms work anytime); call assets limited to business hours | Phase 9 |

## 2. Risks

| ID | Risk | Impact | Mitigation |
|---|---|---|---|
| R-01 | Container is ephemeral; no git remote attached | Loss of all work | Attach a GitHub repo before Phase 1; push at each phase end |
| R-02 | Google Cloud project API access (Explorer/Basic/Standard) may take time or be limited | Delays real integration | Mock-first; start the application early once MCC exists |
| R-03 | Normal Google Ads account has no test protections | Real spend from a bug | Two-key writes, account allowlist, paused-first, adapter-level guard |
| R-04 | Groq model availability/deprecation; structured-output support varies per model | AI failures | Configurable model, schema validation, MockAIProvider fallback for tests only (never silent in production) |
| R-05 | Groq rate limits on lower tiers | Slow/failed drafts | Queue AI calls in Celery, backoff, show status |
| R-06 | Prompt injection through crawled website text | Manipulated drafts | Content treated as data, strict schemas, validation + human approval |
| R-07 | SSRF via crawler URLs | Internal network exposure | DNS/IP checks on every hop, port allowlist, no redirects to private ranges |
| R-08 | Tag detection mistaken for working measurement | False confidence | A detected tag is reported as installation only; "recording" requires recorded conversions |
| R-09 | Keyword Planner / Places costs or quotas | Budget overrun | Field masks, caching, per-run caps |
| R-10 | Scope size (16 phases) | Schedule | Strict phase gates; MVP-critical path first |
| R-11 | Docker daemon unavailable in this cloud container | Can't run compose here | Run Postgres/Redis natively for tests here; Compose verified on your machine/VPS |
| R-12 | Mock metrics mistaken for performance | Wrong client decisions | MOCK badges, report headers, separate tables |
