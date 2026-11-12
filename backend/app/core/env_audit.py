"""Environment audit: every variable the application reads, whether yours is set, and whether it will actually work.

    python -m app.cli check-env [--env-file PATH] [--json] [--strict]

NEVER prints a value. Output says only: set / blank / default, plus a verdict. Exit code 1 when an error is found.

The REGISTRY below is the single list of variables. tests/unit/test_env_audit.py proves it stays in sync with
`Settings`, `.env.example` and every `os.environ` read, so a variable cannot be added to the code and forgotten here.
"""
from __future__ import annotations

import difflib
import os
import re
from pathlib import Path
from urllib.parse import urlparse

from app.core.config import REPO_ROOT, env_files

# -------------------------------------------------------------------------------------------------- registry
# need: "required" (app cannot run without it) | "conditional" (required when `when` holds) | "optional"
V = dict


def _v(name, group, need, purpose, used_by, *, secret=False, when="", default="", allowed=None):
    return V(name=name, group=group, need=need, purpose=purpose, used_by=used_by, secret=secret, when=when, default=default, allowed=allowed)


REGISTRY: list[dict] = [
    # ---- core
    _v("APP_ENV", "Core", "optional", "development | test | staging | production. production switches on the safety guards.", "core/config.py, main.py", default="development", allowed=["development", "test", "staging", "production"]),
    _v("LOG_LEVEL", "Core", "optional", "Python log level.", "main.py", default="INFO", allowed=["DEBUG", "INFO", "WARNING", "ERROR"]),
    # ---- safety
    _v("GOOGLE_ADS_WRITES_ENABLED", "Safety", "optional", "Key 1 of 2 for real Google Ads writes. Keep false until you are ready; a Super Admin must also authorise.", "services/safety.py, integrations_service.py", default="false", allowed=["true", "false"]),
    _v("AUTO_NEGATIVES_MODE", "Safety", "optional", "shadow = negatives are only proposed; active = safe negatives may be applied (also needs a system setting).", "services/safety.py, optimization.py", default="shadow", allowed=["shadow", "active"]),
    _v("AUTO_NEGATIVES_DAILY_LIMIT", "Safety", "optional", "Maximum automatic negatives per day.", "services/optimization.py", default="25"),
    _v("EMERGENCY_BRAKE_WRITE_MODE", "Safety", "optional", "simulate | disabled | real — what the emergency brake does to Google Ads.", "services/emergency.py, safety.py", default="simulate", allowed=["simulate", "disabled", "real"]),
    # ---- per integration modes
    _v("AUTH_PROVIDER", "Login", "optional", "dev = email login for users that already exist (local development only; never allowed in production); google = Google sign-in.", "adapters/registry.py, auth_users.py", default="dev", allowed=["dev", "google"]),
    # ---- infrastructure
    _v("DATABASE_URL", "Infrastructure", "required", "PostgreSQL connection string (local Postgres or the Supabase Session Pooler).", "db/session.py, migrations", secret=True),
    _v("COMPOSE_DATABASE_URL", "Infrastructure", "optional", "Docker Compose / Codespaces only: DATABASE_URL for the containers (blank = the bundled postgres).", "docker-compose.yml", secret=True),
    _v("REDIS_URL", "Infrastructure", "required", "Redis for Celery and rate limits.", "main.py, workers/celery_app.py", default="redis://localhost:6379/0"),
    _v("CELERY_TASK_ALWAYS_EAGER", "Infrastructure", "optional", "true runs background tasks inline (tests / no worker).", "workers/celery_app.py, workflow.py", default="false", allowed=["true", "false"]),
    _v("APP_SECRET_KEY", "Security", "conditional", "Signs sessions and, if TOKEN_ENCRYPTION_KEY is blank, derives the credential-encryption key. Required (32+ random chars) outside development.", "adapters/ads_oauth, config.py", secret=True, when="APP_ENV is not development/test, or you save credentials on the Integrations page"),
    _v("TOKEN_ENCRYPTION_KEY", "Security", "conditional", "Fernet key that encrypts credentials saved on the Integrations page. Required in production.", "adapters/ads_oauth/__init__.py", secret=True, when="production, or saving credentials on the Integrations page"),
    _v("CORS_ORIGINS", "Security", "optional", "Comma-separated browser origins allowed to call the API.", "main.py (cors_origins)", default="http://localhost:5173"),
    _v("SESSION_COOKIE_SECURE", "Security", "optional", "true sends the session cookie over HTTPS only. Must be true in production.", "main.py, auth_users.py", default="false", allowed=["true", "false"]),
    _v("SESSION_TTL_HOURS", "Security", "optional", "Login session lifetime.", "services/auth_service.py", default="12"),
    _v("RATE_LIMIT_PER_MINUTE", "Security", "optional", "API requests per minute per client.", "main.py", default="300"),
    _v("AUTH_RATE_LIMIT_PER_MINUTE", "Security", "optional", "Login attempts per minute per client.", "main.py", default="30"),
    # ---- app login (Google sign-in)
    _v("BOOTSTRAP_ADMIN_EMAIL", "App login (Google)", "conditional", "The first Google account allowed to sign in; it becomes Super Admin. Everyone else must be invited.", "services/auth_service.py", when="AUTH_PROVIDER=google and no Super Admin exists yet"),
    _v("ALLOWED_LOGIN_DOMAINS", "App login (Google)", "optional", "Comma-separated email domains allowed to sign in (blank = any invited account).", "adapters/auth (allowed_login_domains)"),
    _v("GOOGLE_CLIENT_ID", "App login (Google)", "conditional", "OAuth Web client ID for Google SIGN-IN (ID-token flow). The only Google sign-in credential the app uses.", "adapters/auth/__init__.py, auth_users.py", when="AUTH_PROVIDER=google"),
    # ---- google ads
    _v("GOOGLE_ADS_CLIENT_ID", "Google Ads", "conditional", "OAuth client ID used to authorise Google Ads (and GA4 / Business Profile). NOT the sign-in client.", "integrations_service.py", when="real Google Ads"),
    _v("GOOGLE_ADS_CLIENT_SECRET", "Google Ads", "conditional", "OAuth client secret for the Google Ads client above.", "integrations_service.py", secret=True, when="real Google Ads"),
    _v("GOOGLE_ADS_REDIRECT_URI", "Google Ads", "optional", "OAuth redirect URI registered for the Ads client. Blank = <app origin>/api/v1/integrations/google/callback.", "integrations_service.py (oauth_redirect_uri)"),
    _v("GOOGLE_ADS_REFRESH_TOKEN", "Google Ads", "conditional", "Refresh token for the Google user that can access the accounts (or use Connect with Google on the Integrations page).", "integrations_service.py", secret=True, when="real Google Ads"),
    _v("GOOGLE_ADS_LOGIN_CUSTOMER_ID", "Google Ads", "optional", "OPTIONAL default manager (MCC) ID, 10 digits. The manager is normally chosen on the Integrations page and stored in the database; this is only a pre-selection for new deployments.", "integrations_service.py, adapters/google_ads/real.py"),
    _v("GOOGLE_PLACES_API_KEY", "Website research", "optional", "Places API (New) key for competitor and Business Profile research.", "integrations_service.py", secret=True),
    _v("PAGESPEED_API_KEY", "Website research", "optional", "PageSpeed Insights key (works without one at a low quota).", "integrations_service.py", secret=True),
    _v("TAVILY_API_KEY", "Research", "optional", "Tavily web search key (optional; the Integrations page is preferred).", "integrations_service.py", secret=True),
    _v("GEOAPIFY_API_KEY", "Research", "optional", "Geoapify places/geocoding key (optional; the Integrations page is preferred).", "integrations_service.py", secret=True),
    # ---- AI
    _v("GROQ_API_KEY", "AI (Groq)", "conditional", "Groq API key (starts with gsk_).", "integrations_service.py", secret=True, when="Groq is used"),
    _v("GROQ_MODEL", "AI (Groq)", "conditional", "Groq model that writes drafts and explanations. Exact Groq model ID.", "adapters/ai/__init__.py", when="Groq is used"),
    _v("GROQ_CHALLENGER_MODEL", "AI (Groq)", "optional", "Groq model that critiques plans. Blank = GROQ_MODEL (no independent second opinion).", "adapters/ai/__init__.py"),
    _v("ANTHROPIC_API_KEY", "AI (Claude)", "optional", "Fallback key for the Claude integration (model is chosen on the Integrations page; default claude-opus-5-5).", "integrations_service.py", secret=True),
    # ---- email
    _v("SMTP_HOST", "Email", "conditional", "SMTP server for alert emails.", "adapters/notifications", when="alert emails are wanted"),
    _v("SMTP_PORT", "Email", "optional", "SMTP port (STARTTLS).", "adapters/notifications", default="587"),
    _v("SMTP_USER", "Email", "optional", "SMTP username (blank = no login).", "adapters/notifications"),
    _v("SMTP_PASSWORD", "Email", "optional", "SMTP password.", "adapters/notifications", secret=True),
    _v("SMTP_FROM", "Email", "optional", "From address for alert emails.", "adapters/notifications", default="alerts@localhost"),
    # ---- crawler
    _v("CRAWLER_MAX_PAGES", "Mock & crawler", "optional", "Pages crawled per website.", "services/crawler_service.py", default="40"),
    _v("CRAWLER_MAX_DEPTH", "Mock & crawler", "optional", "Link depth crawled.", "services/crawler_service.py", default="2"),
    _v("CRAWLER_TIMEOUT_SECONDS", "Mock & crawler", "optional", "Per-request crawler timeout.", "adapters/crawler/__init__.py", default="10"),
]
BY_NAME = {v["name"]: v for v in REGISTRY}

