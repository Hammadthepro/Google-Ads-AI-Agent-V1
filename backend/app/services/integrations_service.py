"""API & Account Integrations: encrypted credential store, operating mode, stepwise REAL connection tests.

Rules
- Secrets are Fernet-encrypted at rest and are NEVER returned; the API shows fingerprints only.
- A connection is "Connected" only after a genuine successful request to the provider. Never inferred.
- Each test returns ordered diagnostic steps; error text is redacted before it is stored or returned.
- Credentials saved here are used by the adapters (DB first, environment variable as fallback).
"""
from __future__ import annotations

import json
import re
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.adapters.ads_oauth import decrypt_token, encrypt_token
from app.core.config import get_settings
from app.core.errors import NotFound, ValidationFailed
from app.core.util import fingerprint, redact
from app.db.session import session_scope
from app.models import IntegrationConfig

# --------------------------------------------------------------------------------------------- catalogue
GOOGLE_SCOPES = {
    "google_ads": ["https://www.googleapis.com/auth/adwords"],
    "ga4": ["https://www.googleapis.com/auth/analytics.readonly"],
    "gbp": ["https://www.googleapis.com/auth/business.manage"],
}
ADS_API_VERSION = "v26"  # matches the installed google-ads library default

PROVIDERS: dict[str, dict] = {
    "google_ads": {
        "label": "Google Ads API", "group": "Google",
        "description": "Account discovery, reporting and Keyword Planner. Campaign writes stay disabled unless explicitly authorised twice.",
        "settings": {"client_id": "OAuth client ID", "login_customer_id": "Manager (MCC) customer ID",
                     "customer_id": "Client (test) account customer ID"},
        "secrets": {"client_secret": "OAuth client secret", "refresh_token": "Refresh token"},
        # No developer token: Google sunset developer tokens on 2026-09-09; API access levels now belong to the Cloud project
        # that owns the OAuth client.
        "required": ["client_id", "client_secret", "refresh_token"],
        "subtests": [("auth", "OAuth authentication"), ("cloud_access", "Google Cloud API access"), ("manager", "Manager account"),
                     ("client", "Client account")],
        "required_subtests": ["auth", "cloud_access", "client"], "targets": {"manager": "Test manager account", "client": "Test client account"},
        "oauth": True, "scopes": GOOGLE_SCOPES["google_ads"],
        "docs": "https://developers.google.com/google-ads/api/docs/api-policy/developer-token",
    },
    "ga4": {
        "label": "Google Analytics 4", "group": "Google",
        "description": "Supporting traffic and key-event data (Analytics Data API). Never mixed into Ads conversion counts.",
        "settings": {"property_id": "GA4 property ID"}, "secrets": {"refresh_token": "Refresh token"},
        "required": ["refresh_token"], "oauth": True, "scopes": GOOGLE_SCOPES["ga4"], "uses_google_client": True,
        "docs": "https://developers.google.com/analytics/devguides/reporting/data/v1",
    },
    "gbp": {
        "label": "Google Business Profile", "group": "Google",
        "description": "Business Profile data. Requires Google's Business Profile API access approval for the Cloud project.",
        "settings": {"account_name": "Business Profile account (accounts/…)"}, "secrets": {"refresh_token": "Refresh token"},
        "required": ["refresh_token"], "oauth": True, "scopes": GOOGLE_SCOPES["gbp"], "uses_google_client": True,
        "docs": "https://developers.google.com/my-business/content/prereqs",
    },
    "keyword_planner": {
        "label": "Keyword Planner", "group": "Google",
        "description": "Uses the Google Ads authorisation and client account above (no separate credentials). The test asks Keyword Planner for real keyword ideas (read-only).",
        "settings": {}, "secrets": {}, "required": [], "depends_on": "google_ads",
        "docs": "https://developers.google.com/google-ads/api/docs/keyword-planning/overview",
    },
    "groq": {
        "label": "Groq (internal AI)", "group": "AI",
        "description": "The internal AI used for drafting and for the first side of the AI competition.",
        "settings": {"model": "Model", "challenger_model": "Challenger model (optional)"},
        "secrets": {"api_key": "API key"}, "required": ["model", "api_key"],
        "subtests": [("auth", "API key"), ("primary", "Primary model"), ("challenger", "Challenger model")],
        "required_subtests": ["auth", "primary"], "optional_subtests": ["challenger"],
        "targets": {"primary": "Test primary model", "challenger": "Test challenger model"},
        "docs": "https://console.groq.com/docs/models",
    },
    "claude": {
        "label": "Claude (Anthropic)", "group": "AI",
        "description": "The independent second AI: drafts the same task and cross-challenges the internal AI's plan.",
        "settings": {"model": "Model"}, "secrets": {"api_key": "API key"}, "required": ["model", "api_key"],
        "default_settings": {"model": "claude-opus-5-5"},
        "docs": "https://platform.claude.com/docs/en/api/overview",
    },
    "pagespeed": {
        "label": "PageSpeed Insights", "group": "Website research",
        "description": "Mobile/desktop performance for the website audit. Works without a key at low quota.",
        "settings": {}, "secrets": {"api_key": "API key"}, "required": [],
        "docs": "https://developers.google.com/speed/docs/insights/v5/get-started",
    },
    "places": {
        "label": "Google Places API", "group": "Website research",
        "description": "Competitor and Business Profile research (Places API New).",
        "settings": {}, "secrets": {"api_key": "API key"}, "required": ["api_key"],
        "docs": "https://developers.google.com/maps/documentation/places/web-service/overview",
    },
    "smtp": {
        "label": "Email alerts (SMTP)", "group": "System", "readonly": True,
        "description": "Sends alert emails when SMTP_HOST is set. Configured only through SMTP_* environment variables.",
        "settings": {}, "secrets": {}, "required": [], "env_vars": ["SMTP_HOST"], "docs": "",
    },
    "google_login": {
        "label": "Google sign-in (app login)", "group": "System", "readonly": True, "testable": False,
        "description": "Lets your team sign in with Google (ID-token verification, GOOGLE_CLIENT_ID only). Separate from Google Ads. It can only be verified by signing in.",
        "settings": {}, "secrets": {}, "required": [], "env_vars": ["GOOGLE_CLIENT_ID"], "docs": "https://developers.google.com/identity/gsi/web/guides/overview",
    },
    "database": {
        "label": "Database (PostgreSQL / Supabase)", "group": "System", "readonly": True,
        "description": "The application database. Configured through DATABASE_URL.", "settings": {}, "secrets": {}, "required": [], "env_vars": ["DATABASE_URL"], "docs": "",
    },
    "redis": {
        "label": "Redis (queue & rate limits)", "group": "System", "readonly": True,
        "description": "Background jobs and rate limiting. Configured through REDIS_URL.", "settings": {}, "secrets": {}, "required": [], "env_vars": ["REDIS_URL"], "docs": "",
    },
    "crawler": {
        "label": "Website crawler", "group": "Website research",
        "description": "SSRF-guarded crawler. 'live' fetches real sites; 'fixture' uses bundled demo pages.",
        "settings": {"mode": "fixture | live"}, "secrets": {}, "required": [],
        "subtests": [("fetch", "Live page fetch")], "required_subtests": ["fetch"],
        "default_settings": {"mode": "fixture"},
        "docs": "",
    },
}

STATUSES = ["NOT_CONNECTED", "CONFIGURATION_REQUIRED", "CONNECTED", "CONNECTION_FAILED", "AUTHORIZATION_EXPIRED",
            "SYNCHRONIZATION_REQUIRED"]
MODES = ("read_only", "execution")


# --------------------------------------------------------------------------------------------- store
def _row(db: Session, provider: str, create: bool = False) -> IntegrationConfig | None:
    if provider not in PROVIDERS:
        raise NotFound("Unknown integration")
    row = db.scalar(select(IntegrationConfig).where(IntegrationConfig.provider == provider))
    if not row and create:
        row = IntegrationConfig(provider=provider, settings=dict(PROVIDERS[provider].get("default_settings", {})),
                                secret_fingerprints={}, last_test_steps=[], scopes=[], discovered={})
        db.add(row)
        db.flush()
    return row


