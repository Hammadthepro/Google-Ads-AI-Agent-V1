# Google Ads Safety Rules

Status: **Phase 0 — binding design rules.** Every rule below becomes an automated test.

## 1. The core rule
Nothing changes in Google Ads without an approval for the exact payload, **except** safe negative keywords that pass every deterministic check in §5 under an active auto-negative policy. In V1 the auto-negative policy starts in **shadow mode** (proposals only).

## 2. Real-write enablement (all must be true)
1. `GOOGLE_ADS_MODE=real` (explicit; never inferred from credentials).
2. `GOOGLE_ADS_WRITES_ENABLED=true` (environment).
3. Database setting `real_writes_authorized=true`, set by a Super Admin with a written reason (audited).
4. `google_ads_accounts.writes_allowed=true` for the target account.
5. Global, client and account kill switches are all off.

Initial values: 1 = mock, 2 = false, 3 = false, 4 = false. Checked in `WriteGuard` **and** again inside `RealGoogleAdsAdapter`.

## 3. Operation rules
| Rule | Enforcement |
|---|---|
| AI never calls an adapter mutate method | AI module has no access to adapters; tested by import contract |
| Every mutation re-validated immediately before execution | `ExecutionService` |
| Approval bound to payload hash + version | Hash mismatch → `APPROVAL_INVALIDATED` |
| New campaigns, ad groups, ads, keywords created **PAUSED** | Payload builder forces status; validation rejects otherwise |
| Created resources read back and compared to payload | `verification_passed` must be true before enable is offered |
| Enabling requires a separate ENABLE approval | Approval type `ENABLE` |
| Atomic multi-resource create (one mutate batch, temp resource IDs) | Mock simulates all-or-nothing; real uses `GoogleAdsService.Mutate` |
| Idempotency key per operation; row committed before the call | `operations.idempotency_key` unique |
| Never blind-retry a create after timeout/unknown | State `UNKNOWN` → reconcile by reading state (deterministic names/labels) |
| Never automatically delete (REMOVE) anything | `REMOVE` operations not exposed by the service layer in V1 |
| Never auto-pick an experiment winner | Approval type `EXPERIMENT_WINNER`, human only |
| Manual changes detected (`change_event`) before writes | Conflicting manual change blocks the operation and alerts |
| Search Network only, Search Partners + Display off, geo target type PRESENCE | Launch defaults, validated |
| Broad match not allowed in main ad groups | Validation error (unless separate approved test ad group, D-14) |
| Mock operations never reported as real | `integration_mode` on every operation; UI badge |

## 4. Tracking gate effects
| State | Planning | Launch | Conversion-based automation |
|---|---|---|---|
| GREEN | Yes | With approval | Allowed if enough-data gate passes |
| AMBER | Yes | With approval | No |
| RED | Fix list shown; blocked | Blocked unless admin override | No |
| Override | As AMBER | With approval | No |

Bidding default: Maximize Conversions. Exception (PDF 3o): Maximize Clicks with a CPC cap while tracking is RED + override — requires approval.

## 5. Safe negative keyword checks (all must pass)
1. Search term produced no conversion in the last **90 days** (configurable).
2. Negative does not match/block any active keyword (exact, phrase and close-variant normalisation).
3. For phrase negatives / patterns: does not match **any** converting search term in the lookback window.
4. Enough-data gate passes for the item.
5. Scoped to the correct campaign/ad group (ad-group-level when it fails in only one ad group).
6. Daily cap per client not exceeded (default 25).
7. Tracking GREEN (conversion-based decision).
8. Kill switches off; auto-negative policy mode = ACTIVE (default SHADOW → proposal only).
Every proposed and applied negative is logged with the evidence used.

## 6. Emergency brake
Deterministic, hourly. In V1 all actions are **simulated** against the mock. Real emergency writes stay disabled until a separate Emergency Write Policy is approved and enabled by a Super Admin (D-09). Freezing automation (an internal flag) is always allowed.

## 7. Secrets
Never in frontend, API responses, logs or normal DB columns. OAuth refresh tokens encrypted with `TOKEN_ENCRYPTION_KEY`; only a fingerprint is shown.