# Variables that older versions (and some .env files) still contain. They do nothing; the audit says so.
RETIRED = {
    "GOOGLE_ADS_DEVELOPER_TOKEN": "Not needed: Google sunset developer tokens on 2026-09-09. API access is now tied to the Google Cloud project that owns your OAuth client (see the Google Ads API overview page in Cloud Console). The app no longer sends or requires it. Remove it from .env.",
    "GOOGLE_CLIENT_SECRET": "Not used: sign-in verifies Google ID tokens with the client ID only (no authorization-code exchange). Remove it from .env. Do not confuse it with GOOGLE_ADS_CLIENT_SECRET.",
    "GOOGLE_REDIRECT_URI": "Not used: Google sign-in has no redirect (the Google button returns a token to the page). The Ads redirect is GOOGLE_ADS_REDIRECT_URI.",
    "KEYWORD_RESEARCH_MODE": "Not used: Keyword Planner follows the Google Ads connection on the Integrations page.",
    "INTEGRATION_MODE": "Removed: there is no mock mode any more. Every integration is real; configure it on the Integrations page.",
    "GOOGLE_ADS_MODE": "Removed: the app always talks to the real Google Ads API once it is configured; writes stay gated by the two safety keys.",
    "PLACES_MODE": "Removed: Places is used as soon as its API key is saved on the Integrations page.",
    "PAGESPEED_MODE": "Removed: PageSpeed is used as soon as it is configured on the Integrations page.",
    "NOTIFIER_MODE": "Removed: alert emails are sent whenever SMTP_HOST is set.",
    "CRAWLER_MODE": "Removed: the website crawler always fetches real pages (SSRF-guarded).",
    "AI_PROVIDER": "Removed: Groq and Claude are used as soon as they are configured on the Integrations page.",
    "GROQ_ENABLED": "Removed: configure Groq on the Integrations page (or GROQ_API_KEY / GROQ_MODEL).",
    "MOCK_SEED": "Removed with the simulated world.",
    "GOOGLE_ADS_CUSTOMER_ID": "Removed: client accounts are discovered from your manager (MCC) and chosen in the application. Remove it from .env.",
    "TEST_MODE": "Removed with the simulated clock and mock controls.",
}
# Read by the frontend dev server / Docker tooling, not by the backend.
EXTERNAL = {"VITE_API_TARGET": "Vite dev server: where /api is proxied (default http://localhost:8000).", "CODESPACES": "Set by GitHub Codespaces; read by scripts/start-demo.sh.",
            "GAA_ENV_FILE": "Overrides which .env file the backend loads (none = no file; used by tests)."}

