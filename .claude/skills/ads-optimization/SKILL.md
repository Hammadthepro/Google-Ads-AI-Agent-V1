---
name: ads-optimization
description: Use when working on post-launch optimisation of Google Search campaigns in this app — search-term analysis, safe negative keywords, wasted-spend detection, the enough-data gate, keyword pausing / promotion, budget and bidding recommendations, weak ads, the emergency brake and A/B experiments.
---

# Ads optimisation (this project)

The principle: **only safe negative keywords may ever be automatic** (and only in ACTIVE mode with GREEN tracking). Everything else is an approval card. Nothing is deleted.

## Code
| Concern | File |
|---|---|
| Daily sync (30-day re-download for conversion lag), wasted spend, search terms | `backend/app/services/monitoring.py` |
| Enough-data gate, negatives, big-change proposals | `backend/app/services/optimization.py` |
| Emergency brake (hourly, no AI) | `backend/app/services/emergency.py` |
| Experiments (human picks the winner) | `backend/app/services/experiments.py` |
| Thresholds (test values, editable) | `backend/app/services/settings_service.py` → System Settings page |

## Search-term → negative workflow
1. Read `search_terms` (30-day window plus 90-day conversions).
2. A candidate needs ≥ `negatives.min_clicks` clicks and 0 conversions, and either matches an irrelevant pattern (jobs, DIY, free, salary, course, training, "how to") or has ≥ `wasted.min_clicks`.
3. **Safety checks (all must pass):** no lead in 90 days · doesn't equal an active keyword · doesn't block any converting search term · for phrase patterns, matches **no** converting term.
4. Level: AD_GROUP exact if it fails in only one ad group; else CAMPAIGN exact; patterns go to the shared list as PHRASE.
5. Enough-data gate (§ below) must be GO; daily cap `AUTO_NEGATIVES_DAILY_LIMIT` (25).
6. SHADOW mode (default): record as proposal only. ACTIVE needs env `AUTO_NEGATIVES_MODE=active` **and** setting `negatives.mode=active`.

## Enough-data gate (all must pass)
Volume (≥ `enough_data.min_clicks` or spend ≥ multiple × target CPL) · conversion delay passed · not in Google's learning phase (`bidding_strategy_system_status`) · ≥ 7 days since the last change to the item · tracking GREEN with no override. On HOLD, record the reason; it appears in the weekly review.

## Big changes (always approval)
| Signal | Proposal |
|---|---|
| Budget-lost impression share > threshold and CPL ≤ target | +20% budget (BUDGET approval; Super Admin above threshold) |
| Rank-lost impression share high | Advice: relevance / landing page / bids |
| Converting search term not yet a keyword | Add as EXACT, created PAUSED → ENABLE approval |
| Keyword cost ≥ 2 × target CPL with 0 conversions | Pause keyword (PAUSE approval) |
| Ad CTR < ratio × ad-group average | Replace ad via an *Edit an ad* task |

Impression-share metrics: https://support.google.com/google-ads/answer/2497703 · Search terms report: https://support.google.com/google-ads/answer/2472708

## A/B testing rules
One variable per test (BIDDING / AD_ANGLE / MATCH_TYPE / LANDING_PAGE), 50/50, enough data in both arms (configurable), and **a person picks the winner** (EXPERIMENT_WINNER approval). Never change other variables during a test. Google's experiments: https://support.google.com/google-ads/answer/6261395

## Emergency brake
Deterministic: projected spend vs `emergency.alert_multiple` / `freeze_multiple` × daily budget (Google can spend up to 2× the average daily budget on a single day: https://support.google.com/google-ads/answer/2375423), landing page down, tracking outage, conversion collapse with stable clicks, failed jobs. Google Ads actions are SIMULATED; freezing automation is internal.

## Checklist for any optimisation change
- [ ] No automatic action except safe negatives; new auto behaviour must go through `execution.execute_policy` and the WriteGuard
- [ ] Gate and safety checks unit-tested (`tests/integration/test_monitoring_optimization.py`)
- [ ] Recommendation dedupe key stable (so it isn't proposed every day)
- [ ] Weekly review shows applied, shadow, held and proposed items
