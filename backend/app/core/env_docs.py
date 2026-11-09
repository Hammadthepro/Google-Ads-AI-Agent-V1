"""Generates `.env.example` and `docs/ENVIRONMENT_VARIABLES.md` from the registry in env_audit.py.

    python -m app.cli docs-env          # rewrite both files
A unit test fails if either file is out of date, so documentation cannot drift from the code.
"""
from __future__ import annotations

from collections import OrderedDict
from pathlib import Path

from app.core.config import REPO_ROOT
from app.core.env_audit import EXTERNAL, REGISTRY, RETIRED

HEAD = """# Google Ads AI Agent — example configuration.
# Copy to .env (scripts/create-env.sh does this and generates APP_SECRET_KEY). NEVER commit .env.
# The app starts with every credential below left blank; features that need an integration say so until you configure it.
# Check your file any time (prints no values):   bash scripts/check-env.sh
# Fill blank values from your environment variables / Codespaces secrets: cd backend && python -m app.cli populate-env
#
# Credentials for Google Ads, GA4, Business Profile, Groq, Claude, Places and PageSpeed can also be entered on the
# Integrations page (encrypted in the database). Variables here are the fallback when nothing is saved there.
# Blank = use the default. Do not put quotes or spaces around values.
"""
NOTES = {
    "Safety": "# Real Google Ads writes stay OFF. Key 1 of 2 is GOOGLE_ADS_WRITES_ENABLED; key 2 is a Super Admin authorisation in the app.",
    "Infrastructure": ("# Local / Docker Compose Postgres is the default. For Supabase use the Session Pooler URL with SSL, e.g.\n"
                       "#   postgresql+psycopg://postgres.<project-ref>:<url-encoded-password>@aws-0-us-east-1.pooler.supabase.com:5432/postgres?sslmode=require\n"
                       "# Percent-encode special characters in the password (@ -> %40). COMPOSE_DATABASE_URL does the same for the Docker containers."),
    "Security": ("# APP_SECRET_KEY:       python -c \"import secrets; print(secrets.token_urlsafe(48))\"\n"
                 "# TOKEN_ENCRYPTION_KEY: python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\"\n"
                 "# Required (32+ random chars / a Fernet key) in production, and before saving credentials on the Integrations page."),
    "App login (Google)": ("# Google SIGN-IN uses the Identity Services ID-token flow: only GOOGLE_CLIENT_ID is needed (no client secret, no redirect URI).\n"
                           "# Create an OAuth *Web* client in Google Cloud, add your app origin under 'Authorised JavaScript origins', then set AUTH_PROVIDER=google (dev = email login for existing users, local only).\n"
                           "# This is NOT the Google Ads OAuth client below. BOOTSTRAP_ADMIN_EMAIL becomes the first Super Admin; everyone else is invited."),
    "Google Ads": ("# Google Ads API. NO developer token: Google sunset them on 2026-09-09. API access levels (Test / Explorer / Basic / Standard)\n"
                   "# belong to the Google Cloud project that owns the OAuth client below; see the Google Ads API Overview page in Cloud Console.\n"
                   "# A production account needs Explorer access or higher (apply there); test accounts work with Test access.\n"
                   "# OAuth client = where you authorise Ads (different from the sign-in client above).\n"
                   "# GOOGLE_ADS_LOGIN_CUSTOMER_ID = your MANAGER (MCC) account, only if you use one; GOOGLE_ADS_CUSTOMER_ID = a CLIENT account.\n"
                   "# 10 digits, dashes allowed. Do not put the manager ID in both."),
    "Website research": "# Optional. PageSpeed works without a key at a low quota.",
    "AI (Groq)": ("# Groq needs GROQ_API_KEY and GROQ_MODEL (exact Groq model ID, https://console.groq.com/docs/models).\n"
                  "# Which models you can use depends on your Groq plan (e.g. the Llama models are Enterprise-only for some accounts).\n"
                  "# The Integrations page lists what your key can use and sends a real JSON-mode request to each model before it is activated.\n"
                  "# Use a different model family for GROQ_CHALLENGER_MODEL than for GROQ_MODEL so the critic is independent."),
    "AI (Claude)": "# Optional fallback for the Claude integration; the model is chosen on the Integrations page (default claude-opus-5-5).",
    "Email": "# Alert emails are sent when SMTP_HOST is set; otherwise alerts stay in the app and are marked as not emailed.",
}
KEEP_DEFAULT = {"APP_ENV", "LOG_LEVEL", "GOOGLE_ADS_WRITES_ENABLED", "AUTO_NEGATIVES_MODE", "EMERGENCY_BRAKE_WRITE_MODE",
                "AUTH_PROVIDER", "REDIS_URL", "CELERY_TASK_ALWAYS_EAGER", "CORS_ORIGINS", "SESSION_COOKIE_SECURE",
                "SMTP_PORT", "SMTP_FROM", "CRAWLER_MAX_PAGES", "CRAWLER_MAX_DEPTH", "CRAWLER_TIMEOUT_SECONDS", "SESSION_TTL_HOURS",
                "RATE_LIMIT_PER_MINUTE", "AUTH_RATE_LIMIT_PER_MINUTE", "AUTO_NEGATIVES_DAILY_LIMIT"}
