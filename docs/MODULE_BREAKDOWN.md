# Module Breakdown

Status: **Phase 0 — plan.** Each module lists its responsibility, key rules, dependencies and build phase.

| # | Module | Responsibility | Key deterministic rules | Depends on | Phase |
|---|---|---|---|---|---|
| 1 | `core` | Config, logging, errors, clock, provenance, security headers | Real mode never inferred from credentials | — | 1 |
| 2 | `db` | Engine, sessions, base mixins (timestamps, ownership, source_type) | All schema via Alembic | core | 1 |
| 3 | `users` | Users, roles, activation, first-admin bootstrap | Backend RBAC on every route | db, auth adapter | 2 |
| 4 | `activity` | Activity log + external operation log | Append-only; tokens redacted | db | 2 |
| 5 | `integrations` | Integration status, connection tests, OAuth connections | MOCK CONNECTED labelling | adapters | 3 |
| 6 | `mock_world` | Simulated Google Ads / Places / PageSpeed state, seeds, scenarios, failure injection | Seeded + reproducible | db, clock | 3 |
| 7 | `clients` | Client profile | Client may have many businesses | users | 4 |
| 8 | `businesses` | Business profile, services, competitors (≤3), claims, brand rules | Max 3 competitors | clients | 4 |
| 9 | `ads_accounts` | Linked Google Ads accounts, hierarchy, permission status | Login ≠ Ads authorization | Google Ads adapter | 4 |
| 10 | `tasks` | Single task form, 10 task types, workflow orchestration up to approval | No mutation during task run | most modules | 5 |
| 11 | `tracking_gate` | GREEN / AMBER / RED, fix list, admin override, daily recheck | Override = AMBER; no conversion-based automation | Google Ads, crawler | 6 |
| 12 | `audits` | Website, Business Profile, competitor audits (parallel) | Tag detected ≠ conversion verified; competitor ads never "live" without source | crawler, places, pagespeed, AI | 7 |
| 13 | `keywords` | Keyword research, labelling, clustering, overlap check, negative ideas | Exact + phrase default; no broad in main ad groups | keyword adapter, AI | 8 |
| 14 | `feasibility` | Budget math, 3 scenarios, bottlenecks, review-adjusted forecast | All numbers computed, labelled ESTIMATE | keywords, audits | 8 |
| 15 | `fixes` | Client fix list + fix tracker, auto-recheck | Status Open/In progress/Resolved with evidence | feasibility, audits | 8 |
| 16 | `planner` | AI draft, AI challenger, human-plan comparison, playbook injection | AI never overwrites human decisions | AI, learning | 9 |
| 17 | `validation` | Deterministic checks (+ adapter validate-only) | Pass ≠ approved | planner, crawler | 10 |
| 18 | `approvals` | Approval records, versioning, hash binding, expiry, escalation | Payload change → invalidation | validation | 10 |
| 19 | `safety` | WriteGuard, kill switches, payload hashing, policy registry | Enforced in backend + real adapter | approvals, settings | 10–11 |
| 20 | `execution` | Operation lifecycle, idempotency, paused-first create, read-back, reconcile, enable | Never blind-retry a create | safety, Google Ads | 11 |
| 21 | `monitoring` | Daily sync (30-day refresh), wasted spend, search terms, IS, manual changes | Metrics only from adapter data | Google Ads, clock | 12 |
| 22 | `emergency` | Hourly brake rules, kill switches, alerts | No AI; real writes disabled by policy | monitoring, notifier | 12 |
| 23 | `reporting` | Weekly review, diagnosis tree, rules∩AI causes | AI writes text, not numbers | monitoring, fixes | 13 |
| 24 | `optimization` | Enough-data gate, safe negatives, big-change proposals | All gate conditions must pass | monitoring, approvals | 13 |
| 25 | `experiments` | 50/50 experiment lifecycle | Human selects winner | approvals, execution | 13 |
| 26 | `learning` | Edit/rejection log, playbook rules, predictions vs actuals, regression cases | AI cannot edit production rules | planner, approvals | 14 |
| 27 | `settings` | Thresholds, policies, real-write authorization | Changes audited | users | 2 → ongoing |

## Task type → workflow mapping (from PDF step 1)

| Task | Requires | Workflow | Resources created/changed |
|---|---|---|---|
| Build a new campaign | Account (+ business) | Gate → 4a–4d → Feasibility → Draft+Challenger → Validate → Approve → Execute (paused) | Budget, campaign, locations, ad groups, keywords, negatives, ads, call asset |
| Add an ad group | Campaign | Gate → audits (service-focused) → Overlap → Feasibility (budget thin?) → Draft → Validate → Approve → Execute | Ad group, keywords, negatives, ads |
| Add an ad | Ad group | Audits (focused) → Draft → Validate → Approve → Execute | One RSA |
| Add keywords | Ad group | Keyword research → Overlap → Validate → Approve → Execute | Keywords |
| Edit an ad | Ad | Draft → Validate → Approve → Execute | New ad created paused; old ad paused after enable approval (RSAs are replaced, not edited in place) |
| Pause an ad | Ad | Approve → Execute | Ad status PAUSED |
| Change budget | Campaign | Validate → Approve → Execute | Campaign budget |
| Change target | Campaign | Validate → (Feasibility recompute) → Approve | Internal weekly target (no Google Ads write) |
| Change locations | Campaign | Validate → Approve → Execute | Campaign criteria |
| Audit only | Account | Gate → 90-day read-only account audit → Monitor + Weekly Review | Nothing |

Note: "Change target" (weekly conversion target) is an internal business setting; it changes feasibility and reporting, not Google Ads. Flagged as D-20 to confirm.
