# Approval Workflow

Status: **Implemented in V1** (tests: see TEST_RESULTS.md).

## 1. State machine

```
DRAFT ──validate──► VALIDATION_FAILED ──(AI fix ≤2 / human edit)──► DRAFT
  │
  └──validate ok──► PENDING_APPROVAL ──approve──► APPROVED ──execute──► EXECUTING ─► EXECUTED
                        │   │   │                    │                      └──► EXECUTION_FAILED
                        │   │   └─request changes──► CHANGES_REQUESTED ─► (new version) DRAFT
                        │   └─reject (reason req.)─► REJECTED
                        ├─payload edited──────────► INVALIDATED (new version created)
                        └─timeout──────────────────► reminder → EXPIRED → escalated to backup owner
APPROVED ──payload changed / expired before execution──► INVALIDATED
```

## 2. Payload binding
- `payload_hash = SHA-256(canonical_json(payload))` — keys sorted, no whitespace, decimals as strings, micros as integers.
- Approve request must send the `payload_hash` the approver saw. Backend compares with stored hash **and** recomputes from stored payload. Any mismatch → 409, approval not recorded.
- Execution recomputes the hash again. Mismatch → `INVALIDATED`, nothing executed.
- An approval authorises **one** operation set. It is consumed on execution and can never be reused.

## 3. Approval card contents
Tracking status → feasibility → open client fixes → research summary → proposed change (current vs new state diff) → reason → expected effect (labelled ESTIMATE) → risks → validation errors/warnings → challenger objections → human-plan comparison → AI explanation → source data (with MOCK/LIVE badge) → requester.

## 4. Approval types
| Type | Example | Who may approve |
|---|---|---|
| CREATE | New campaign (paused) | Admin, Super Admin |
| ENABLE | Turn on verified paused resources | Admin, Super Admin |
| UPDATE / PAUSE | Edit ad (replace), pause ad, locations | Admin, Super Admin |
| BUDGET | Budget increase/shift | Admin; above threshold: Super Admin (D-08) |
| NEGATIVES | Batch of proposed negatives (shadow mode) | Admin, Super Admin |
| CONVERSION_ACTION | Create/modify conversion actions | Admin, Super Admin |
| EXPERIMENT_START / EXPERIMENT_WINNER | A/B test | Admin, Super Admin |

Campaign Managers submit; Analysts can neither submit mutations nor approve. Self-approval policy: D-07.

## 5. Recorded for every decision
Approver, timestamp, decision, reason (mandatory for reject/changes requested), exact payload, payload version, payload hash, IP, request id. Edits store before/after/who/when for the learning loop.