# Groq chat models seen on https://console.groq.com/docs/models and on a real key (2026-11-08). Availability depends on the Groq plan; the
# Integrations page verifies the exact name live against your key (GET /openai/v1/models); this list only drives a soft warning.
GROQ_KNOWN_CHAT = {"llama-3.1-8b-instant", "llama-3.3-70b-versatile", "openai/gpt-oss-120b", "openai/gpt-oss-20b", "qwen/qwen3.8-27b"}
GROQ_NOT_CHAT = ("whisper", "orpheus", "prompt-guard", "safeguard", "tts", "guard")
PLACEHOLDER = re.compile(r"^(<.*>|changeme|change-me|your[-_ ].*|xxx+|todo|example|replace.*|\.\.\.)$", re.I)
TRUE = {"true", "1", "yes", "on"}
FALSE = {"false", "0", "no", "off", ""}


# -------------------------------------------------------------------------------------------------- loading
def _parse_env_file(path: Path) -> tuple[dict[str, str], list[str]]:
    """KEY=VALUE pairs of a .env file (last wins, like python-dotenv) and the list of keys defined more than once."""
    values: dict[str, str] = {}
    dup: list[str] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[7:]
        k, v = line.split("=", 1)
        k, v = k.strip(), v.strip()
        if " #" in v and not v.startswith(("'", '"')):
            v = v.split(" #", 1)[0].strip()
        if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
            v = v[1:-1]
        if k in values:
            dup.append(k)
        values[k] = v
    return values, dup


