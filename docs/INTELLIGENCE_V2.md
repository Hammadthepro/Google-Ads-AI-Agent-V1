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
