> **Note:** the app is now real-integration-only (no mock mode, no demo data). Mentions of mock mode, seeding or demo users below are historical; see [REAL_DATA_ONLY.md](REAL_DATA_ONLY.md).

# Implementation Plan V2 — Product Vision "Google Search Ads AI Agent"

Scope: Google Ads **Search** campaigns only (lead generation). No Meta / Facebook / Instagram / Local Services Ads.

## 1. Gap analysis (state of V1 before this plan)
| Vision section | V1 state | Gap |
|---|---|---|
| 2 Intake & validation | Business profile form; tracking gate; audits | No completeness/consistency audit of the intake; no "clarification needed" stage |
| 3 Conversion analysis & budget recommendation | Feasibility computes budget needed for a target | No explicit recommended budget, and no team confirmation step |
| 4 Feasibility | FEASIBLE / STRETCH / UNREALISTIC with scenarios | Verdict wording (feasible / partially feasible / unrealistic) and adjustment recommendations need surfacing; acceptance step |
| 5 Strategy | AI draft, keyword research, negatives, RSAs, bidding, allocation | Broad match only via an approved test (kept as a safety policy, see §3) |
| 6 AI competition | One provider drafts; the *same* provider challenges | **Second independent LLM (Claude)**, identical inputs, cross-challenge, comparison, rule-based consolidation |
| 7 Review & approval | Approval card, raw JSON payload edit → new version | **Structured editor** (ad groups, keywords, match types, negatives, ads, budget, bidding), plan versions, original vs final diff |
| 8 Launch | Paused-first, read-back, reconciliation, separate enable approval | Done; conversion-tracking re-check at launch (validation already does it) |
| 9 Monitoring | Daily sync, wasted spend, search terms, recommendations | Done (mock); budget utilisation % to add |
| 10 Weekly targets | Weekly target per campaign | **Client-level weekly target** + report against it |
| 11 A/B testing | Experiments (mock), human picks winner | Done (mock). Real ExperimentService still open |
| 12 Learning loop | Playbook, regression, predictions | Done |
| 13 Integrations | Env-var configuration only; mock-oriented status screen | **Credential store (encrypted, UI-managed), OAuth with stored client, per-integration cards, real Test Connection with step diagnostics, GA4, GBP, Groq, Claude, PageSpeed, Places, health dashboard, operating modes Mock / Read-only / Validated execution, multi-account management, audit log** |
| UI quality | Functional but generic | Redesign; every control wired to the backend |

## 2. Implementation order (dependencies)
1. **Integration platform** (credential store → modes → registry → OAuth → connection tests → health). Every real-data feature depends on it.
2. **Dual-LLM competition** (Claude provider and pipeline), which plugs into the existing planner.
3. **Intake audit, budget recommendation, client targets**, which feed the planner and reports.
4. **Campaign review editor** (plan versions → payload rebuild → validation → approval).
5. **Frontend redesign** of all pages plus the new pages.
6. Tests, Playwright inspection, security review, docs.

## 3. Safety decisions carried over (non-negotiable)
- Mock is the default mode. Read-only mode uses real APIs and never writes; even Google's `validate_only` calls create nothing.
- Validated execution needs `GOOGLE_ADS_WRITES_ENABLED=true` **and** Super Admin authorization **and** a per-account allowlist. Creates are PAUSED, enabling needs a separate approval, and every write is read back.
- Broad match stays out of main ad groups. The vision lists broad match as a strategy option, so the planner may *recommend* a broad-match test ad group, which runs through the experiment / approval flow.
- Model agreement is never proof. Consolidation is decided by deterministic validation rules and evidence (keyword volume, intent labels, verified claims, landing-page checks, overlap).

## 4. External requirements (cannot be satisfied from the build sandbox)
Google Ads API access for the Cloud project (Explorer/Basic/Standard for production accounts; developer tokens are sunset) · Google Cloud OAuth client · Google Analytics Data/Admin API enabled · Business Profile API access approval (Google application form) · Groq API key · Anthropic API key · PageSpeed / Places API keys. The build sandbox's network policy blocks Google APIs, Groq and Anthropic, so no live request can be made from there; connection tests report that failure honestly.
