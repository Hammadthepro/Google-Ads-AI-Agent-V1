# Intelligence V2 — what is implemented (and what is not)

## Implemented and tested
- **Intake**: Google Business Profile URL (required when "Yes", cleared on "No"), optional weekly conversion target, monthly budget no longer asked (AI-recommended), competitor name + website mandatory (up to 10), legacy records load and are completed on next save.
- **Preparation without an account or the Google Ads integration**: research, strategy, draft, local validation. Deployment is refused at execution (`execution.execute`) unless the payload names a customer that matches the approval's linked account.
- **Business Profile API removed** (provider, scope, tester, card, deps, tests). The profile *link* remains a research input.
- **Keyword Planner** runs under `research_customer_id` (Integrations > Keyword Planner), never a client account; missing prerequisite is reported exactly; absent metrics are `None`, with source, time, location, language, network.
- **Financial engine** (`services/finance.py`, `POST /finance/analyse`): every specified formula, 3 scenarios, affordability, sufficiency, sensitivity, input provenance. Unknown stays unknown.
- **Ten-module intelligence record** (`services/intelligence_service.py`, migration 0008, `GET/POST /clients/{id}/intelligence[/refresh]`): ~200 specified checks, each evaluated or `not_assessed`; findings carry origin/verification/source/time/confidence; versioned history; reuse of unchanged modules; selective invalidation by input hash; single-stage re-run with dependents; readiness decision (Ready / Ready with conditions / Not ready / Not commercially viable).

## Not implemented / limited
- No single conversational agent workspace: the record is surfaced as one panel on the client page, not a chat agent.
- Not assessed (no data source): SEMrush, SERP observation, Auction Insights, GA4/GTM audit, offline conversions (Data Manager API), consent, CRM, location-level Keyword Planner metrics, forecasts, policy pre-checks, account billing/policy history (needs a linked account and the read-only account audit).
- Confidence is categorical (by how a finding was obtained), not statistical.
- Monitoring/optimisation do not yet read the intelligence record.
- A draft prepared without an account has no customer; after linking, prepare the task again for an approval bound to the account.
- A leftover encrypted `gbp` row in `integration_configs` is ignored, not deleted.

---

# Agent workspace and monitoring (phase 2)

## Conversational agent (`/agent`, `services/agent_service.py`, migration 0009)
- One persistent thread per user and client. It owns no knowledge: it reads the intelligence record, the audit/feasibility tables and the synced Google Ads data.
- **Understanding**: rules first (labelled `rules`); the AI is asked only for ties or no match (labelled `ai`, schema-validated, restricted to registered intents); otherwise the agent asks, with option buttons. Client/business are asked only when they cannot be inferred and are remembered.
- **Planning**: every request becomes stages, each `RUN` / `REUSE` / `SKIP` with a reason (research current? client info changed? research older than 30 days? stage inputs changed? synced data older than 24 h? no account / integration?). Valid research and data are never repeated.
- **Execution**: stages are saved as they finish. A failed or interrupted run (worker restart, no heartbeat for 15 min) shows as resumable and continues from the first unfinished stage.
- **Answers**: deterministic, from the record: headline, readiness, blockers (with the exact missing prerequisite), warnings, per-stage findings with source, retrieval time, verification, origin and confidence, tables/numbers with estimate labels, reused stages.
- **Consequential actions**: the agent only reads and researches. Creating a draft task, or requesting approval for a recommendation, needs an explicit confirmation by a user with the permission, and creates only a task / approval REQUEST. Writes stay disabled; execution stays behind the approval, write guard and kill switches.
- Intents: audit, keyword research, recalculate budget, prepare strategy, status, create draft (confirm), diagnose, performance, search terms, recommendations, weekly report, request approval (confirm).

## Monitoring and optimisation (`services/performance_insights.py`)
- **Bound to the record**: baselines come from it (weekly target, recommended budget, maximum affordable CPC, tracking readiness, selected keywords and research negatives, research freshness). Synced account data feeds the account/keyword/finance stages, so those stages become stale and refresh when new data arrives.
- **Anomalies**: last 7 days vs the 28 before (needs 4 and 14 days, otherwise "insufficient history" and nothing is concluded). Spend, CPC, CTR, conversions, cost per lead, not serving, budget-limited, tracking-suspect. Each flag lists what corroborates it; one metric alone is "a signal". Causes are labelled hypotheses with evidence and confidence.
- **Pacing / shortfall**: month-to-date vs pro-rata budget with a labelled projection; last week vs target with a labelled cost-to-close estimate.
- **Search terms**: irrelevant (research negatives, rules), relevant-but-not-converting, converting-but-not-keywords; a term that ever converted is never offered for exclusion.
- **Ads and landing pages**: weak ads, and final-URL conversion rates (derived from ad-level data; stated as such).
- **Recommendations** (existing table): impact estimates with low/expected/high from the account's measured CPC and conversion rate, enough-data gate (held while tracking is not GREEN), approval requirement, and whether they are executable (budget, keyword, negative: via approval) or advice (a person acts).
- **Outcomes**: before/after for changes live 8+ days, labelled correlational.
- **Weekly report** now carries insights, optimisation, outcomes, outside-app changes and the intelligence it was judged against. A scheduled `insights_all` job (07:37) keeps the record and recommendations current; it never calls Google Ads.

## Not done / limits
- No automatic execution of any recommendation; advice kinds (pacing, landing page, bid ceiling, tracking check, shortfall, negative review) are never executable.
- Landing-page conversion rate is not reported by Google at that level here; asset, device and policy-status data are not synced.
- Impact numbers are estimates; before/after is not causal proof.
- The conversational AI is used only for tie-breaking intent; answers are not free-form generation.
- Verified in a real browser (headless Chromium against a scratch copy of the seeded test database with test doubles). No live Google Ads calls were made.

---

# Conversion tracking gate (evidence-based states)

| State | Meaning | Stops preparation? | Live deployment |
|---|---|---|---|
| GREEN | verified: nothing failed or unknown, leads recorded in Google Ads | no | allowed |
| YELLOW | exists, configuration or verification incomplete | no (warning + list of what to verify) | needs GREEN or an admin authorisation |
| RED | evidence CONFIRMS it is broken/misconfigured (a check that could be read failed) | yes | blocked |
| UNKNOWN | cannot be assessed: no linked account, integration not configured, account unreadable, never assessed, or assessed before the current account link | no (warning) | blocked unless an admin authorises |

- Missing evidence is never a negative finding: no account makes the account checks, the conversion-action checks and the tag check "unknown" (the AW- tag comes from the account's conversion action, so its absence is only a failure once an account exists). Recorded conversions prove the tag works whatever the crawl saw; Tag Manager without an AW- tag is a warning, not a failure.
- Stored results are only trusted while current: rows from the old rules (no `assessable` flags) or taken under a different account link read as UNKNOWN ("reassess"), so a cached RED cannot leak. Linking or unlinking an account reassesses tracking at once.
- The admin override (any state but GREEN, written reason, audited) is the explicit authorisation for deployment; it behaves like YELLOW and never enables conversion-based automation.
- Deployment gate: `validation.validate(deployment=True)` on execution and a defence-in-depth check in `safety.check`. Account binding, approval, write protection and kill switches are unchanged.
- Behaviour change to note: a new advertiser with no recorded leads is YELLOW and can no longer be deployed on an approval alone; it needs the audited admin authorisation until tracking is verified.