def _secrets(row: IntegrationConfig | None) -> dict:
    if not row or not row.secrets_ciphertext:
        return {}
    try:
        return json.loads(decrypt_token(row.secrets_ciphertext))
    except Exception:  # noqa: BLE001 - key rotated / corrupt: treat as not configured, never crash callers
        return {}


def save(db: Session, provider: str, *, settings: dict | None, secrets: dict | None, user_id: str | None) -> IntegrationConfig:
    """Write-only secrets: blank values mean 'keep existing'. Returns the row (status reset to needs-test)."""
    spec = PROVIDERS[provider]
    if any(v and str(v).strip() for v in (secrets or {}).values()):
        _require_safe_encryption_key()
    row = _row(db, provider, create=True)
    cur = dict(row.settings or {})
    for k, v in (settings or {}).items():
        if k not in spec["settings"]:
            raise ValidationFailed(f"Unknown setting '{k}' for {provider}")
        cur[k] = (v or "").strip() if isinstance(v, str) else v
    if provider == "crawler" and cur.get("mode") not in (None, "fixture", "live"):
        raise ValidationFailed("crawler mode must be 'fixture' or 'live'")
    for k in ("customer_id", "login_customer_id"):
        if cur.get(k):
            digits = cur[k].replace("-", "")
            if not digits.isdigit() or len(digits) != 10:
                raise ValidationFailed(f"{spec['settings'][k]} must be a 10-digit number")
            cur[k] = digits
    row.settings = cur
    store = _secrets(row)
    for k, v in (secrets or {}).items():
        if k not in spec["secrets"]:
            raise ValidationFailed(f"Unknown secret '{k}' for {provider}")
        if v is None or not str(v).strip():
            continue
        store[k] = str(v).strip()
    row.secrets_ciphertext = encrypt_token(json.dumps(store)) if store else ""
    row.secret_fingerprints = {k: fingerprint(v) for k, v in store.items()}
    row.updated_by_id = user_id
    row.status = "CONFIGURATION_REQUIRED" if missing(provider, row) else "SYNCHRONIZATION_REQUIRED"
    row.last_test_ok = None
    row.enabled = row.enabled and not missing(provider, row)
    db.flush()
    invalidate()
    return row


def _require_safe_encryption_key() -> None:
    """Credentials are encrypted with TOKEN_ENCRYPTION_KEY, or a key derived from APP_SECRET_KEY. The built-in development
    APP_SECRET_KEY is public, so storing real credentials under it would be encryption in name only."""
    s = get_settings()
    if not s.TOKEN_ENCRYPTION_KEY and (s.APP_SECRET_KEY.startswith("dev-") or len(s.APP_SECRET_KEY) < 32):
        raise ValidationFailed("Set TOKEN_ENCRYPTION_KEY (or a random APP_SECRET_KEY of 32+ characters) in .env and restart "
                               "before saving credentials: the built-in development key is public.")


def readonly_missing(provider: str) -> list[str]:
    """Read-only providers (database, redis, smtp, google sign-in) are configured only through environment variables."""
    s = get_settings()
    return [v for v in PROVIDERS[provider].get("env_vars", []) if not getattr(s, v, "")]


def missing(provider: str, row: IntegrationConfig | None, env_fallback: bool = True) -> list[str]:
    spec = PROVIDERS[provider]
    if spec.get("readonly"):
        return readonly_missing(provider)
    have = effective(provider, row, env_fallback)
    return [k for k in spec["required"] if not have.get(k)]


def clear_secret(db: Session, provider: str, name: str, user_id: str | None) -> IntegrationConfig:
    row = _row(db, provider, create=True)
    store = _secrets(row)
    store.pop(name, None)
    row.secrets_ciphertext = encrypt_token(json.dumps(store)) if store else ""
    row.secret_fingerprints = {k: fingerprint(v) for k, v in store.items()}
    row.status, row.enabled, row.last_test_ok, row.updated_by_id = "CONFIGURATION_REQUIRED", False, None, user_id
    invalidate()
    return row


def disconnect(db: Session, provider: str, user_id: str | None) -> IntegrationConfig:
    """Forget credentials (secrets, discovered data, status). Non-secret settings are kept."""
    row = _row(db, provider, create=True)
    row.secrets_ciphertext, row.secret_fingerprints = "", {}
    row.enabled, row.status, row.connected_account, row.account_id = False, "NOT_CONNECTED", "", ""
    row.scopes, row.last_test_steps, row.last_test_ok, row.discovered, row.last_error = [], [], None, {}, ""
    row.updated_by_id = user_id
    invalidate()
    return row


# --------------------------------------------------------------------------------------------- resolution
_ENV = {
    "google_ads": {"client_id": "GOOGLE_ADS_CLIENT_ID", "login_customer_id": "GOOGLE_ADS_LOGIN_CUSTOMER_ID",
                   "customer_id": "GOOGLE_ADS_CUSTOMER_ID",
                   "client_secret": "GOOGLE_ADS_CLIENT_SECRET", "refresh_token": "GOOGLE_ADS_REFRESH_TOKEN"},
    "groq": {"api_key": "GROQ_API_KEY", "model": "GROQ_MODEL", "challenger_model": "GROQ_CHALLENGER_MODEL"},
    "places": {"api_key": "GOOGLE_PLACES_API_KEY"},
    "pagespeed": {"api_key": "PAGESPEED_API_KEY"},
}


def effective(provider: str, row: IntegrationConfig | None, env_fallback: bool = True) -> dict:
    """Merged values: saved settings + decrypted secrets, falling back to environment variables."""
    out: dict[str, Any] = {}
    s = get_settings()
    if env_fallback:
        for k, env in _ENV.get(provider, {}).items():
            v = getattr(s, env, "")
            if v:
                out[k] = v
    if provider == "claude" and env_fallback and s.ANTHROPIC_API_KEY:
        out["api_key"] = s.ANTHROPIC_API_KEY
    for k, v in PROVIDERS[provider].get("default_settings", {}).items():
        out.setdefault(k, v)
    if row:
        out.update({k: v for k, v in (row.settings or {}).items() if v not in (None, "")})
        out.update({k: v for k, v in _secrets(row).items() if v})
    for k in ("customer_id", "login_customer_id"):  # 123-456-7890 and 1234567890 are the same Google Ads ID
        if isinstance(out.get(k), str):
            out[k] = out[k].replace("-", "").strip()
    return out


_cache: dict[str, tuple[float, dict]] = {}


def invalidate() -> None:
    _cache.clear()
    from app.adapters import registry
    registry.reset()


def record_runtime_failure(provider: str, status: str, message: str) -> None:
    """A real call failed in normal operation (e.g. the refresh token was revoked): show it on the Integrations page.
    Credentials are kept; the user re-authorises or re-tests. Never stores response bodies, only a short safe message."""
    with session_scope() as db:
        row = _row(db, provider)
        if row is None:
            return
        row.status, row.last_error, row.last_test_ok = status, message[:300], False
    invalidate()


def ensure_configured(provider: str) -> dict:
    """Credentials for a provider, or IntegrationNotConfigured. The single gate every real adapter goes through:
    no mock, no silent switch to another provider."""
    from app.core.errors import IntegrationNotConfigured
    spec = PROVIDERS[provider]
    vals = resolve(provider)
    gaps = readonly_missing(provider) if spec.get("readonly") else [k for k in spec["required"] if not vals.get(k)]
    if vals.get("_disabled"):
        raise IntegrationNotConfigured(provider, spec["label"], disabled=True)
    if gaps:
        raise IntegrationNotConfigured(provider, spec["label"], missing=gaps)
    return vals


def resolve(provider: str) -> dict:
    """Credentials for adapters (own short session; cached ~15s; cleared on every save/disconnect)."""
    hit = _cache.get(provider)
    if hit and time.monotonic() - hit[0] < 15:
        return hit[1]
    with session_scope() as db:
        row = _row(db, provider)
        vals = effective(provider, row)
        vals["_enabled"] = bool(row and row.enabled)
        vals["_disabled"] = bool(row and row.status == "DISABLED")
        if PROVIDERS[provider].get("uses_google_client") or provider == "google_ads":
            if provider != "google_ads":
                ads = effective("google_ads", _row(db, "google_ads"))
                vals.setdefault("client_id", ads.get("client_id", ""))
                vals.setdefault("client_secret", ads.get("client_secret", ""))
    _cache[provider] = (time.monotonic(), vals)
    return vals


