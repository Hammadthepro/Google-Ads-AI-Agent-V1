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
- **Anomalies**: last 7 days vs the 28 before (needs 4 and 14 days, otherwise "insufficient history" and nothing is concluded). Spend, CPC, CTR, conversions, cost per lead, not serving, budget-limited, conversions-stopped. Each flag lists what corroborates it; one metric alone is "a signal". Causes are labelled hypotheses with evidence and confidence.
- **Pacing / shortfall**: month-to-date vs pro-rata budget with a labelled projection; last week vs target with a labelled cost-to-close estimate.
- **Search terms**: irrelevant (research negatives, rules), relevant-but-not-converting, converting-but-not-keywords; a term that ever converted is never offered for exclusion.
- **Ads and landing pages**: weak ads, and final-URL conversion rates (derived from ad-level data; stated as such).
- **Recommendations** (existing table): impact estimates with low/expected/high from the account's measured CPC and conversion rate, enough-data gate (held while conversion data is not reliable), approval requirement, and whether they are executable (budget, keyword, negative: via approval) or advice (a person acts).
- **Outcomes**: before/after for changes live 8+ days, labelled correlational.
- **Weekly report** now carries insights, optimisation, outcomes, outside-app changes and the intelligence it was judged against. A scheduled `insights_all` job (07:37) keeps the record and recommendations current; it never calls Google Ads.

## Not done / limits
- No automatic execution of any recommendation; advice kinds (pacing, landing page, bid ceiling, measurement check, shortfall, negative review) are never executable.
- Landing-page conversion rate is not reported by Google at that level here; asset, device and policy-status data are not synced.
- Impact numbers are estimates; before/after is not causal proof.
- The conversational AI is used only for tie-breaking intent; answers are not free-form generation.
- Verified in a real browser (headless Chromium against a scratch copy of the seeded test database with test doubles). No live Google Ads calls were made.

---

# Conversion measurement audit

Conversion measurement is analysed, not graded. There is no overall tracking status, no gate, no override and no workflow stage. `services/measurement.py` writes a `measurement_audits` row holding **findings** and **facts**; the same findings appear in the intelligence record (module `measurement`), the agent's answers, the weekly report and the client page.

Each finding states: what was inspected, the result (`ok`, `issue`, `attention`, `not_assessable`), the actual value, the evidence, severity, business impact, the recommended action, and whether that action is informational, needs approval, or needs a dependency (for example a linked account).

What is inspected: whether conversion actions exist and are enabled, primary vs secondary, counting (one vs every), duplicate sources for the same lead, conversion values, attribution model and lookback, enhanced conversions, auto-tagging, the tag on the site, whether conversions are actually being recorded, signs they stopped, call-length settings, and whether volume is sufficient for conversion-based bidding.

Rules that follow from it:
- Missing evidence is `not_assessable`, never an issue. No linked account means the account-side checks cannot be read; nothing is inferred.
- A stored audit is only used while current. One taken under a different account link, or in the pre-0010 format, is treated as absent and re-run. Linking or unlinking an account re-audits at once.
- Nothing here stops research, preparation, approval or deployment. Deployment is still controlled by account binding, exact-version approval, write protection, kill switches and read-back verification.
- Two decisions read the facts directly: bidding (`supports_conversion_bidding`: conversion-based bidding only when conversions are recorded; Maximize Clicks with a CPC cap when they are confirmed absent; a validation warning when unverified) and conversion-based automation (`conversion_data_reliable`: auto-negatives, broad-match experiments and the enough-data check need conversions that are recorded and counted once).
- API: `GET /businesses/{id}/measurement`, `POST /businesses/{id}/measurement/run`. Daily job: `audit_measurement`.
