---
name: conversion-analysis
description: Use when working on conversion tracking, the conversion measurement audit, GA4 (Analytics Data API) integration, client weekly conversion targets, cost per conversion, budget recommendation / feasibility, or the weekly performance diagnosis in this app.
---

# Conversion analysis (this project)

Source of truth for leads in V1 = **Google Ads conversions** (read via the Google Ads API). GA4 is a supporting source (traffic, events, key events), never mixed into Ads conversion counts without labelling.

## Code
| Concern | File |
|---|---|
| Measurement audit | `backend/app/services/measurement.py` |
| Budget recommendation & feasibility (deterministic, no AI) | `backend/app/services/feasibility.py` |
| Weekly review & diagnosis tree | `backend/app/services/reporting.py` |
| GA4 connection test (Admin + Data API; no GA4 reporting adapter yet) | `backend/app/services/integrations_service.py` (`_test_ga4`) |
| Client / campaign targets | `clients.weekly_conversion_target`, `campaigns.weekly_conversion_target` |

## Measurement audit
There is no status, gate or override. `measurement.audit()` stores findings (inspected, result `ok`/`issue`/`attention`/`not_assessable`, actual, evidence, severity, impact, action, action type) and facts. Missing evidence is `not_assessable`, never an issue. Nothing here blocks preparation or deployment.
- Bidding reads `supports_conversion_bidding(facts)`; conversion-based automation reads `conversion_data_reliable()`.
- Threshold: `measurement.min_conversions_30d`.

A detected tag proves **installation**, not that conversions fire. Docs: https://support.google.com/google-ads/answer/1722022 · Counting (One vs Every): https://support.google.com/google-ads/answer/3438531

## Budget recommendation & feasibility math (all estimates)
- daily = monthly / 30.4; weekly = monthly / 4.345
- CPC range from Keyword Planner top-of-page bids (median; ×1.3 conservative / ×0.8 optimistic) or 90-day account history (≥ 200 clicks)
- CVR range from history, else the business-type table (`feasibility.cvr_by_business_type`), reduced by the review-gap adjustment
- expected weekly conversions = weekly / CPC × CVR; **recommended monthly budget** = target × expected CPL × 4.345
- verdict: FEASIBLE (expected ≥ target) · PARTIALLY FEASIBLE (only optimistic ≥ target) · UNREALISTIC; always with the adjustment options (budget, target, scope)

## Weekly diagnosis order (rules and AI independently; only causes named by both become actions)
1 conversions being recorded? · 2 budget limiting (budget-lost IS) · 3 ad rank limiting (rank-lost IS) · 4 CTR low · 5 conversion rate low · 6 calls missed or short · 7 CPC too high · 8 client fixes open.
**Don't answer a shortfall by only raising the budget.** Check 1, 3, 4, 5 and 8 first.

## GA4 facts (official)
- Reporting: Google Analytics **Data API** `properties/{id}:runReport` (scope `https://www.googleapis.com/auth/analytics.readonly`). Docs: https://developers.google.com/analytics/devguides/reporting/data/v1
- Property discovery: Analytics **Admin API** `accountSummaries.list`. Docs: https://developers.google.com/analytics/devguides/config/admin/v1
- GA4 "key events" are distinct from Google Ads conversions. If a GA4 key event is imported into Google Ads as a primary conversion *and* the Google Ads tag also counts the same lead, the measurement audit reports a duplicate.

## Checklist
- [ ] Every number in a report comes from code; AI only writes the summary text
- [ ] MOCK / LIVE / MIXED source label on every metric block
- [ ] The 30-day re-download stays in place (conversion lag)
- [ ] The weekly report compares against the **client** target and each campaign target
- [ ] Changing a gate rule → update `tests/integration/test_tracking_audits_ai.py`