# --------------------------------------------------------------------------------------------- operating mode
def operating_mode(db: Session | None = None) -> str:
    from app.services import settings_service
    mode = settings_service.get_value("operating_mode", "read_only", db)
    return mode if mode in MODES else "read_only"  # a retired value (e.g. the old "mock") is treated as read-only, never as writes


def set_operating_mode(db: Session, mode: str, user_id: str | None) -> dict:
    from app.services import settings_service
    if mode not in MODES:
        raise ValidationFailed(f"mode must be one of {MODES}")
    if mode == "execution":
        row = _row(db, "google_ads")
        if not (row and row.enabled and row.last_test_ok):
            raise ValidationFailed("Validated execution needs a Google Ads connection that has passed Test Connection and is enabled")
        s = get_settings()
        if not s.GOOGLE_ADS_WRITES_ENABLED:
            raise ValidationFailed("Validated Execution needs GOOGLE_ADS_WRITES_ENABLED=true in the server environment (key 1 of 2)")
    settings_service.set_value(db, "operating_mode", mode, user_id)
    db.flush()
    invalidate()
    return mode_summary(db)


def mode_summary(db: Session) -> dict:
    from app.services import settings_service
    s = get_settings()
    mode = operating_mode(db)
    key1 = s.GOOGLE_ADS_WRITES_ENABLED
    key2 = bool(settings_service.get_value("real_writes_authorized", False, db))
    return {"mode": mode,
            "label": {"read_only": "Real account — read-only", "execution": "Validated execution"}[mode],
            "real_reads": True,
            "real_writes_possible": mode == "execution" and key1 and key2,
            "write_keys": {"environment_enabled": key1, "super_admin_authorized": key2},
            "explanation": {
                "read_only": "Real Google Ads data is read. Campaign writes are impossible in this mode.",
                "execution": ("Real reads plus validated, approved writes. Campaigns are created PAUSED; enabling needs a "
                              "separate approval. Writes also need both safety keys.")}[mode]}


# --------------------------------------------------------------------------------------------- tests
class Steps:
    """Collects ordered diagnostic steps. Messages are redacted before leaving this class.
    A step may carry `sub` = the sub-test it belongs to (e.g. "manager", "primary")."""

    def __init__(self):
        self.items: list[dict] = []
        self.failed = False
        self.auth_expired = False

    def ok(self, step: str, detail: str = "", *, sub: str | None = None, **data):
        self.items.append({"name": step, "status": "PASSED", "detail": _clean(detail), **({"sub": sub} if sub else {}),
                           **({"data": redact(data)} if data else {})})

    def fail(self, step: str, detail: str, *, hint: str = "", expired: bool = False, sub: str | None = None):
        self.items.append({"name": step, "status": "FAILED", "detail": _clean(detail), "hint": hint, **({"sub": sub} if sub else {})})
        self.failed, self.auth_expired = True, self.auth_expired or expired

    def skip(self, step: str, why: str, *, sub: str | None = None):
        self.items.append({"name": step, "status": "SKIPPED", "detail": why, **({"sub": sub} if sub else {})})


def _clean(text: str) -> str:
    return str(redact(str(text)))[:600]


def _http_error(resp: httpx.Response) -> str:
    try:
        body = resp.json()
    except Exception:  # noqa: BLE001
        body = {}
    err = body.get("error") if isinstance(body, dict) else None
    if isinstance(err, dict):
        msg = err.get("message") or ""
        details = err.get("details") or []
        codes = []
        for d in details:
            for e in (d.get("errors") or []):
                codes.append(json.dumps(e.get("errorCode", {})))
        return f"HTTP {resp.status_code}: {msg} {' '.join(codes)}".strip()
    if isinstance(err, str):
        return f"HTTP {resp.status_code}: {err} {body.get('error_description', '')}".strip()
    return f"HTTP {resp.status_code}"


def refresh_access_token(vals: dict, http: httpx.Client, steps: Steps, name: str = "Refresh OAuth access token", sub: str | None = None) -> str | None:
    try:
        r = http.post("https://oauth2.googleapis.com/token", data={
            "client_id": vals.get("client_id", ""), "client_secret": vals.get("client_secret", ""),
            "refresh_token": vals.get("refresh_token", ""), "grant_type": "refresh_token"})
    except httpx.HTTPError as exc:
        steps.fail(name, f"Could not reach oauth2.googleapis.com: {type(exc).__name__}",
                   hint="Check network access to oauth2.googleapis.com from the server.", sub=sub)
        return None
    if r.status_code != 200:
        expired = "invalid_grant" in r.text
        steps.fail(name, _http_error(r), expired=expired,
                   hint=("The refresh token was revoked or expired — reconnect Google." if expired else
                         "Check the OAuth client ID/secret and that the refresh token belongs to this client."), sub=sub)
        return None
    data = r.json()
    steps.ok(name, "Access token issued", sub=sub, scope=data.get("scope", ""), expires_in=data.get("expires_in"))
    return data.get("access_token")


ADS_CONSOLE = "https://console.cloud.google.com/google/ads-apis/overview"
ADS_LIBRARY = "https://console.cloud.google.com/apis/library/googleads.googleapis.com"


def _cloud_project_number(client_id: str) -> str:
    m = re.match(r"^(\d+)-", client_id or "")
    return m.group(1) if m else ""


def _ads_codes(resp: httpx.Response) -> tuple[str, str]:
    """(error code names joined, message) from a Google Ads REST error body."""
    try:
        err = resp.json().get("error", {})
    except Exception:  # noqa: BLE001
        return "", ""
    names = []
    for d in err.get("details") or []:
        for e in d.get("errors") or []:
            for v in (e.get("errorCode") or {}).values():
                names.append(str(v))
        for v in (d.get("reason"),):
            if v:
                names.append(str(v))
    if err.get("status"):
        names.append(str(err["status"]))
    return " ".join(names), str(err.get("message") or "")


def _ads_hint(resp: httpx.Response, project: str) -> tuple[str, str]:
    """(hint, access) for a failed Google Ads call. `access` is one of: api_disabled, test_only, no_permission, auth, unknown."""
    codes, msg = _ads_codes(resp)
    both = f"{codes} {msg}".upper()
    link = f"{ADS_CONSOLE}?project={project}" if project else ADS_CONSOLE
    if "SERVICE_DISABLED" in both or "HAS NOT BEEN USED IN PROJECT" in both or "API HAS NOT BEEN" in both or "IS DISABLED" in both:
        return (f"The Google Ads API is not enabled in the Cloud project that owns your OAuth client. Enable it: {ADS_LIBRARY}"
                + (f"?project={project}" if project else ""), "api_disabled")
    if "CLOUD_PROJECT_NOT_APPROVED_FOR_PRODUCTION" in both or "ACTION_NOT_PERMITTED" in both:
        return ("Your Google Cloud project only has Test access, which works with test accounts. Apply for Explorer access on the "
                f"Google Ads API Overview page: {link}", "test_only")
    if "USER_PERMISSION_DENIED" in both or "PERMISSION_DENIED" in both:
        return ("The Google user who authorised the refresh token cannot access this account. Give that user access in Google Ads, "
                "or set the manager (login) customer ID that links the accounts.", "no_permission")
    if "AUTHORIZATION_ERROR" in both:
        return ("Google reports an authorisation error. If you recently upgraded access, Google documents a known issue for newly "
                f"approved projects (check {link}); otherwise confirm the Cloud project owning this OAuth client has API access.", "unknown")
    if "CUSTOMER_NOT_FOUND" in both or "NOT_ADS_USER" in both:
        return ("That customer ID was not found for this Google user. Check the 10-digit ID (dashes are fine).", "unknown")
    if resp.status_code == 401:
        return ("Google rejected the access token. Reconnect Google (the refresh token may be revoked).", "auth")
    return ("", "unknown")


def _ads_search(http: httpx.Client, base: str, headers: dict, customer: str, query: str, login: str = ""):
    h = {**headers, **({"login-customer-id": login} if login else {})}
    return http.post(f"{base}/customers/{customer}/googleAds:search", headers=h, json={"query": query}, timeout=30)  # no pageSize: not supported by googleAds:search in current API versions; queries use LIMIT


