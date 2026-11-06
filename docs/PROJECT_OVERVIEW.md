# Project Overview

Status: **Phase 0 — planning document.** Nothing described here is implemented yet.

## 1. Objective

Build a working, testable, modular platform that lets a marketing team run Google Search lead-generation campaigns for multiple clients with AI assistance, while every Google Ads change is controlled by deterministic backend rules and human approval.

## 2. V1 scope

| In scope (V1) | Out of scope (Future / V2) |
|---|---|
| Goal = Leads (conversions) only | Sales, traffic, awareness goals |
| Campaign type = Search only | PMax, Display, Video, Shopping, Demand Gen (read-only display only) |
| Google Ads as the only ad platform | Meta, LinkedIn, TikTok, Microsoft Ads |
| Google Ads conversions as the lead source of truth | CRM / offline conversion import, lead quality scoring |
| Exact + phrase match in main ad groups | Broad match outside a separate, approved test ad group |
| Safe negatives as the only automatic write | Any other automatic optimization |
| Human picks A/B test winners | Automatic winner selection |
| Mock integrations for everything; Groq optionally real | Real Google Ads / Places / PageSpeed / OAuth until explicitly authorized |

## 3. End-to-end flow (from PDF, steps 1–14)

```
1 Task form ─► 3 Tracking gate ─┬─ RED ─► 3r Fix tracking / 3o Admin override (behaves as AMBER)
                                └─ GREEN/AMBER
                                     ▼
            4 Audits in parallel: 4a Website · 4b Business Profile · 4c Competitors · 4d Keywords
                                     ▼
            5 Feasibility + bottlenecks ─► 5a Client fix list ─► 5b Fix tracker
                                     ▼
            6 AI draft + AI challenger (+ human plan comparison)
                                     ▼
            7 Deterministic validation (mock validate-only → later real validate_only)
                                     ▼
            8 Human approval (exact payload version)
                                     ▼
            9 Execution: create PAUSED → read back → verify → separate approval → ENABLE
                                     ▼
           10 Daily monitoring · 10b Hourly emergency brake
                                     ▼
           11 Weekly review ─► 12 Enough-data gate ─┬─ hold (12n)
                                                    ├─ 13a Safe negatives (auto, shadow first)
                                                    └─ 13b Big change → approval card / A/B test
                                     ▼
           14 Learning loop (playbook rules, prediction vs actual, regression cases)
```

## 4. Guiding principles

1. **Deterministic core, AI at the edges.** AI drafts, labels, challenges and explains. Code calculates every number, validates every payload and decides every gate.
2. **Mock-first, adapter-based.** Business logic depends on interfaces; mock and real implementations are swappable by configuration, never by credential presence.
3. **Two-key writes.** Real writes need an environment flag *and* a Super Admin authorization recorded in the database, plus a valid approval for the exact payload.
4. **Provenance everywhere.** Every metric, audit and report carries `MOCK`, `LIVE` or `MIXED`.
5. **Auditability.** Every request, approval, AI call and external operation is traceable.

## 5. Conflicts between the PDF and the Master Prompt (resolved)

| # | PDF says | Master Prompt says | Resolution |
|---|---|---|---|
| C1 | AI service = Anthropic Claude API | Use Groq; do not use Claude API | **Groq** via `AIProvider` interface. |
| C2 | Step 9: create paused, read back, *then turn on* in the same flow | Enabling requires a *separate* approved action | **Separate ENABLE approval.** Create and enable are two approvals (see DECISIONS_REQUIRED D-11 for an optional combined card). |
| C3 | Step 3: `ConversionActionService` creates missing conversion actions at onboarding | Nothing changes without approval | Creating conversion actions becomes an **approval-gated mutation**. |
| C4 | Step 10b: emergency brake pauses / lowers budget automatically | Emergency writes on real Google Ads need a separate, explicitly approved policy | **Simulated only** in V1; real emergency writes are disabled until a separate policy is approved. |
| C5 | Step 8: "edit inline, then approve" | Approval applies only to the exact version reviewed | Inline edit creates a **new payload version**, re-runs validation, then that version is approved. |
| C6 | Step 4a: Claude vision checks above-the-fold mobile content | Groq is the AI provider | Use a **deterministic Playwright check** (element positions in the first mobile viewport) as primary; optional Groq vision if a supported model is configured (D-12). |
| C7 | Step 7: on failure "AI fixes it (max 2 tries)" | Never execute malformed output; controlled retry | Keep max 2 AI fix attempts; each attempt is fully re-validated; then flag to a human. |
| C8 | PDF does not list a Google Ads developer token | `.env` list omits it | `GOOGLE_ADS_DEVELOPER_TOKEN` added — the Google Ads API requires one for every real call. |

## 6. Success criteria for V1

- Full workflow runs end-to-end in MOCK mode with zero external credentials.
- Automated E2E test: new client → simulated campaign created and verified in `PAUSED`.
- Automated test proving the campaign cannot be enabled without the required approval.
- All 32 required test scenarios pass (see TESTING_STRATEGY.md).
- No real Google Ads write path reachable while `GOOGLE_ADS_WRITES_ENABLED=false`.