def load(env_file: str | None = None, environ: dict | None = None) -> tuple[dict[str, str], list[str], list[str]]:
    """Effective environment as the app sees it: .env file(s) then real environment variables on top. Returns
    (values, files_read, duplicate_keys)."""
    values: dict[str, str] = {}
    files: list[str] = []
    dups: list[str] = []
    paths = [env_file] if env_file else list(env_files())
    for p in paths:
        path = Path(p)
        if path.is_file():
            vals, d = _parse_env_file(path)
            values.update(vals)
            dups += d
            files.append(str(path))
    env = os.environ if environ is None else environ
    for k in list(BY_NAME) + list(RETIRED) + list(EXTERNAL):
        if env.get(k) not in (None, ""):
            values[k] = env[k]
    return values, files, dups


# -------------------------------------------------------------------------------------------------- audit
def _digits(v: str) -> str:
    return re.sub(r"[\s-]", "", v or "")


def audit(values: dict[str, str], *, files: list[str] | None = None, duplicates: list[str] | None = None) -> dict:
    """Return {rows, problems, summary}. Values are inspected, never returned."""
    g = lambda k: (values.get(k) or "").strip()  # noqa: E731
    flag = lambda k: g(k).lower() in TRUE  # noqa: E731
    env = g("APP_ENV") or "development"
    prod = env == "production"
    problems: list[dict] = []
    per_var: dict[str, list[dict]] = {}

    def add(level: str, var: str, msg: str, action: str = ""):
        item = {"level": level, "var": var, "message": msg, "action": action}
        problems.append(item)
        per_var.setdefault(var, []).append(item)

    # -- structural
    for k in sorted(set(duplicates or [])):
        add("error", k, "Defined more than once in the file; only the last value is used.", "Delete the duplicate lines.")
    known = set(BY_NAME) | set(RETIRED) | set(EXTERNAL)
    for k in sorted(values):
        if k in known:
            continue
        guess = difflib.get_close_matches(k, list(BY_NAME), n=1, cutoff=0.75)
        add("warn", k, "Not read by the application (ignored)." + (f" Did you mean {guess[0]}?" if guess else ""), "Remove it or fix the spelling.")
    for k, why in RETIRED.items():
        if g(k):
            add("warn", k, why, "Remove it from .env.")
    # -- format of each set value
    for v in REGISTRY:
        k, val = v["name"], g(v["name"])
        if not val:
            continue
        if PLACEHOLDER.match(val):
            add("error", k, "Still contains a placeholder, not a real value.", "Replace it or leave the variable blank.")
            continue
        if v["allowed"] and val.lower() not in [a.lower() for a in v["allowed"]]:
            add("error", k, f"Invalid value; allowed: {', '.join(v['allowed'])}.", "Fix the value.")
        if k in ("AUTO_NEGATIVES_DAILY_LIMIT", "SESSION_TTL_HOURS", "RATE_LIMIT_PER_MINUTE", "AUTH_RATE_LIMIT_PER_MINUTE", "CRAWLER_MAX_PAGES",
                 "CRAWLER_MAX_DEPTH", "SMTP_PORT") and not val.isdigit():
            add("error", k, "Must be a whole number.", "Fix the value.")
        if k == "CRAWLER_TIMEOUT_SECONDS":
            try:
                float(val)
            except ValueError:
                add("error", k, "Must be a number.", "Fix the value.")
    # -- safety posture
    if flag("GOOGLE_ADS_WRITES_ENABLED"):
        add("warn", "GOOGLE_ADS_WRITES_ENABLED", "Key 1 of 2 for REAL Google Ads writes is ON. Writes still need Validated-execution mode and a Super Admin authorisation.", "Set false unless you are deliberately enabling writes.")
    if g("EMERGENCY_BRAKE_WRITE_MODE").lower() == "real":
        add("warn", "EMERGENCY_BRAKE_WRITE_MODE", "The emergency brake may change real campaigns.", "Use simulate.")
    if g("AUTO_NEGATIVES_MODE").lower() == "active":
        add("warn", "AUTO_NEGATIVES_MODE", "Safe negative keywords may be applied automatically (a system setting must also allow it).", "Use shadow until reviewed.")
    # -- security / production
    secret, tek = g("APP_SECRET_KEY"), g("TOKEN_ENCRYPTION_KEY")
    if tek:
        try:
            from cryptography.fernet import Fernet
            Fernet(tek.encode())
        except Exception:  # noqa: BLE001
            add("error", "TOKEN_ENCRYPTION_KEY", "Not a valid Fernet key (32 url-safe base64 bytes).", 'python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"')
    weak = not secret or secret.startswith("dev-") or len(secret) < 32
    if prod:
        if weak:
            add("error", "APP_SECRET_KEY", "Production needs a random secret of 32+ characters.", 'python -c "import secrets; print(secrets.token_urlsafe(48))"')
        if not tek:
            add("error", "TOKEN_ENCRYPTION_KEY", "Required in production.", "Generate a Fernet key (see .env.example).")
        if g("AUTH_PROVIDER").lower() in ("", "dev", "mock"):
            add("error", "AUTH_PROVIDER", "dev login is not allowed in production.", "Set AUTH_PROVIDER=google.")
        if not flag("SESSION_COOKIE_SECURE"):
            add("error", "SESSION_COOKIE_SECURE", "Must be true in production (HTTPS-only cookie).", "Set true.")
    elif weak and not tek:
        add("warn", "APP_SECRET_KEY", "Blank or development default: the app runs, but the Integrations page will REFUSE to save credentials (the default key is public).", "Set TOKEN_ENCRYPTION_KEY, or a random APP_SECRET_KEY, before saving real credentials.")
    if g("CORS_ORIGINS"):
        for o in [x.strip() for x in g("CORS_ORIGINS").split(",") if x.strip()]:
            p = urlparse(o)
            if p.scheme not in ("http", "https") or not p.netloc or p.path not in ("", "/") or o.endswith("/"):
                add("error", "CORS_ORIGINS", f"'{o}' is not a bare origin like https://app.example.com (no path or trailing slash).", "Fix the entry.")
            elif prod and p.hostname in ("localhost", "127.0.0.1"):
                add("warn", "CORS_ORIGINS", "Production allows a localhost origin.", "Use your real app origin.")
    # -- database / redis
    for k, schemes in (("DATABASE_URL", ("postgresql", "postgresql+psycopg", "postgres")), ("REDIS_URL", ("redis", "rediss"))):
        if not g(k):
            if k == "DATABASE_URL":
                add("warn", k, "Blank: the built-in local default (app:app@localhost) is used.", "Set it for Supabase or any non-default database.")
            continue
        p = urlparse(g(k))
        if p.scheme not in schemes or not p.hostname:
            add("error", k, f"Not a valid {k.split('_')[0].lower()} URL.", "Check the scheme and host.")
        elif k == "DATABASE_URL" and "supabase" in (p.hostname or "") and "sslmode" not in (p.query or ""):
            add("warn", k, "Supabase requires SSL; add ?sslmode=require.", "Append ?sslmode=require.")
        elif k == "DATABASE_URL" and p.password and any(c in g(k)[len(p.scheme) + 3:].rsplit("@", 1)[0].split(":", 1)[-1] for c in "/#?"):
            add("warn", k, "The password contains / # or ? unencoded, which breaks the URL.", "Percent-encode special characters (@ → %40, / → %2F).")
    # -- app login
    ap = (g("AUTH_PROVIDER") or "dev").lower()
    ap = "dev" if ap == "mock" else ap
    cid, ads_cid = g("GOOGLE_CLIENT_ID"), g("GOOGLE_ADS_CLIENT_ID")
    if ap == "google":
        if not cid:
            add("error", "GOOGLE_CLIENT_ID", "AUTH_PROVIDER=google needs the Google sign-in client ID or nobody can sign in.", "Create an OAuth Web client in Google Cloud and paste its client ID.")
        if not g("BOOTSTRAP_ADMIN_EMAIL"):
            add("warn", "BOOTSTRAP_ADMIN_EMAIL", "No bootstrap admin: only users already in the database can sign in.", "Set it to the first admin's Google email (or run python -m app.cli create-super-admin EMAIL).")
    else:
        if cid:
            add("info", "GOOGLE_CLIENT_ID", "Set, but ignored while AUTH_PROVIDER=dev (email login for existing users).", "Set AUTH_PROVIDER=google to use Google sign-in.")
        if g("BOOTSTRAP_ADMIN_EMAIL"):
            add("info", "BOOTSTRAP_ADMIN_EMAIL", "Only used with AUTH_PROVIDER=google.", "")
    if cid and not cid.endswith(".apps.googleusercontent.com"):
        add("error", "GOOGLE_CLIENT_ID", "A Google OAuth client ID ends with .apps.googleusercontent.com.", "Copy the client ID, not the secret or project number.")
    if g("BOOTSTRAP_ADMIN_EMAIL") and not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", g("BOOTSTRAP_ADMIN_EMAIL")):
        add("error", "BOOTSTRAP_ADMIN_EMAIL", "Not a valid email address.", "Fix it.")
    for d in [x.strip() for x in g("ALLOWED_LOGIN_DOMAINS").split(",") if x.strip()]:
        if "@" in d or " " in d or "." not in d:
            add("error", "ALLOWED_LOGIN_DOMAINS", f"'{d}' must be a bare domain like example.com (no @).", "Fix the entry.")
    if cid and ads_cid and cid == ads_cid:
        add("warn", "GOOGLE_ADS_CLIENT_ID", "Same OAuth client as Google sign-in. The app treats them as separate: sign-in needs an authorised JavaScript origin, Ads needs an authorised redirect URI and scopes.", "Prefer two separate OAuth clients.")
    # -- google ads
    ads_vars = ("GOOGLE_ADS_CLIENT_ID", "GOOGLE_ADS_CLIENT_SECRET", "GOOGLE_ADS_REFRESH_TOKEN")
    if any(g(k) for k in ads_vars):
        for k in ads_vars:
            if not g(k):
                add("warn", k, "Google Ads credentials are partly set; this one is missing, so the connection test will stop at 'Credentials present'.", "Provide it, or enter all four on the Integrations page instead.")
    if ads_cid and not ads_cid.endswith(".apps.googleusercontent.com"):
        add("error", "GOOGLE_ADS_CLIENT_ID", "A Google OAuth client ID ends with .apps.googleusercontent.com.", "Copy the client ID from Google Cloud → Credentials.")
    mcc = _digits(g("GOOGLE_ADS_LOGIN_CUSTOMER_ID"))
    if g("GOOGLE_ADS_LOGIN_CUSTOMER_ID") and not (mcc.isdigit() and len(mcc) == 10):
        add("error", "GOOGLE_ADS_LOGIN_CUSTOMER_ID", "A Google Ads customer ID is exactly 10 digits (dashes allowed: 123-456-7890).", "Copy it from the Google Ads account switcher, or leave it blank and choose the manager on the Integrations page.")
    # -- groq
    groq_in_use = bool(g("GROQ_API_KEY") or g("GROQ_MODEL"))
    if groq_in_use:
        if not g("GROQ_API_KEY"):
            add("error", "GROQ_API_KEY", "Groq is configured but there is no API key; the model name alone does nothing.", "Create a key at console.groq.com → API Keys (or enter it on the Integrations page).")
        elif not g("GROQ_API_KEY").startswith("gsk_"):
            add("warn", "GROQ_API_KEY", "Groq keys start with gsk_.", "Check you pasted the right key.")
        if not g("GROQ_MODEL"):
            add("error", "GROQ_MODEL", "No model configured.", "Use an exact Groq model ID your key can access (the Integrations page lists them), e.g. openai/gpt-oss-120b.")
    for k in ("GROQ_MODEL", "GROQ_CHALLENGER_MODEL"):
        m = g(k)
        if not m:
            continue
        if any(x in m.lower() for x in GROQ_NOT_CHAT):
            add("error", k, f"'{m}' is not a chat model (speech/safety/guard models cannot write ad plans).", "Use a chat model such as openai/gpt-oss-120b or qwen/qwen3.8-27b.")
        elif m not in GROQ_KNOWN_CHAT:
            add("warn", k, f"'{m}' is not one of the production chat models verified on 2026-11-08 ({', '.join(sorted(GROQ_KNOWN_CHAT))}). It may be valid, preview, or retired.", "Run Test Connection on the Integrations page: it checks the exact name against your key.")
    if g("GROQ_CHALLENGER_MODEL") and g("GROQ_CHALLENGER_MODEL") == g("GROQ_MODEL"):
        add("info", "GROQ_CHALLENGER_MODEL", "Same model as GROQ_MODEL: the critic shares the drafter's blind spots.", "Prefer a different model family (e.g. openai/gpt-oss-120b and qwen/qwen3.8-27b).")
    if groq_in_use and not g("GROQ_CHALLENGER_MODEL"):
        add("info", "GROQ_CHALLENGER_MODEL", "Blank: the challenger falls back to GROQ_MODEL.", "Optional but recommended: set a different model.")
    # -- email
    if (g("SMTP_USER") or g("SMTP_PASSWORD")) and not g("SMTP_HOST"):
        add("error", "SMTP_HOST", "SMTP credentials are set but there is no SMTP host, so no alert email can be sent.", "Set SMTP_HOST.")
    # -- rows
    rows = []
    for v in REGISTRY:
        k = v["name"]
        val = g(k)
        errs = [p for p in per_var.get(k, []) if p["level"] == "error"]
        warns = [p for p in per_var.get(k, []) if p["level"] == "warn"]
        needed = v["need"] == "required" or (v["need"] == "conditional" and _needed(k, g, flag, ap, groq_in_use, prod))
        rows.append({"name": k, "group": v["group"], "need": v["need"], "needed_now": needed, "when": v["when"], "purpose": v["purpose"], "secret": v["secret"],
                     "configured": "set" if val else ("default" if v["default"] else "blank"), "default": v["default"], "used": True, "used_by": v["used_by"],
                     "status": "error" if errs else "warn" if warns else "ok", "notes": [p["message"] for p in per_var.get(k, [])], "actions": [p["action"] for p in per_var.get(k, []) if p["action"]]})
    for k, why in RETIRED.items():
        rows.append({"name": k, "group": "Retired", "need": "unused", "needed_now": False, "when": "", "purpose": why, "secret": "SECRET" in k, "configured": "set" if g(k) else "blank",
                     "default": "", "used": False, "used_by": "—", "status": "warn" if g(k) else "ok", "notes": [why] if g(k) else [], "actions": ["Remove from .env."] if g(k) else []})
    counts = {lvl: sum(1 for p in problems if p["level"] == lvl) for lvl in ("error", "warn", "info")}
    return {"files": files or [], "rows": rows, "problems": problems, "counts": counts, "ok": counts["error"] == 0,
            "mode": {"app_env": env, "auth_provider": ap, "real_writes_key1": flag("GOOGLE_ADS_WRITES_ENABLED"),
                     "emergency_mode": g("EMERGENCY_BRAKE_WRITE_MODE") or "simulate"}}


