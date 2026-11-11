---
name: campaign-strategy
description: Use when designing or changing how this app plans Google Search lead-generation campaigns — campaign structure, keyword research and classification, match types, negative keywords, ad groups, responsive search ads, landing pages, bidding and budget allocation — including the drafting/challenger prompts and the dual-LLM consolidation.
---

# Google Search campaign strategy (this project)

Scope: **Google Ads Search only**, goal = **Leads (conversions)**. No Display, PMax creation, Meta or Local Services Ads.

## Where strategy lives in code
| Step | File |
|---|---|
| Keyword research, labelling (SERVICE / GENERIC / IRRELEVANT), clusters, overlap | `backend/app/services/audits.py` (`keyword_research`, `overlap_check`) |
| Feasibility & budget recommendation | `backend/app/services/feasibility.py` |
| Draft, challenger, human-plan comparison, payload builder | `backend/app/services/planner.py` |
| Dual-LLM competition & consolidation | `backend/app/services/competition.py` |
| Prompts (versioned) and output schemas (strict) | `backend/app/adapters/ai/prompts.py`, `schemas.py` |
| Playbook rules injected into drafting | `backend/app/services/learning.py` |

## Structure workflow
1. **One campaign per business goal and geography**; ad groups = one service each (tight intent), plus an optional *Generic* group ("plumber near me").
2. Keywords per ad group: SERVICE-labelled ideas for that service, by volume, filtered for affordability (high top-of-page bid ≤ daily budget / 5 is the current heuristic).
3. **Match types:** EXACT + PHRASE in main ad groups. **Broad match** only as a separate, approved experiment with reliable conversion data and Smart Bidding (Google recommends pairing broad match with Smart Bidding). Docs: https://support.google.com/google-ads/answer/7478529
4. **Negatives:** IRRELEVANT ideas (jobs, DIY, free, salary, training, excluded services) as campaign exact negatives; single-word patterns in the shared list "GAA account negatives" as phrase. Docs: https://support.google.com/google-ads/answer/2453972
5. **Responsive search ads:** up to 15 headlines (≤30 chars) and 4 descriptions (≤90 chars), paths ≤15. Use only **verified claims**, no excluded services, no banned brand phrases, no phone numbers in text (use a call asset), and no "!" in headlines. Docs: https://support.google.com/google-ads/answer/7684791
6. **Landing pages:** dedicated service page from the website audit (`landing_page_map`); flag services without a page as client fixes.
7. **Settings:** Google Search network only (Search Partners and Display off), location option PRESENCE, ads 24/7, call asset limited to business hours.
8. **Bidding:** Maximize Conversions when conversions are recorded or not yet verifiable. Maximize Clicks with a CPC cap only when the measurement audit confirms no conversions are recorded. Docs: https://support.google.com/google-ads/answer/7381968
9. **Budget:** daily = monthly / 30.4 (Google's monthly spending limit is 30.4 × average daily budget). Allocation % per ad group comes from the draft; the amounts are computed by code.

## Dual-LLM competition rules
- Both models get **identical** inputs (same payload, same prompt template id and version).
- Each model drafts independently, then challenges the *other* draft.
- Consolidation is **rule-based**: pick per-ad-group keywords and ads by evidence (volume, intent label, verified-claim compliance, length/policy validity, landing-page match, no overlap). Agreement between models is a signal, **never proof**.
- Record both drafts, both challenges, the comparison and the consolidation rationale (AIRun + CampaignPlan).

## Checklist before shipping a strategy change
- [ ] Prompt changed → bump its `version` in `prompts.py`
- [ ] Run the regression cases (Learning → Re-run, or `learning.run_regression`); all must pass
- [ ] Deterministic validation (`services/validation.py`) still rejects broad match in main groups, unverified claims, and excluded services
- [ ] Payload still creates everything PAUSED (`tests/e2e/test_full_workflow.py`)
- [ ] No metric is produced by the AI; numbers come from code

## Official references
Search campaign best practices (Google Ads Help): https://support.google.com/google-ads/answer/6324971 · Keyword Planner: https://support.google.com/google-ads/answer/7337243 · Ad strength: https://support.google.com/google-ads/answer/9921843
(Help Center article IDs occasionally change. If a link fails, search the article title on support.google.com/google-ads.)