LOCAL_DB = "postgresql+psycopg://app:app@localhost:5432/google_ads_agent"


def render_example() -> str:
    groups: OrderedDict[str, list[dict]] = OrderedDict()
    for v in REGISTRY:
        groups.setdefault(v["group"], []).append(v)
    out = [HEAD]
    for g, vs in groups.items():
        out.append(f"# --- {g} " + "-" * max(3, 60 - len(g)))
        if g in NOTES:
            out.append(NOTES[g])
        for v in vs:
            name = v["name"]
            value = LOCAL_DB if name == "DATABASE_URL" else (v["default"] if name in KEEP_DEFAULT else "")
            out.append(f"{name}={value}")
        out.append("")
    return "\n".join(out)


def render_docs() -> str:
    g: OrderedDict[str, list[dict]] = OrderedDict()
    for v in REGISTRY:
        g.setdefault(v["group"], []).append(v)
    o = ["# Environment variables", "",
         "Generated from `backend/app/core/env_audit.py` by `python -m app.cli docs-env` (a unit test fails if this file is out of date).", "",
         "**Check your own file at any time (prints no values):** `bash scripts/check-env.sh` (or `cd backend && python -m app.cli check-env`). Exit code 1 means an error. "
         "`--file-only` audits just the file, `--json` is machine-readable.",
         "**Fill blank values from your environment variables / Codespaces secrets:** `cd backend && python -m app.cli populate-env` (names only are printed; existing values are kept; safety switches are never loosened).", "",
         "## How configuration is loaded",
         "1. `<repo root>/.env`, then `./.env` (e.g. `backend/.env`); later files win. This works wherever the backend is started from.",
         "2. Real environment variables (Docker Compose `env_file`, Codespaces/cloud secrets, your shell) override the files. If a variable is set in both, the environment wins — change it where it is set.",
         "3. Blank values mean “use the default”. Quotes and surrounding spaces are trimmed. `GAA_ENV_FILE=<path>` loads only that file; `GAA_ENV_FILE=none` loads none (tests).",
         "4. Credentials entered on the **Integrations** page are stored encrypted in the database and win over these variables. The page refuses to save credentials until `TOKEN_ENCRYPTION_KEY` (or a random 32+ character `APP_SECRET_KEY`) is set.", "",
         "## Google sign-in is not Google Ads OAuth",
         "| | App login (Google sign-in) | Google Ads / GA4 / Business Profile |", "|---|---|---|",
         "| Variables | `AUTH_PROVIDER=google`, `GOOGLE_CLIENT_ID`, `BOOTSTRAP_ADMIN_EMAIL`, `ALLOWED_LOGIN_DOMAINS` | `GOOGLE_ADS_CLIENT_ID`, `GOOGLE_ADS_CLIENT_SECRET`, `GOOGLE_ADS_REFRESH_TOKEN`, `GOOGLE_ADS_REDIRECT_URI`, `GOOGLE_ADS_LOGIN_CUSTOMER_ID`, `GOOGLE_ADS_CUSTOMER_ID` |",
         "| Flow | Google Identity Services returns an ID token; the backend verifies signature, issuer, audience, expiry, verified email and domain. **Only the client ID is needed** — no secret, no redirect URI. | Authorization-code flow with PKCE; needs client ID + secret + redirect URI and yields a refresh token. |",
         "| Grants | Access to this app only. Never grants Google Ads access. | Access to Google Ads data (read-only unless writes are separately enabled). |",
         "| Google Cloud setup | OAuth *Web* client, **Authorised JavaScript origins** = your app origin | OAuth client, **Authorised redirect URI** = `<app origin>/api/v1/integrations/google/callback`; Google Ads API enabled; API access for the Cloud project |",
         "Google sign-in is optional for local use: with `AUTH_PROVIDER=dev` (development only) users that already exist can sign in by email. `AUTH_PROVIDER=dev` is refused when `APP_ENV=production`.", "",
         "## Google Ads: no developer token",
         "Google **sunset developer tokens on 2026-09-09** ([official guide](https://developers.google.com/google-ads/api/docs/api-policy/developer-token)). Sending one is optional and ignored, so the app neither requires nor sends `GOOGLE_ADS_DEVELOPER_TOKEN`. "
         "What matters now is the **API access level of the Google Cloud project that owns your OAuth client** (Test, Explorer, Basic or Standard), shown on the Google Ads API Overview page in Cloud Console:",
         "- Test access works with Google Ads *test accounts* only. Calling a production account returns `CLOUD_PROJECT_NOT_APPROVED_FOR_PRODUCTION`; the Integrations page then tells you to apply for **Explorer access** (automatic) for that project.",
         "- The Google Ads API must be enabled in that project; the connection test says so if it is not.",
         "- The Google API does not expose the access level, so the dashboard shows what the real responses prove (API enabled; test-only / production account readable) and links to the Overview page.", "",
         "## Google Ads IDs",
         "`GOOGLE_ADS_LOGIN_CUSTOMER_ID` is the **manager (MCC)** account, sent as the `login-customer-id` header; a manager account is no longer required unless you manage several accounts through it. `GOOGLE_ADS_CUSTOMER_ID` is a **client** account (the one the connection test must read). Dashes are accepted and removed. Putting the manager ID in both makes the app query the manager itself, which has no campaigns (the audit warns).", "",
         "## Groq",
         "Groq is used as soon as `GROQ_API_KEY` and `GROQ_MODEL` are set — here or on the Integrations page (recommended, then run Test Connection). `GROQ_MODEL` drafts; `GROQ_CHALLENGER_MODEL` critiques (blank = same model). "
         "Model names are not hard-coded and **which models you can use depends on your Groq plan**: the Integrations page lists what your key can access and sends a real JSON-mode request to the primary and the challenger model. Use a different model family for the challenger so the critique is independent. See https://console.groq.com/docs/models.", "",
         "## Checklist"]
    for grp, vs in g.items():
        o += ["", f"### {grp}", "", "| Variable | Needed | Default | Purpose | Read by |", "|---|---|---|---|---|"]
        for v in vs:
            need = {"required": "**required**", "optional": "optional", "conditional": "when: " + v["when"]}[v["need"]]
            o.append(f"| `{v['name']}`{' 🔒' if v['secret'] else ''} | {need} | {('`' + v['default'] + '`') if v['default'] else '—'} | {v['purpose']} | {v['used_by']} |")
    o += ["", "🔒 = secret: never commit, never printed by the audit.", "", "### Retired — do nothing, remove from `.env`", "", "| Variable | Why |", "|---|---|"]
    for k, w in RETIRED.items():
        o.append(f"| `{k}` | {w} |")
    o += ["", "### Read outside the backend", "", "| Variable | Purpose |", "|---|---|"]
    for k, w in EXTERNAL.items():
        o.append(f"| `{k}` | {w} |")
    return "\n".join(o) + "\n"


def write_all() -> list[str]:
    (REPO_ROOT / ".env.example").write_text(render_example())
    (REPO_ROOT / "docs").mkdir(exist_ok=True)
    (REPO_ROOT / "docs" / "ENVIRONMENT_VARIABLES.md").write_text(render_docs())
    return [".env.example", "docs/ENVIRONMENT_VARIABLES.md"]


def is_current() -> bool:
    return (Path(REPO_ROOT / ".env.example").read_text() == render_example()
            and Path(REPO_ROOT / "docs" / "ENVIRONMENT_VARIABLES.md").read_text() == render_docs())
