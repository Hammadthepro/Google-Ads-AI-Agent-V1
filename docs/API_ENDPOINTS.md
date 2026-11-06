# API Endpoints (planned)

Status: **Phase 0 — plan.** Prefix `/api/v1`. Every route except health and login requires a session; state-changing routes require CSRF. OpenAPI docs served at `/api/docs` (disabled in production unless authenticated).

| Group | Endpoints | Min role |
|---|---|---|
| Health | `GET /healthz`, `GET /readyz` | public |
| Auth | `POST /auth/dev-login`, `POST /auth/google`, `POST /auth/logout`, `GET /auth/me` | — |
| Users | `GET/POST /users`, `PATCH /users/{id}`, `POST /users/{id}/deactivate|activate` | Admin |
| Clients | `GET/POST /clients`, `GET/PATCH /clients/{id}` | CM (read: Analyst) |
| Businesses | `GET/POST /clients/{id}/businesses`, `GET/PATCH /businesses/{id}`, services, competitors | CM |
| Ads accounts | `GET /ads-accounts`, `POST /ads-accounts/discover`, `GET /ads-accounts/{id}/campaigns|ad-groups|ads` | Analyst |
| Ads OAuth | `GET /ads-oauth/start`, `GET /ads-oauth/callback`, `POST /ads-oauth/{id}/disconnect` | Super Admin |
| Tasks | `POST /tasks` (Run), `GET /tasks`, `GET /tasks/{id}`, `GET /tasks/{id}/steps` | CM |
| Tracking gate | `POST /businesses/{id}/tracking-gate/run`, `GET …/latest`, `POST …/override` | CM / Admin |
| Audits | `POST /audits` (type), `GET /audits/{id}` | Analyst |
| Keywords | `POST /keyword-research`, `GET /keyword-research/{id}`, `POST /keyword-research/{id}/overlap-check` | CM |
| Feasibility | `POST /feasibility`, `GET /feasibility/{id}` | CM |
| Fixes | `GET/POST /fixes`, `PATCH /fixes/{id}` | CM |
| Planner | `POST /plans` (draft), `POST /plans/{id}/challenge`, `POST /plans/{id}/compare-human`, `GET /plans/{id}` | CM |
| Validation | `POST /validations`, `GET /validations/{id}` | CM |
| Approvals | `GET /approvals`, `GET /approvals/{id}`, `POST /approvals/{id}/approve` (body: payload_hash), `…/reject`, `…/request-changes`, `PUT /approvals/{id}/payload` (creates new version) | Admin to decide |
| Execution | `POST /operations` (from approval), `GET /operations/{id}`, `POST /operations/{id}/reconcile` | Admin |
| Monitoring | `GET /monitor/campaigns`, `/search-terms`, `/wasted-spend`, `/change-events` | Analyst |
| Emergency | `GET /emergency/events`, `GET/PATCH /kill-switches`, `GET/PATCH /emergency/rules` | Admin / Super Admin |
| Reports | `GET /weekly-reports`, `POST /weekly-reports/generate`, `GET /weekly-reports/{id}` | Analyst |
| Optimization | `GET /recommendations`, `GET /negatives/proposals` | Analyst |
| Experiments | `POST /experiments`, `GET /experiments/{id}`, `POST /experiments/{id}/select-winner` (creates approval) | CM / Admin |
| Learning | `GET/POST /playbook-rules`, `GET/POST /regression-cases`, `POST /regression-cases/run` | Admin |
| Integrations | `GET /integrations`, `POST /integrations/{name}/test` | Admin |
| Settings | `GET/PATCH /settings`, `POST /settings/real-writes-authorization` | Super Admin |
| Activity | `GET /activity-logs` | per role |
| Mock controls (TEST_MODE only) | `POST /dev/mock/reset`, `POST /dev/mock/advance-time`, `POST /dev/mock/failures` | Super Admin |
