---
name: conversion-analysis
description: Use when working on conversion tracking, the GREEN/AMBER/RED tracking gate, GA4 (Analytics Data API) integration, client weekly conversion targets, cost per conversion, budget recommendation / feasibility, or the weekly performance diagnosis in this app.
---

# Conversion analysis (this project)

Source of truth for leads in V1 = **Google Ads conversions** (read via the Google Ads API). GA4 is a supporting source (traffic, events, key events), never mixed into Ads conversion counts without labelling.

## Code
| Concern | File |
|---|---|
| Tracking gate | `backend/app/services/tracking_gate.py` |
| Budget recommendation & feasibility (deterministic, no AI) | `backend/app/services/feasibility.py` |
| Weekly review & diagnosis tree | `backend/app/services/reporting.py` |
| GA4 connection test (Admin + Data API; no GA4 reporting adapter yet) | `backend/app/services/integrations_service.py` (`_test_ga4`) |
| Client / campaign targets | `clients.weekly_conversion_target`, `campaigns.weekly_conversion_target` |

## Tracking gate — what each state means
| State | Rule | Consequence |
|---|---|---|
| GREEN | Auto-tagging on, Google Ads tag detected, primary form conversion, counting ONE_PER_CLICK, no duplicate GA4 import, ≥ `tracking.green_min_conversions_30d` recorded, no per-campaign outage | Full workflow; automation may run after the enough-data gate |
| AMBER | Configured, but not yet verified by real leads (or a non-critical issue such as call length) | Plan and launch with approval; no conversion-based automation |
| RED | Missing, broken, duplicate or untrusted | Fix list; build blocked unless an admin override (which behaves as AMBER) |
A detected tag proves **installation**, not that conversions fire. Docs: https://support.google.com/google-ads/answer/1722022 · Counting (One vs Every): https://support.google.com/google-ads/answer/3438531

## Budget recommendation & feasibility math (all estimates)
- daily = monthly / 30.4; weekly = monthly / 4.345
- CPC range from Keyword Planner top-of-page bids (median; ×1.3 conservative / ×0.8 optimistic) or 90-day account history (≥ 200 clicks)
- CVR range from history, else the business-type table (`feasibility.cvr_by_business_type`), reduced by the review-gap adjustment
- expected weekly conversions = weekly / CPC × CVR; **recommended monthly budget** = target × expected CPL × 4.345
- verdict: FEASIBLE (expected ≥ target) · PARTIALLY FEASIBLE (only optimistic ≥ target) · UNREALISTIC; always with the adjustment options (budget, target, scope)

## Weekly diagnosis order (rules and AI independently; only causes named by both become actions)
1 tracking working? · 2 budget limiting (budget-lost IS) · 3 ad rank limiting (rank-lost IS) · 4 CTR low · 5 conversion rate low · 6 calls missed or short · 7 CPC too high · 8 client fixes open.
**Don't answer a shortfall by only raising the budget.** Check 1, 3, 4, 5 and 8 first.

## GA4 facts (official)
- Reporting: Google Analytics **Data API** `properties/{id}:runReport` (scope `https://www.googleapis.com/auth/analytics.readonly`). Docs: https://developers.google.com/analytics/devguides/reporting/data/v1
- Property discovery: Analytics **Admin API** `accountSummaries.list`. Docs: https://developers.google.com/analytics/devguides/config/admin/v1
- GA4 "key events" are distinct from Google Ads conversions. If a GA4 key event is imported into Google Ads as a primary conversion *and* the Google Ads tag also counts the same lead, the tracking gate flags a duplicate.

## Checklist
- [ ] Every number in a report comes from code; AI only writes the summary text
- [ ] MOCK / LIVE / MIXED source label on every metric block
- [ ] The 30-day re-download stays in place (conversion lag)
- [ ] The weekly report compares against the **client** target and each campaign target
- [ ] Changing a gate rule → update `tests/integration/test_tracking_audits_ai.py`