def _test_google_ads(vals: dict, http: httpx.Client, steps: Steps, row: IntegrationConfig, target: str = "all") -> dict:
    need = [k for k in PROVIDERS["google_ads"]["required"] if not vals.get(k)]
    if need:
        steps.fail("Credentials present", f"Missing: {', '.join(need)}", hint="Fill in the missing fields and save.", sub="auth")
        return {}
    steps.ok("Credentials present", "OAuth client ID, secret and refresh token are present (no developer token is needed)", sub="auth")
    token = refresh_access_token(vals, http, steps, "OAuth authentication: refresh access token", sub="auth")
    if not token:
        return {}
    project = _cloud_project_number(vals.get("client_id", ""))
    base = f"https://googleads.googleapis.com/{ADS_API_VERSION}"
    headers = {"Authorization": f"Bearer {token}"}  # no developer-token header: sunset 2026-09-09, optional and ignored by Google
    disc: dict = {"cloud_project_number": project, "console_url": f"{ADS_CONSOLE}?project={project}" if project else ADS_CONSOLE}
    try:
        r = http.get(f"{base}/customers:listAccessibleCustomers", headers=headers, timeout=30)
    except httpx.HTTPError as exc:
        steps.fail("Google Cloud API access: Google Ads API reachable", f"Could not reach googleads.googleapis.com: {type(exc).__name__}",
                   hint="Check network access from the server.", sub="cloud_access")
        return disc
    if r.status_code != 200:
        hint, kind = _ads_hint(r, project)
        steps.fail("Google Cloud API access: Google Ads API enabled for the project", _http_error(r), hint=hint, sub="cloud_access",
                   expired=(kind == "auth"))
        disc["cloud_access"] = {"api_enabled": False if kind == "api_disabled" else None, "level": "unknown", "basis": _http_error(r)[:200]}
        return disc
    ids = [n.split("/")[-1] for n in r.json().get("resourceNames", [])]
    steps.ok("Google Cloud API access: Google Ads API enabled for the project", f"Accepted by Google; {len(ids)} account(s) directly accessible to this Google user",
             sub="cloud_access", customers=ids[:50])
    disc["accessible_customers"] = ids
    access = {"api_enabled": True, "level": "unknown", "basis": "The API accepted the request. Google does not expose the access level through the API; "
              f"see {ADS_CONSOLE}" + (f"?project={project}" if project else "")}
    disc["cloud_access"] = access
    mcc, cust = vals.get("login_customer_id", ""), vals.get("customer_id", "")
    query = "SELECT customer.id, customer.descriptive_name, customer.manager, customer.test_account, customer.currency_code, customer.time_zone FROM customer LIMIT 1"
    if target in ("all", "manager"):
        if not mcc:
            steps.skip("Manager account", "No manager (MCC) customer ID configured — optional if the client account is directly accessible", sub="manager")
        else:
            try:
                r = _ads_search(http, base, headers, mcc, query, login=mcc)
            except httpx.HTTPError as exc:
                steps.fail("Manager account: read account information", f"Request failed: {type(exc).__name__}", sub="manager")
            else:
                if r.status_code != 200:
                    hint, kind = _ads_hint(r, project)
                    steps.fail("Manager account: read account information", _http_error(r), hint=hint, sub="manager")
                    if kind == "test_only":
                        access["level"] = "test"
                else:
                    c = (r.json().get("results") or [{}])[0].get("customer", {})
                    disc["manager"] = {"id": c.get("id", mcc), "name": c.get("descriptiveName", ""), "manager": bool(c.get("manager"))}
                    if not c.get("manager"):
                        steps.fail("Manager account: is a manager account", f"{mcc} is a regular account, not a manager (MCC).",
                                   hint="Put the MANAGER account ID in GOOGLE_ADS_LOGIN_CUSTOMER_ID and a client account under it in GOOGLE_ADS_CUSTOMER_ID.", sub="manager")
                    else:
                        steps.ok("Manager account: read account information", f"{c.get('descriptiveName') or mcc} (manager account)", sub="manager", **disc["manager"])
    if target in ("all", "client"):
        if not cust:
            steps.skip("Client account", "No client customer ID configured (GOOGLE_ADS_CUSTOMER_ID) — the app cannot be called Connected until a client account is read", sub="client")
        elif not mcc and cust not in ids:
            steps.fail("Client account: accessible", f"Account {cust} is not directly accessible to this Google user and no manager ID is set",
                       hint="Set GOOGLE_ADS_LOGIN_CUSTOMER_ID to the manager account that links it, or authorise with a user who has access.", sub="client")
        else:
            try:
                r = _ads_search(http, base, headers, cust, query, login=mcc)
            except httpx.HTTPError as exc:
                steps.fail("Client account: read account information", f"Request failed: {type(exc).__name__}", sub="client")
                return disc
            if r.status_code != 200:
                hint, kind = _ads_hint(r, project)
                steps.fail("Client account: read account information", _http_error(r), hint=hint, sub="client")
                if kind == "test_only":
                    access["level"] = "test"
                    access["basis"] = "Google answered CLOUD_PROJECT_NOT_APPROVED_FOR_PRODUCTION: this project has Test access only (test accounts)."
                return disc
            c = (r.json().get("results") or [{}])[0].get("customer", {})
            disc["client"] = {"id": c.get("id", cust), "name": c.get("descriptiveName", ""), "manager": bool(c.get("manager")),
                              "test_account": bool(c.get("testAccount")), "currency": c.get("currencyCode", ""), "time_zone": c.get("timeZone", "")}
            disc["customer"] = disc["client"]
            if c.get("manager"):
                steps.fail("Client account: is a client account", f"{cust} is a manager account. Managers hold no campaigns.",
                           hint="Set GOOGLE_ADS_CUSTOMER_ID to a client account under the manager.", sub="client")
                return disc
            steps.ok("Client account: read account information", f"{c.get('descriptiveName') or cust}" + (" (Google Ads TEST account)" if c.get("testAccount") else ""),
                     sub="client", **disc["client"])
            if c.get("testAccount"):
                access["level"] = "test-or-higher"
                access["basis"] = "The client is a Google Ads test account, which any project can call. Production access is not proven by this test."
            else:
                access["level"] = "production"
                access["basis"] = "A production account was read successfully, so this project has Explorer, Basic or Standard access (Google does not say which via the API)."
            try:
                r = _ads_search(http, base, headers, cust, "SELECT campaign.id, campaign.name, campaign.status FROM campaign WHERE campaign.status != 'REMOVED' LIMIT 5", login=mcc)
            except httpx.HTTPError as exc:
                steps.fail("Client account: read campaigns", f"Request failed: {type(exc).__name__}", sub="client")
            else:
                if r.status_code != 200:
                    steps.fail("Client account: read campaigns", _http_error(r), hint=_ads_hint(r, project)[0], sub="client")
                else:
                    steps.ok("Client account: read campaigns", f"{len(r.json().get('results', []))} campaign(s) readable (first page, read-only)", sub="client")
    return disc


def _test_ga4(vals: dict, http: httpx.Client, steps: Steps, row, target: str = "all") -> dict:
    need = [k for k in ("client_id", "client_secret", "refresh_token") if not vals.get(k)]
    if need:
        steps.fail("Credentials present", f"Missing: {', '.join(need)} (OAuth client comes from the Google Ads card)",
                   hint="Authorize Google with the Analytics scope.")
        return {}
    steps.ok("Credentials present")
    token = refresh_access_token(vals, http, steps)
    if not token:
        return {}
    h = {"Authorization": f"Bearer {token}"}
    try:
        r = http.get("https://analyticsadmin.googleapis.com/v1beta/accountSummaries", headers=h)
    except httpx.HTTPError as exc:
        steps.fail("List GA4 properties", f"Could not reach analyticsadmin.googleapis.com: {type(exc).__name__}")
        return {}
    if r.status_code != 200:
        steps.fail("List GA4 properties", _http_error(r),
                   hint="Enable the Google Analytics Admin API in the Cloud project and grant analytics.readonly.")
        return {}
    props = [{"property": p.get("property", "").split("/")[-1], "name": p.get("displayName", ""),
              "account": a.get("displayName", "")} for a in r.json().get("accountSummaries", [])
             for p in a.get("propertySummaries", [])]
    steps.ok("List GA4 properties", f"{len(props)} propert(ies)", properties=props[:50])
    pid = (vals.get("property_id") or "").replace("properties/", "")
    if not pid:
        steps.skip("Run report", "Pick a property to test reporting")
        return {"properties": props}
    try:
        r = http.post(f"https://analyticsdata.googleapis.com/v1beta/properties/{pid}:runReport", headers=h, json={
            "dateRanges": [{"startDate": "7daysAgo", "endDate": "yesterday"}], "metrics": [{"name": "sessions"}]})
    except httpx.HTTPError as exc:
        steps.fail("Run report", f"Request failed: {type(exc).__name__}")
        return {"properties": props}
    if r.status_code != 200:
        steps.fail("Run report", _http_error(r), hint="Check the property ID and that the Analytics Data API is enabled.")
    else:
        steps.ok("Run report", "Analytics Data API returned a report")
    return {"properties": props}