def _needed(k: str, g, flag, ap: str, groq: bool, prod: bool) -> bool:
    if k == "GOOGLE_CLIENT_ID":
        return ap == "google"
    if k == "BOOTSTRAP_ADMIN_EMAIL":
        return False  # needed only before the first admin exists: reported as a warning when AUTH_PROVIDER=google
    if k in ("GROQ_API_KEY", "GROQ_MODEL"):
        return groq
    if k == "SMTP_HOST":
        return False
    if k in ("APP_SECRET_KEY", "TOKEN_ENCRYPTION_KEY"):
        return prod
    if k.startswith("GOOGLE_ADS_"):
        return False  # entered on the Integrations page, or reported as 'partly set'
    return False


# -------------------------------------------------------------------------------------------------- populate .env
# Never copied from the environment unless the value is the safe one: the test environment stays a test environment.
SAFE_VALUES = {"GOOGLE_ADS_WRITES_ENABLED": {"false"}, "EMERGENCY_BRAKE_WRITE_MODE": {"simulate", "disabled"}, "AUTO_NEGATIVES_MODE": {"shadow"},
               "AUTH_PROVIDER": {"dev"}, "APP_ENV": {"development", "test"}}


def _quote(v: str) -> str:
    """Write a value so python-dotenv AND docker compose's env_file read it back unchanged."""
    if v == "" or not re.search(r"[\s#$\"'`\\]", v):
        return v
    return f"'{v}'" if "'" not in v else '"' + v.replace("\\", "\\\\").replace('"', '\\"').replace("$", "\\$") + '"'


