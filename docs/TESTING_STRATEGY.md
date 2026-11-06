# Testing Strategy

Status: **Phase 0 — plan.**

## 1. Layers
| Layer | Tool | Scope |
|---|---|---|
| Unit | pytest | Rules, calculations, hashing, validators, parsers |
| Database | pytest + real PostgreSQL (transaction rollback per test) | Models, constraints, migrations up/down |
| Contract | pytest parametrized over adapter implementations | Same suite for mock and (later) real adapters |
| API | pytest + httpx `AsyncClient` | Every endpoint incl. 401/403 cases |
| Integration | pytest + Celery eager mode + SimulatedClock | Multi-module workflows |
| Security | pytest | RBAC matrix, CSRF, SSRF, secret redaction, write-guard bypass attempts |
| Frontend | Vitest + Testing Library | Components, forms, DataSourceBadge |
| E2E | Playwright | Browser through full stack in mock mode |
| AI regression | pytest over `regression_cases` | Prompt/rule changes |

Coverage target: ≥ 85% for `safety`, `approvals`, `execution`, `feasibility`, `optimization`, `tracking_gate`.

## 2. Required scenarios → phase
| # | Scenario | Phase |
|---|---|---|
| 1 | Successful development login | 2 |
| 2 | Unauthorized user blocked | 2 |
| 3 | Role-based permissions (full matrix) | 2 |
| 4–5 | Create client / business | 4 |
| 6–9 | Website, GBP, competitor audits; keyword research | 7–8 |
| 10–13 | GREEN / AMBER / RED gate; admin override | 6 |
| 14 | Feasibility calculations (incl. PDF example $1,500/mo, 50/wk ≈ $6.90/lead) | 8 |
| 15–16 | AI draft via Mock; via Groq (skipped unless configured — reported as SKIPPED, never PASSED) | 9 |
| 17–18 | Challenger disagreement; invalid AI JSON | 9 |
| 19–21 | Validation failure; approval workflow; invalidation after payload change | 10 |
| 22–25 | Mock creation; paused-first; read-back; reconciliation after timeout-after-commit | 11 |
| 26 | Emergency brake | 12 |
| 27–30 | Negative safety; enough-data hold; weekly report; experiment workflow | 13 |
| 31 | Learning loop | 14 |
| 32 | Integration failure handling (every failure type) | 3 + all |
| E2E-A | New client → campaign created and verified PAUSED | 15 |
| E2E-B | Campaign cannot be enabled without ENABLE approval | 15 |
| SEC-W | Real write impossible while `GOOGLE_ADS_WRITES_ENABLED=false` (even with real adapter loaded) | 11 |

## 3. Commands (to be created in Phase 1)
`make test` · `make test-backend` · `make test-frontend` · `make e2e` · `make lint`. Results recorded verbatim in TEST_RESULTS.md each phase.