def _test_gbp(vals: dict, http: httpx.Client, steps: Steps, row, target: str = "all") -> dict:
    need = [k for k in ("client_id", "client_secret", "refresh_token") if not vals.get(k)]
    if need:
        steps.fail("Credentials present", f"Missing: {', '.join(need)}", hint="Authorize Google with the Business Profile scope.")
        return {}
    steps.ok("Credentials present")
    token = refresh_access_token(vals, http, steps)
    if not token:
        return {}
    try:
        r = http.get("https://mybusinessaccountmanagement.googleapis.com/v1/accounts", headers={"Authorization": f"Bearer {token}"})
    except httpx.HTTPError as exc:
        steps.fail("List Business Profile accounts", f"Request failed: {type(exc).__name__}")
        return {}
    if r.status_code != 200:
        hint = ("Google must approve Business Profile API access for your Cloud project before this works."
                if r.status_code in (403, 429) else "")
        steps.fail("List Business Profile accounts", _http_error(r), hint=hint)
        return {}
    accts = [{"name": a.get("name"), "account": a.get("accountName", "")} for a in r.json().get("accounts", [])]
    steps.ok("List Business Profile accounts", f"{len(accts)} account(s)", accounts=accts[:50])
    return {"accounts": accts}


def _groq_probe(http: httpx.Client, key: str, model: str, ids: list[str], steps: Steps, label: str, sub: str) -> dict:
    """A REAL chat completion in JSON mode (the mode the app uses). Passing needs HTTP 200 and a parseable JSON reply."""
    if ids and model not in ids:
        steps.fail(f"{label}: model available", f"'{model}' is not in your Groq model list", sub=sub,
                   hint="Use an exact model ID from https://console.groq.com/docs/models (e.g. llama-3.3-70b-versatile, openai/gpt-oss-120b).")
        return {}
    body = {"model": model, "temperature": 0, "max_tokens": 300, "response_format": {"type": "json_object"},
            "messages": [{"role": "system", "content": "Reply with a JSON object only."},
                         {"role": "user", "content": 'Return exactly this JSON: {"ok": true}'}]}
    started = time.monotonic()
    try:
        r = http.post("https://api.groq.com/openai/v1/chat/completions", headers={"Authorization": f"Bearer {key}"}, json=body, timeout=45)
    except httpx.TimeoutException:
        steps.fail(f"{label}: request", "Timed out after 45 s", hint="Groq may be overloaded; try again.", sub=sub)
        return {}
    except httpx.HTTPError as exc:
        steps.fail(f"{label}: request", f"Could not reach api.groq.com: {type(exc).__name__}", sub=sub)
        return {}
    ms = int((time.monotonic() - started) * 1000)
    if r.status_code != 200:
        hint = {401: "Check the API key.", 403: "The key may lack access to this model.", 404: "Model not found for your account.",
                429: "Rate limit or quota reached; retry later or check your Groq plan."}.get(r.status_code, "")
        steps.fail(f"{label}: request", _http_error(r), hint=hint, sub=sub)
        return {}
    try:
        content = r.json()["choices"][0]["message"]["content"] or ""
        parsed = json.loads(content)
    except Exception:  # noqa: BLE001
        steps.fail(f"{label}: JSON reply", "The model answered but not with valid JSON in JSON mode, which the app requires",
                   hint="Choose a different model for this role.", sub=sub)
        return {}
    usage = r.json().get("usage", {})
    hdr = r.headers
    limit = {}
    try:
        limit = {"tokens_per_minute": int(hdr.get("x-ratelimit-limit-tokens", 0) or 0), "requests_per_day": int(hdr.get("x-ratelimit-limit-requests", 0) or 0)}
    except ValueError:
        pass
    note = f"; plan limit {limit['tokens_per_minute']} tokens/min" if limit.get("tokens_per_minute") else ""
    steps.ok(f"{label}: real request", f"{model} answered in {ms} ms ({usage.get('total_tokens', '?')} tokens, valid JSON){note}", sub=sub,
             model=model, latency_ms=ms, reply_ok=bool(parsed))
    return {model: limit} if limit else {}


def _test_groq(vals: dict, http: httpx.Client, steps: Steps, row, target: str = "all") -> dict:
    key, primary, challenger = vals.get("api_key"), vals.get("model"), vals.get("challenger_model")
    if not key:
        steps.fail("Credentials present", "API key is required", sub="auth", hint="Set GROQ_API_KEY (or save it on this page).")
        return {}
    steps.ok("Credentials present", "API key is set", sub="auth")
    try:
        r = http.get("https://api.groq.com/openai/v1/models", headers={"Authorization": f"Bearer {key}"}, timeout=30)
    except httpx.HTTPError as exc:
        steps.fail("API key: list models", f"Could not reach api.groq.com: {type(exc).__name__}", sub="auth")
        return {}
    if r.status_code != 200:
        steps.fail("API key: list models", _http_error(r), hint="Check the API key.", sub="auth")
        return {}
    ids = sorted(m.get("id", "") for m in r.json().get("data", []))
    steps.ok("API key: list models", f"Key accepted; {len(ids)} model(s) available", sub="auth")
    limits: dict = {}
    if target in ("all", "primary"):
        if not primary:
            steps.fail("Primary model", "GROQ_MODEL is not set", hint="Set an exact Groq model ID your key can use (see the list on this page).", sub="primary")
        else:
            limits.update(_groq_probe(http, key, primary, ids, steps, "Primary model", "primary"))
    if target in ("all", "challenger"):
        if not challenger:
            steps.skip("Challenger model", "GROQ_CHALLENGER_MODEL is not set — the challenger falls back to the primary model (no independent second opinion)", sub="challenger")
        else:
            limits.update(_groq_probe(http, key, challenger, ids, steps, "Challenger model", "challenger"))
    return {"models": ids, **({"rate_limits": limits} if limits else {})}


def _test_keyword_planner(vals: dict, http: httpx.Client, steps: Steps, row, target: str = "all") -> dict:
    """Real, read-only Keyword Planner request using the Google Ads authorisation and the client account."""
    need = [k for k in ("client_id", "client_secret", "refresh_token") if not vals.get(k)]
    if need or not vals.get("customer_id"):
        steps.fail("Google Ads configured", "Needs the Google Ads OAuth credentials and a client customer ID (GOOGLE_ADS_CUSTOMER_ID)" + (f"; missing {', '.join(need)}" if need else ""),
                   hint="Configure Google Ads above first.")
        return {}
    token = refresh_access_token(vals, http, steps, "OAuth authentication")
    if not token:
        return {}
    project = _cloud_project_number(vals.get("client_id", ""))
    headers = {"Authorization": f"Bearer {token}", **({"login-customer-id": vals["login_customer_id"]} if vals.get("login_customer_id") else {})}
    try:
        r = http.post(f"https://googleads.googleapis.com/{ADS_API_VERSION}/customers/{vals['customer_id']}:generateKeywordIdeas", headers=headers, timeout=45, json={
            "keywordSeed": {"keywords": ["plumber"]}, "language": "languageConstants/1000", "geoTargetConstants": ["geoTargetConstants/2840"], "pageSize": 5})
    except httpx.HTTPError as exc:
        steps.fail("Generate keyword ideas", f"Request failed: {type(exc).__name__}")
        return {}
    if r.status_code != 200:
        steps.fail("Generate keyword ideas", _http_error(r), hint=_ads_hint(r, project)[0])
        return {}
    n = len(r.json().get("results", []))
    steps.ok("Generate keyword ideas", f"Keyword Planner returned {n} idea(s) for the seed 'plumber' (read-only)")
    return {}