def populate(env_path: str | Path, environ: dict | None = None, *, dry_run: bool = False) -> dict:
    """Fill blank or missing variables in `.env` from the process environment. NAMES ONLY are returned or printed.

    - an existing non-blank value in the file always wins (reported under `differs` if the environment disagrees)
    - safety switches are only copied when the value is the safe one
    - duplicate keys in the file are collapsed to one line (the effective value)
    - retired / unknown / external variables are never copied
    """
    env = os.environ if environ is None else environ
    path = Path(env_path)
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    parsed: dict[str, list[tuple[int, str]]] = {}
    for i, raw in enumerate(lines):
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k, v = k.strip().removeprefix("export ").strip(), v.strip()
        if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
            v = v[1:-1]
        parsed.setdefault(k, []).append((i, v))
    out = {"copied": [], "kept": [], "differs": [], "refused": [], "unavailable": [], "deduplicated": [], "added": []}
    drop: set[int] = set()
    replace: dict[int, str] = {}
    append: list[str] = []
    for var in REGISTRY:
        name = var["name"]
        occ = parsed.get(name, [])
        env_val = (env.get(name) or "").strip()
        file_vals = [v for _, v in occ]
        effective_file = next((v for v in reversed(file_vals) if v), "")
        if len(occ) > 1:
            out["deduplicated"].append(name)
            drop.update(i for i, _ in occ[1:])
            replace[occ[0][0]] = f"{name}={_quote(effective_file)}"
        if effective_file:
            out["kept"].append(name)
            if env_val and env_val != effective_file:
                out["differs"].append(name)
            continue
        if not env_val:
            out["unavailable"].append(name)
            continue
        if name in SAFE_VALUES and env_val.lower() not in SAFE_VALUES[name]:
            out["refused"].append(name)
            continue
        line = f"{name}={_quote(env_val)}"
        if occ:
            replace[occ[0][0]] = line
        else:
            append.append(line)
            out["added"].append(name)
        out["copied"].append(name)
    if not dry_run and (replace or drop or append):
        new = [replace.get(i, l) for i, l in enumerate(lines) if i not in drop]
        if append:
            new += ["", "# --- added from the environment by `python -m app.cli populate-env` ---"] + append
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text("\n".join(new) + "\n", encoding="utf-8")
        os.chmod(tmp, 0o600)
        tmp.replace(path)
    return out


