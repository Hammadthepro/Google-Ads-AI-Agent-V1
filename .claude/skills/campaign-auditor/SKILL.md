---
name: campaign-auditor
description: Use when validating a Google Search campaign before approval or launch in this app — deterministic validation rules, ad policy checks, budget and targeting checks, conversion-tracking gate, landing pages, duplicates, and pre-launch verification / read-back. Also for adding a new validation rule.
---

# Campaign auditor (pre-launch validation)

Validation passing ≠ approved ≠ launched. The flow is: validation → human approval of the exact payload hash → execution (PAUSED) → read-back → separate ENABLE approval.

## Code
| Check | File |
|---|---|
| Deterministic rules | `backend/app/services/validation.py` (`validate`) |
| Simplified Google editorial checks | `backend/app/adapters/google_ads/mock.py` (`policy_issues_for_text`) |
| Conversion-tracking gate (GREEN/AMBER/RED) | `backend/app/services/tracking_gate.py` |
| Write guard (modes, kill switches, approvals) | `backend/app/services/safety.py` |
| Read-back verification & reconciliation | `backend/app/services/execution.py` |

## Pre-launch checklist (each item maps to a rule; codes in brackets)
**Structure**
- [ ] Campaign type SEARCH; network Google Search only, Search Partners and Display off [NETWORK, CAMPAIGN_TYPE]
- [ ] Location option PRESENCE [GEO_TYPE]; at least one location target [LOCATIONS]
- [ ] Bidding strategy present; Maximize Clicks only as the RED-override exception [BIDDING]
- [ ] All creates PAUSED [NOT_PAUSED]; no remove operations [DELETE_FORBIDDEN]
- [ ] No duplicate campaign name in the account; no keyword that already exists in the ad group [DUPLICATE_RESOURCE]

**Budget**
- [ ] Daily budget > 0 and equals monthly / 30.4 (±0.05) [BUDGET]
- [ ] Increase above threshold (`approvals.budget_super_admin_pct` / `_abs_usd`) → Super Admin approval [BUDGET_THRESHOLD]
- [ ] Feasibility verdict shown on the approval card (estimate, never a guarantee)

**Keywords**
- [ ] No BROAD in main ad groups [MATCH_TYPE]; ≤ 80 chars and ≤ 10 words [KEYWORD_LENGTH]
- [ ] No excluded service [EXCLUDED_SERVICE]; no duplicate in the same group [DUPLICATE_KEYWORD]; overlap across groups → warning [KEYWORD_OVERLAP]

**Ads (RSA)**
- [ ] 3–15 headlines ≤ 30 chars, 2–4 descriptions ≤ 90 chars, paths ≤ 15 [AD_LENGTH]
- [ ] Editorial: no "!" in headlines, no repeated punctuation, no gimmicky caps, no phone numbers in text [POLICY]. Google's policy is broader; see https://support.google.com/adspolicy/answer/6021546
- [ ] Claims only from the business's **verified claims** list [UNVERIFIED_CLAIM]; no banned brand phrases [BRAND_RULE]; no excluded services [EXCLUDED_SERVICE_IN_AD]

**Landing pages & tracking**
- [ ] Every final URL returns 2xx through the crawler (SSRF-guarded) [LANDING_PAGE]
- [ ] Tracking gate not RED unless an admin override exists [TRACKING]. Override = AMBER, which means no conversion-based automation
- [ ] Google-side `validate_only` mutate passes [ADAPTER_VALIDATION]

**After execution**
- [ ] Operation SUCCEEDED and `verification_passed` true (status PAUSED, names, budget, keywords and headlines match)
- [ ] On timeout: reconciliation decides SUCCEEDED / NOT_APPLIED / RECONCILE_CONFLICT. Never resend blindly
- [ ] ENABLE approval created; enabling only after it is approved

## Adding a validation rule
1. Add the check in `validation.validate` with a new `code`; mark it `fixable` only if the AI fix loop may repair it (ad text issues).
2. Add a failing-case assertion in `tests/integration/test_approvals_execution.py::test_validation_failures_are_reported`.
3. Mention it in `docs/GOOGLE_ADS_SAFETY_RULES.md` and in this skill's checklist.

## Official references
Google Ads policies: https://support.google.com/adspolicy/answer/6008942 · Editorial requirements: https://support.google.com/adspolicy/answer/6021546 · Location options (Presence): https://support.google.com/google-ads/answer/1722038
