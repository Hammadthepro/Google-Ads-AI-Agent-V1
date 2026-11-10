# Integration configuration: who configures what, precedence, encryption

## Who configures what
| Integration | Configured by | Where | Notes |
|---|---|---|---|
| Google Ads, GA4, Business Profile, Groq, Claude, Places, PageSpeed, SMTP | **Super Admin, in the application** | Integrations page | Encrypted in the database; the environment is only a fallback. |
| Keyword Planner, Website crawler | nobody (no credentials) | — | Keyword Planner depends on Google Ads (see the dependency panel). |
| Google sign-in, Database, Redis | **Deployment** (whoever runs the app) | environment (`GOOGLE_CLIENT_ID` + `AUTH_PROVIDER=google`, `DATABASE_URL`, `REDIS_URL`) | Shown as *Deployment-managed*; not editable in the app because a wrong value could lock everyone out or expose infrastructure secrets. Super Admins can still run their tests. |

Ordinary users (Admin, Campaign Manager, Analyst) can see the masked status; every change, test, enable/disable and disconnect is rejected server-side (HTTP 403).

## Precedence (per field)
1. a value saved on the Integrations page (database, per workspace) — **wins**;
2. otherwise the environment variable;
3. otherwise the built-in default (e.g. SMTP port 587, security `starttls`).
Blank values never override a non-empty one in either direction: an empty environment variable does not hide a saved value; a blank secret field keeps the stored
secret; a blank **required** setting keeps the saved one (use *Disconnect* to remove a configuration). Each field is labelled *saved here*, *from deployment (.env)*,
*default* or *missing*. Infrastructure and security settings (`DATABASE_URL`, `REDIS_URL`, `TOKEN_ENCRYPTION_KEY`, `APP_SECRET_KEY`, `AUTH_PROVIDER`, `GOOGLE_CLIENT_ID`,
the three safety switches) can never be set from the page.

## Credential storage
* Secrets are stored only as Fernet ciphertext (`integration_configs.secrets_ciphertext`); API responses carry a fingerprint and the source, never a value.
* The key is `TOKEN_ENCRYPTION_KEY` (generate: `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`), supplied by the deployment
  (Codespaces secret / secret manager / `.env`). **Saving a credential is refused** unless that key — or a random `APP_SECRET_KEY` of 32+ characters — is set; the built-in
  development secret is public, so no insecure fallback key is used to save anything. Production requires `TOKEN_ENCRYPTION_KEY`.
* If the key later changes, stored credentials cannot be decrypted; the card says so ("Saved credentials cannot be decrypted") instead of showing the fields as missing.
  Rotating the key means re-entering the credentials.
* Credentials are scoped to a **workspace** (`integration_configs.workspace_id`, unique per provider and workspace). A second workspace never sees or changes the first one's credentials.

## Audit and safety
Every save/test/enable/disconnect/OAuth/test-email writes an `activity_logs` entry with field *names* only. Connection tests have request timeouts, are rate limited
with the rest of the API, and never trust the browser's status. `GOOGLE_ADS_WRITES_ENABLED=false`, `AUTO_NEGATIVES_MODE=shadow`, `EMERGENCY_BRAKE_WRITE_MODE=simulate`
are untouched; nothing in this change creates or modifies a campaign.