def _test_smtp(vals: dict, http, steps: Steps, row, target: str = "all") -> dict:
    import smtplib
    s_ = get_settings()
    if not s_.SMTP_HOST:
        steps.fail("Credentials present", "SMTP_HOST is not set", hint="Set SMTP_HOST (and SMTP_USER / SMTP_PASSWORD) to send alert emails.")
        return {}
    steps.ok("Credentials present", f"Host {s_.SMTP_HOST}:{s_.SMTP_PORT}")
    try:
        with smtplib.SMTP(s_.SMTP_HOST, s_.SMTP_PORT, timeout=15) as c:
            c.ehlo()
            steps.ok("Connect and EHLO", "Server answered")
            if c.has_extn("starttls"):
                c.starttls()
                c.ehlo()
                steps.ok("STARTTLS", "Encrypted connection established")
            else:
                steps.fail("STARTTLS", "Server does not offer STARTTLS", hint="The app sends alerts over STARTTLS.")
                return {}
            if s_.SMTP_USER:
                c.login(s_.SMTP_USER, s_.SMTP_PASSWORD)
                steps.ok("Login", "Credentials accepted")
            else:
                steps.skip("Login", "No SMTP_USER configured")
    except Exception as exc:  # noqa: BLE001
        steps.fail("SMTP connection", f"{type(exc).__name__}: {str(exc)[:160]}", hint="Check host, port and credentials.")
    return {}


def _test_database(vals: dict, http, steps: Steps, row, target: str = "all") -> dict:
    from sqlalchemy import text
    started = time.monotonic()
    try:
        with session_scope() as db:
            version = (db.execute(text("select version()")).scalar() or "")[:40]
            head = db.execute(text("select version_num from alembic_version")).scalar()
        steps.ok("Query the database", f"Answered in {int((time.monotonic() - started) * 1000)} ms ({version.split(' on ')[0]})")
        steps.ok("Schema version", f"Migration {head}")
    except Exception as exc:  # noqa: BLE001
        steps.fail("Query the database", f"{type(exc).__name__}", hint="Check DATABASE_URL, network access and that migrations ran.")
    return {}


def _test_redis(vals: dict, http, steps: Steps, row, target: str = "all") -> dict:
    import redis as redis_lib
    try:
        started = time.monotonic()
        redis_lib.Redis.from_url(get_settings().REDIS_URL, socket_timeout=5).ping()
        steps.ok("PING", f"Answered in {int((time.monotonic() - started) * 1000)} ms")
    except Exception as exc:  # noqa: BLE001
        steps.fail("PING", f"{type(exc).__name__}", hint="Check REDIS_URL and that Redis is running.")
    return {}


def _test_claude(vals: dict, http: httpx.Client, steps: Steps, row, target: str = "all") -> dict:
    if not vals.get("api_key") or not vals.get("model"):
        steps.fail("Credentials present", "API key and model are required")
        return {}
    steps.ok("Credentials present")
    try:
        r = http.get("https://api.anthropic.com/v1/models?limit=100", headers={
            "x-api-key": vals["api_key"], "anthropic-version": "2023-06-01"})
    except httpx.HTTPError as exc:
        steps.fail("Authenticate / list models", f"Could not reach api.anthropic.com: {type(exc).__name__}")
        return {}
    if r.status_code != 200:
        steps.fail("Authenticate / list models", _http_error(r), hint="Check the API key.")
        return {}
    ids = [m.get("id", "") for m in r.json().get("data", [])]
    steps.ok("Authenticate / list models", f"{len(ids)} model(s) available")
    if vals["model"] in ids:
        steps.ok("Model available", vals["model"])
    else:
        steps.fail("Model available", f"'{vals['model']}' not returned by the Models API", hint="Check the model name.")
    return {"models": ids[:100]}


def _test_pagespeed(vals: dict, http: httpx.Client, steps: Steps, row, target: str = "all") -> dict:
    params = {"url": "https://www.google.com/", "strategy": "mobile", "category": "performance"}
    if vals.get("api_key"):
        params["key"] = vals["api_key"]
        steps.ok("Credentials present", "API key saved")
    else:
        steps.ok("Credentials present", "No key (shared low quota)")
    try:
        r = http.get("https://www.googleapis.com/pagespeedonline/v5/runPagespeed", params=params, timeout=60)
    except httpx.HTTPError as exc:
        steps.fail("Run PageSpeed", f"Request failed: {type(exc).__name__}")
        return {}
    if r.status_code != 200:
        hint = ("Quota exceeded. Add a PageSpeed API key (without one the quota is shared and tiny)." if r.status_code == 429 and not vals.get("api_key")
                else "Quota exceeded for this key; wait or raise the quota in Google Cloud." if r.status_code == 429
                else "Enable the PageSpeed Insights API for the key's Cloud project and check the key restrictions.")
        steps.fail("Run PageSpeed", _http_error(r), hint=hint)
    else:
        score = r.json().get("lighthouseResult", {}).get("categories", {}).get("performance", {}).get("score")
        pct = round(score * 100) if isinstance(score, (int, float)) else None
        steps.ok("Run PageSpeed", f"Real analysis of https://www.google.com/ (mobile): performance score {pct if pct is not None else 'n/a'}/100")
    return {}


def _test_places(vals: dict, http: httpx.Client, steps: Steps, row, target: str = "all") -> dict:
    if not vals.get("api_key"):
        steps.fail("Credentials present", "API key is required")
        return {}
    steps.ok("Credentials present")
    try:
        r = http.post("https://places.googleapis.com/v1/places:searchText", json={"textQuery": "plumber in Austin TX"},
                      headers={"X-Goog-Api-Key": vals["api_key"], "X-Goog-FieldMask": "places.id"})
    except httpx.HTTPError as exc:
        steps.fail("Text search", f"Request failed: {type(exc).__name__}")
        return {}
    if r.status_code != 200:
        steps.fail("Text search", _http_error(r), hint="Enable Places API (New) and billing for the key's project.")
    else:
        steps.ok("Text search", f"{len(r.json().get('places', []))} place(s) returned")
    return {}


def _test_crawler(vals: dict, http: httpx.Client, steps: Steps, row, target: str = "all") -> dict:
    mode = vals.get("mode", "fixture")
    steps.ok("Mode", mode)
    if mode != "live":
        steps.skip("Fetch a page", "Fixture mode serves bundled demo pages; nothing real is fetched, so this is NOT a connection. Set the crawler mode to live to test.", sub="fetch")
        return {}
    from app.adapters.crawler import LiveCrawlerAdapter
    try:
        res = LiveCrawlerAdapter().fetch("https://example.com/")
        if getattr(res, "status", 0) and 200 <= res.status < 300:
            steps.ok("Fetch a page", f"HTTP {res.status} via the SSRF-guarded crawler", sub="fetch")
        else:
            steps.fail("Fetch a page", f"HTTP {getattr(res, 'status', '?')}", sub="fetch")
    except Exception as exc:  # noqa: BLE001
        steps.fail("Fetch a page", f"{type(exc).__name__}: {getattr(exc, 'message', str(exc))}", sub="fetch")
    return {}


_TESTERS: dict[str, Callable] = {"keyword_planner": _test_keyword_planner, "smtp": _test_smtp, "database": _test_database, "redis": _test_redis,
                                 "google_ads": _test_google_ads, "ga4": _test_ga4, "gbp": _test_gbp, "groq": _test_groq,
                                 "claude": _test_claude, "pagespeed": _test_pagespeed, "places": _test_places,
                                 "crawler": _test_crawler}