def render_populate(res: dict, path: str) -> str:
    def row(label, names):
        return f"  {label:34} {len(names):2}  {', '.join(names) if names else '—'}"
    return "\n".join([f"populate-env → {path} (names only; no values are printed)",
                      row("copied from the environment", res["copied"]), row("already in the file (kept)", res["kept"]),
                      row("file value differs from env (file kept)", res["differs"]), row("refused (not the safe value)", res["refused"]),
                      row("duplicates collapsed", res["deduplicated"]), row("unavailable (blank everywhere)", res["unavailable"])])


# -------------------------------------------------------------------------------------------------- output
def render(report: dict, *, brief: bool = False) -> str:
    out = []
    m = report["mode"]
    out.append(f"Environment audit — app env: {m['app_env']} · login: {m['auth_provider']} · "
               f"real-write key 1: {'ON' if m['real_writes_key1'] else 'off'} · emergency brake: {m['emergency_mode']}")
    out.append("File(s) read: " + (", ".join(report["files"]) or "none (process environment only)"))
    if not brief:
        out.append("")
        out.append(f"{'VARIABLE':32} {'NEED':12} {'CONFIGURED':11} {'STATUS':7} NOTES")
        for r in report["rows"]:
            note = ("; ".join(r["notes"]))[:100]
            out.append(f"{r['name']:32} {r['need']:12} {r['configured']:11} {r['status']:7} {note}")
    out.append("")
    for lvl, label in (("error", "ERRORS (fix these)"), ("warn", "WARNINGS"), ("info", "NOTES")):
        items = [p for p in report["problems"] if p["level"] == lvl]
        if items:
            out.append(label)
            for p in items:
                out.append(f"  [{lvl}] {p['var']}: {p['message']}" + (f"\n          → {p['action']}" if p["action"] else ""))
    c = report["counts"]
    out.append("")
    out.append(f"Result: {'OK' if report['ok'] else 'NOT OK'} — {c['error']} error(s), {c['warn']} warning(s), {c['info']} note(s). Values are never printed.")
    return "\n".join(out)


def run(env_file: str | None = None, *, as_json: bool = False, brief: bool = False, strict: bool = False, file_only: bool = False,
        environ: dict | None = None) -> int:
    """file_only=True audits just the file; otherwise real environment variables are layered on top, exactly as the app sees them."""
    import json
    values, files, dups = load(env_file, environ={} if file_only else environ)
    report = audit(values, files=files, duplicates=dups)
    print(json.dumps(report, indent=2) if as_json else render(report, brief=brief))
    if not report["ok"] or (strict and report["counts"]["warn"]):
        return 1
    return 0


__all__ = ["REGISTRY", "BY_NAME", "RETIRED", "EXTERNAL", "audit", "load", "render", "run", "REPO_ROOT"]
