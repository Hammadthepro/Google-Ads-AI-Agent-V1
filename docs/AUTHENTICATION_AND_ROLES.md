# Authentication and Roles

Status: **Phase 0 — design.** Built in Phase 2.

## 1. Login
- **Development:** `MockAuthenticationProvider` — login screen listing seeded users; available only when `APP_ENV != production` and `AUTH_PROVIDER=mock` (startup fails otherwise).
- **Production:** Google Identity Services on the frontend returns an ID token → `POST /api/auth/google` → backend verifies the token (Google public keys, `aud` = our client id, `iss`, `exp`, `email_verified`, optional allowed domains) → finds an **existing, active** user by email/sub. Unknown users are rejected (no self-registration) unless invited.
- **Sessions:** opaque random session id in httpOnly, Secure, SameSite=Lax cookie; hashed in DB; idle + absolute expiry; logout revokes server-side. CSRF token required on POST/PUT/PATCH/DELETE.
- **First-admin bootstrap:** `BOOTSTRAP_ADMIN_EMAIL` — first successful login with that email becomes Super Admin once; then the variable is ignored. Also a CLI: `python -m app.cli create-super-admin`.
- **Separation:** app login never grants Google Ads access. Ads accounts are connected through the separate Ads OAuth flow by an authorised user.

## 2. Permission matrix
| Capability | Super Admin | Admin | Campaign Manager | Analyst |
|---|:-:|:-:|:-:|:-:|
| Manage users / roles | ✔ | assigned users only (not Super Admin) | — | — |
| Manage integrations, system settings, kill switches (global) | ✔ | — | — | — |
| Client/account kill switch | ✔ | ✔ | — | — |
| Create/edit clients & businesses | ✔ | ✔ | assigned (edit) | — |
| Run audits | ✔ | ✔ | ✔ | permitted audits |
| Create tasks / campaign plans / recommendations | ✔ | ✔ | ✔ | — |
| Submit approvals | ✔ | ✔ | ✔ | — |
| Approve / reject changes | ✔ | ✔ | — | — |
| Tracking gate override | ✔ | ✔ (named, D-01) | — | — |
| Authorize real writes | ✔ | — | — | — |
| Pick experiment winner | ✔ | ✔ | — | — |
| Create playbook rules | ✔ | ✔ | — (D-17) | — |
| View reports / monitor | ✔ | ✔ | ✔ | ✔ |
| View activity logs | all | own clients | own actions | — |

Enforced by a FastAPI dependency `require(permission, client_id)` on every route; row-level client scoping for non-Super-Admin users. Tests cover every cell.

## 3. User lifecycle
Invite (email + role) → active on first login → deactivate (sessions revoked immediately, pending approvals reassigned/escalated) → reactivate. All changes logged.