def _subtest_states(provider: str, steps: Steps, prior: dict, started: datetime) -> dict:
    """Merge this run's per-sub-test outcomes into the stored ones (a partial run only updates the sub-tests it ran)."""
    out = {k: dict(v) for k, v in (prior or {}).items()}
    for key, _label in PROVIDERS[provider].get("subtests", []):
        mine = [i for i in steps.items if i.get("sub") == key]
        if not mine:
            continue
        failed = [i for i in mine if i["status"] == "FAILED"]
        passed = [i for i in mine if i["status"] == "PASSED"]
        cur = out.get(key, {})
        if failed:
            out[key] = {"status": "FAILED", "detail": failed[0]["detail"], "hint": failed[0].get("hint", ""), "at": started.isoformat(), "last_success_at": cur.get("last_success_at")}
        elif passed:
            out[key] = {"status": "PASSED", "detail": passed[-1]["detail"], "hint": "", "at": started.isoformat(), "last_success_at": started.isoformat()}
        else:
            out[key] = {"status": "SKIPPED", "detail": mine[-1]["detail"], "hint": "", "at": started.isoformat(), "last_success_at": cur.get("last_success_at")}
    return out


def _connection_state(provider: str, vals: dict, subtests: dict, steps_failed: bool) -> bool | None:
    """True only when every REQUIRED sub-test passed in a real request; False when one failed; None when not fully tested."""
    spec = PROVIDERS[provider]
    req = list(spec.get("required_subtests", []))
    if not req:
        return not steps_failed
    if provider == "groq" and vals.get("challenger_model"):
        req.append("challenger")
    states = [subtests.get(k, {}).get("status") for k in req]
    if any(x == "FAILED" for x in states):
        return False
    return True if all(x == "PASSED" for x in states) else None


def test_connection(db: Session, provider: str, *, http: httpx.Client | None = None, user_id: str | None = None, target: str = "all") -> dict:
    """Run a REAL request sequence. Persists the outcome. `http` is injectable for tests only.
    `target` limits a multi-part integration to one part (google_ads: manager|client, groq: primary|challenger)."""
    spec = PROVIDERS[provider]
    if spec.get("testable") is False:
        raise ValidationFailed(f"{spec['label']} has no connection test: it can only be verified by signing in")
    if target != "all" and target not in spec.get("targets", {}):
        raise ValidationFailed(f"Unknown test target '{target}' for {provider}")
    row = _row(db, provider, create=True)
    steps = Steps()
    started = datetime.now(timezone.utc)
    if provider == "keyword_planner":
        vals = effective("google_ads", _row(db, "google_ads"))
    else:
        vals = effective(provider, row)
    if spec.get("uses_google_client"):
        ads_vals = effective("google_ads", _row(db, "google_ads"))
        vals.setdefault("client_id", ads_vals.get("client_id", ""))
        vals.setdefault("client_secret", ads_vals.get("client_secret", ""))
    own = http is None
    client = http or httpx.Client(timeout=20)
    try:
        discovered = _TESTERS[provider](vals, client, steps, row, target) or {}
    finally:
        if own:
            client.close()
    row.last_test_at = started
    row.last_test_steps = steps.items
    if discovered:
        row.discovered = {**(row.discovered or {}), **discovered}
    if spec.get("subtests"):
        subs = _subtest_states(provider, steps, (row.discovered or {}).get("subtests"), started)
        row.discovered = {**(row.discovered or {}), "subtests": subs}
        connected = _connection_state(provider, vals, subs, steps.failed)
    else:
        subs, connected = {}, (not steps.failed)
    row.last_test_ok = connected
    if steps.failed:
        failed = next(s for s in steps.items if s["status"] == "FAILED")
        row.last_error = f"{failed['name']}: {failed['detail']}"
        row.status = "AUTHORIZATION_EXPIRED" if steps.auth_expired else (
            "CONFIGURATION_REQUIRED" if failed["name"] == "Credentials present" else "CONNECTION_FAILED")
        row.enabled = False
    elif connected:
        row.last_error, row.last_success_at = "", started
        row.status = "CONNECTED"
        info = (discovered or {}).get("client") or (discovered or {}).get("customer")
        if provider == "google_ads" and info:
            row.connected_account, row.account_id = info.get("name", ""), info.get("id", "")
        row.scopes = spec.get("scopes", row.scopes)
    else:
        row.last_error = ""
        row.status = "SYNCHRONIZATION_REQUIRED"  # this part passed, but the integration is not fully verified yet
    db.flush()
    invalidate()
    return {"provider": provider, "target": target, "ok": not steps.failed, "connected": bool(connected), "status": row.status, "steps": steps.items,
            "subtests": subs, "tested_at": started.isoformat()}


def set_enabled(db: Session, provider: str, enabled: bool, user_id: str | None) -> IntegrationConfig:
    row = _row(db, provider, create=True)
    if enabled and not row.last_test_ok:
        raise ValidationFailed("Run Test Connection successfully before enabling this integration")
    row.enabled, row.updated_by_id = enabled, user_id
    if not enabled:
        row.status = "DISABLED"
    elif row.status == "DISABLED":
        row.status = "CONNECTED"
    if not enabled and provider == "google_ads":
        from app.services import settings_service
        if settings_service.get_value("operating_mode", "read_only", db) != "read_only":
            settings_service.set_value(db, "operating_mode", "read_only", user_id)  # fall back to the safe mode
    db.flush()
    invalidate()
    return row


# --------------------------------------------------------------------------------------------- OAuth
def oauth_redirect_uri() -> str:
    s = get_settings()
    return s.GOOGLE_ADS_REDIRECT_URI or "http://localhost:5173/api/v1/integrations/google/callback"


def oauth_start(db: Session, providers: list[str], user_id: str) -> dict:
    import hashlib
    import secrets
    from urllib.parse import urlencode

    from app.adapters.ads_oauth import new_pkce
    from app.models import OAuthState
    for p in providers:
        if not PROVIDERS.get(p, {}).get("oauth"):
            raise ValidationFailed(f"{p} does not use Google OAuth")
    ads = effective("google_ads", _row(db, "google_ads"))
    if not ads.get("client_id") or not ads.get("client_secret"):
        raise ValidationFailed("Save the OAuth client ID and secret on the Google Ads card first")
    scopes = sorted({sc for p in providers for sc in GOOGLE_SCOPES[p]} | {"openid", "email"})
    verifier, challenge = new_pkce()
    state = secrets.token_urlsafe(32)
    db.add(OAuthState(state_hash=hashlib.sha256(state.encode()).hexdigest(), provider=",".join(providers),
                      verifier_ciphertext=encrypt_token(verifier), user_id=user_id, redirect_uri=oauth_redirect_uri(),
                      expires_at=datetime.now(timezone.utc) + timedelta(minutes=10)))
    db.flush()
    url = "https://accounts.google.com/o/oauth2/v2/auth?" + urlencode({
        "client_id": ads["client_id"], "redirect_uri": oauth_redirect_uri(), "response_type": "code", "scope": " ".join(scopes),
        "access_type": "offline", "prompt": "consent", "state": state, "code_challenge": challenge,
        "code_challenge_method": "S256", "include_granted_scopes": "true"})
    return {"authorization_url": url, "redirect_uri": oauth_redirect_uri(), "scopes": scopes}


def oauth_callback(db: Session, state: str, code: str, user_id: str, *, http: httpx.Client | None = None) -> dict:
    """Validate state (single-use, 10 min, same user), exchange the code with PKCE, store the refresh token encrypted."""
    import hashlib
    from app.models import OAuthState
    row = db.scalar(select(OAuthState).where(OAuthState.state_hash == hashlib.sha256(state.encode()).hexdigest()))
    now = datetime.now(timezone.utc)
    if not row or row.used_at or row.expires_at < now or row.user_id != user_id:
        raise ValidationFailed("Invalid or expired authorization request — start the connection again")
    row.used_at = now
    db.flush()
    ads = effective("google_ads", _row(db, "google_ads"))
    own = http is None
    client = http or httpx.Client(timeout=20)
    try:
        r = client.post("https://oauth2.googleapis.com/token", data={
            "code": code, "client_id": ads.get("client_id", ""), "client_secret": ads.get("client_secret", ""),
            "redirect_uri": row.redirect_uri, "grant_type": "authorization_code", "code_verifier": decrypt_token(row.verifier_ciphertext)})
    finally:
        if own:
            client.close()
    if r.status_code != 200:
        raise ValidationFailed("Google rejected the authorization code: " + _http_error(r))
    data = r.json()
    if not data.get("refresh_token"):
        raise ValidationFailed("Google returned no refresh token — remove the app's access in your Google account and retry")
    granted = set((data.get("scope") or "").split())
    saved = []
    for p in row.provider.split(","):
        if set(GOOGLE_SCOPES[p]) <= granted:
            save(db, p, settings=None, secrets={"refresh_token": data["refresh_token"]}, user_id=user_id)
            _row(db, p).scopes = GOOGLE_SCOPES[p]
            saved.append(p)
    if not saved:
        raise ValidationFailed("None of the requested permissions were granted")
    return {"saved": saved, "granted_scopes": sorted(granted), "next": "Run Test Connection on each card"}


# --------------------------------------------------------------------------------------------- views
def _display(provider: str, row: IntegrationConfig | None, miss: list[str]) -> dict:
    """The six statuses a person sees. Connected is shown ONLY after a real successful request (row.last_test_ok)."""
    spec = PROVIDERS[provider]
    config = "Not configured" if miss else "Configured"
    if spec.get("testable") is False:
        return {"config": config, "connection": config, "label": config, "testable": False}
    if miss:
        label = "Not configured"
    elif row and row.status == "DISABLED":
        label = "Disabled"
    elif row and row.last_test_ok is True:
        label = "Connected"
    elif row and row.last_test_ok is False:
        label = "Connection failed"
    else:
        label = "Not tested"
    return {"config": config, "connection": label, "label": label, "testable": True}


def _readonly_info(provider: str) -> dict:
    """Non-secret facts about env-configured system components."""
    from urllib.parse import urlparse
    s = get_settings()
    if provider == "database":
        u = urlparse(s.DATABASE_URL)
        host = u.hostname or ""
        return {"kind": "Supabase (pooler)" if "supabase" in host else "PostgreSQL", "host": host, "database": (u.path or "").lstrip("/"), "ssl": "sslmode" in (u.query or "")}
    if provider == "redis":
        u = urlparse(s.REDIS_URL)
        return {"host": u.hostname or "", "port": u.port}
    if provider == "smtp":
        return {"host": s.SMTP_HOST, "port": s.SMTP_PORT, "login": bool(s.SMTP_USER), "from": s.SMTP_FROM}
    if provider == "google_login":
        return {"auth_provider": s.AUTH_PROVIDER, "active": s.AUTH_PROVIDER == "google", "client_id_set": bool(s.GOOGLE_CLIENT_ID),
                "bootstrap_admin_set": bool(s.BOOTSTRAP_ADMIN_EMAIL), "allowed_domains": s.allowed_login_domains}
    return {}


def serialize(db: Session, provider: str) -> dict:
    spec = PROVIDERS[provider]
    row = _row(db, provider)
    vals = effective(provider, row) if not spec.get("readonly") else {}
    env_vals = effective(provider, None) if not spec.get("readonly") else {}
    miss = missing(provider, row)
    status = row.status if row else "NOT_CONNECTED"
    if provider == "keyword_planner":
        ads_vals = effective("google_ads", _row(db, "google_ads"))
        miss = [k for k in ("client_id", "client_secret", "refresh_token", "customer_id") if not ads_vals.get(k)]
    elif miss and status in ("NOT_CONNECTED", "SYNCHRONIZATION_REQUIRED") and (row or env_vals):
        status = "CONFIGURATION_REQUIRED" if (row and (row.settings or row.secret_fingerprints)) else status
    elif not row and any(env_vals.get(k) for k in spec["required"]) and not miss:
        status = "SYNCHRONIZATION_REQUIRED"  # credentials exist only in the environment: never auto-"connected"
    fps = dict(row.secret_fingerprints) if row else {}
    saved_subs = ((row.discovered or {}).get("subtests") or {}) if row else {}
    subtests = [{"key": k, "label": lbl, **({"status": "NOT_TESTED", "detail": "", "hint": "", "at": None, "last_success_at": None} | saved_subs.get(k, {}))}
                for k, lbl in spec.get("subtests", [])]
    discovered = dict((row.discovered or {}) if row else {})
    discovered.pop("subtests", None)
    info = _readonly_info(provider) if spec.get("readonly") else {}
    if provider == "google_ads":
        disc = discovered or {}
        num = _cloud_project_number(vals.get("client_id", ""))
        info = {"cloud_project_number": num or None, "console_url": f"{ADS_CONSOLE}?project={num}" if num else ADS_CONSOLE,
                "developer_token": "Not required (sunset 2026-09-09): access is granted to the Google Cloud project that owns the OAuth client",
                "api_access": disc.get("cloud_access") or {"api_enabled": None, "level": "not tested", "basis": "Run Test Connection"},
                "manager_id_set": bool(vals.get("login_customer_id")), "client_id_set": bool(vals.get("customer_id")),
                "manager": disc.get("manager"), "client": disc.get("client")}
    if provider == "groq":
        info = {"primary_model": vals.get("model") or "", "challenger_model": vals.get("challenger_model") or "",
                "challenger_falls_back": not vals.get("challenger_model"), "rate_limits": (row.discovered or {}).get("rate_limits") if row else None,
                "active_in_app": registry_ai_active()}
    return {
        "provider": provider, "label": spec["label"], "group": spec["group"], "description": spec["description"],
        "status": status, "enabled": bool(row and row.enabled), "readonly": bool(spec.get("readonly")),
        "display": _display(provider, row, miss), "subtests": subtests, "targets": spec.get("targets", {}), "info": info,
        "settings": {k: {"label": lbl, "value": (vals.get(k) if k in (row.settings if row else {}) or k in env_vals else
                                                  vals.get(k, "")) or ""} for k, lbl in spec["settings"].items()},
        "secrets": {k: {"label": lbl, "saved": bool(fps.get(k)) or bool(env_vals.get(k) and k in _ENV.get(provider, {})),
                        "fingerprint": fps.get(k) or (fingerprint(str(env_vals[k]))[:8] if env_vals.get(k) and k in _ENV.get(provider, {}) else ""),
                        "source": "saved" if fps.get(k) else ("environment" if env_vals.get(k) and k in _ENV.get(provider, {}) else "")}
                    for k, lbl in spec["secrets"].items()},
        "required": spec["required"], "missing": miss, "scopes": (row.scopes if row else []) or spec.get("scopes", []),
        "oauth": bool(spec.get("oauth")), "docs": spec["docs"],
        "connected_account": row.connected_account if row else "", "account_id": row.account_id if row else "",
        "last_test_at": row.last_test_at.isoformat() if row and row.last_test_at else None,
        "last_test_ok": row.last_test_ok if row else None, "last_test_steps": row.last_test_steps if row else [],
        "last_success_at": row.last_success_at.isoformat() if row and row.last_success_at else None,
        "last_sync_at": row.last_sync_at.isoformat() if row and row.last_sync_at else None,
        "last_error": row.last_error if row else "", "discovered": discovered,
    }


def registry_ai_active() -> bool:
    """True when the app can actually use Groq (configured and not disabled)."""
    from app.core.errors import IntegrationNotConfigured
    try:
        ensure_configured("groq")
        return True
    except IntegrationNotConfigured:
        return False


def health(db: Session) -> dict:
    items = [serialize(db, p) for p in PROVIDERS]
    counts: dict[str, int] = {}
    for i in items:
        counts[i["display"]["label"]] = counts.get(i["display"]["label"], 0) + 1
    return {"items": [{**{k: i[k] for k in ("provider", "label", "group", "status", "enabled", "last_test_at", "last_test_ok",
                                           "last_error", "connected_account")}, "display": i["display"]} for i in items],
            "counts": counts, "mode": mode_summary(db)}


def test_all(db: Session, user_id: str | None, *, http: httpx.Client | None = None) -> list[dict]:
    out = []
    for p, spec in PROVIDERS.items():
        row = _row(db, p)
        if spec.get("testable") is False:
            continue
        if missing(p, row) and p not in ("pagespeed", "crawler", "keyword_planner"):
            out.append({"provider": p, "ok": False, "connected": False, "status": serialize(db, p)["status"], "skipped": True,
                        "steps": [{"name": "Credentials present", "status": "SKIPPED", "detail": "Not configured"}]})
        else:
            out.append(test_connection(db, p, http=http, user_id=user_id))
    return out
