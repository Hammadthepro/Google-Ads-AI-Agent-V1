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
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

import httpx
from sqlalchemy import Integer, select
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
}
ADS_API_VERSION = "v26"  # matches the installed google-ads library default

PROVIDERS: dict[str, dict] = {
    "google_ads": {
        "label": "Google Ads API", "group": "Google",
        "description": "Account discovery, reporting and Keyword Planner. Campaign writes stay disabled unless explicitly authorised twice.",
        "settings": {"client_id": "OAuth client ID", "login_customer_id": "Default manager (MCC) ID — optional; choose the manager in the connection flow"},
        "secrets": {"client_secret": "OAuth client secret", "refresh_token": "Refresh token"},
        # No developer token: Google sunset developer tokens on 2026-09-09; API access levels now belong to the Cloud project
        # that owns the OAuth client.
        "required": ["client_id", "client_secret", "refresh_token"],
        "subtests": [("auth", "OAuth authentication"), ("cloud_access", "Google Cloud API access"), ("manager", "Manager accounts"),
                     ("hierarchy", "Client accounts")],
        "required_subtests": ["auth", "cloud_access", "manager"], "targets": {"manager": "Test manager accounts", "hierarchy": "Test client discovery"},
        "oauth": True, "scopes": GOOGLE_SCOPES["google_ads"],
        "docs": "https://developers.google.com/google-ads/api/docs/api-policy/developer-token",
    },
    "ga4": {
        "label": "Google Analytics 4", "group": "Google",
        "description": "Supporting traffic and key-event data (Analytics Data API). Never mixed into Ads conversion counts.",
        "settings": {"property_id": "GA4 property ID"}, "secrets": {"refresh_token": "Refresh token"},
        "required": ["refresh_token", "property_id"], "oauth": True, "scopes": GOOGLE_SCOPES["ga4"], "uses_google_client": True,
        "docs": "https://developers.google.com/analytics/devguides/reporting/data/v1",
    },
    "keyword_planner": {
        "label": "Keyword Planner", "group": "Google",
        "description": "Keyword research for any client, before or without a linked Google Ads account. Uses the Google Ads authorisation above and ONE research "
                       "customer ID (a Google Ads account used only to call Keyword Planner; never a client's deployment account). The test asks Keyword Planner for real ideas (read-only).",
        "settings": {"research_customer_id": "Research customer ID — the Google Ads account Keyword Planner requests run under"},
        "secrets": {}, "required": ["research_customer_id"], "depends_on": "google_ads",
        "docs": "https://developers.google.com/google-ads/api/docs/keyword-planning/overview",
    },
    "groq": {
        "label": "Groq (internal AI)", "group": "AI", "superseded_by": "ai_roles",
        "description": "The internal AI used for drafting and for the first side of the AI competition.",
        "settings": {"model": "Model", "challenger_model": "Challenger model (optional)"},
        "secrets": {"api_key": "API key"}, "required": ["model", "api_key"],
        "subtests": [("auth", "API key"), ("primary", "Primary model"), ("challenger", "Challenger model")],
        "required_subtests": ["auth", "primary"], "optional_subtests": ["challenger"],
        "targets": {"primary": "Test primary model", "challenger": "Test challenger model"},
        "docs": "https://console.groq.com/docs/models",
    },
    "claude": {
        "label": "Claude (Anthropic)", "group": "AI", "superseded_by": "ai_roles",
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
        "description": "Optional, paid: Google Business Profile ratings, reviews and hours (Places API New). Not required: without it, business presence and "
                       "nearby competitors come from OpenStreetMap / Geoapify, and ratings and reviews are reported as unavailable.",
        "settings": {}, "secrets": {"api_key": "API key"}, "required": ["api_key"],
        "docs": "https://developers.google.com/maps/documentation/places/web-service/overview",
    },
    "tavily": {
        "label": "Tavily Web Search", "group": "Research",
        "description": "Web Search (primary): finds pages the app does not know yet — competitors that were not supplied, customer questions and industry "
                       "language for keyword intent, local market context — and returns real URLs, titles and snippets. Snippets are stored as web evidence, "
                       "never as verified facts. Free plan: 1,000 API credits a month (1 per basic search, 2 per advanced). Repeated queries come from the "
                       "cache and each audit has a search budget. Without it (and without Brave Search) the app does no web search at all.",
        "settings": {"search_depth": "Search depth (basic | advanced)", "max_searches_per_audit": "Maximum searches per audit"},
        "secrets": {"api_key": "API key"}, "required": ["api_key"],
        "default_settings": {"search_depth": "basic", "max_searches_per_audit": "8"},
        "docs": "https://docs.tavily.com/documentation/api-reference/endpoint/search",
    },
    "brave_search": {
        "label": "Brave Search API", "group": "Research",
        "description": "Web Search (fallback): used only when Tavily is not configured or fails, and the result says so. Brave's own independent index, "
                       "country targeting. Billing is metered (about $5 per 1,000 requests) with a monthly credit of about $5 that needs a payment method — "
                       "check Brave's current terms; it is not an unconditional free plan.",
        "settings": {"max_searches_per_audit": "Maximum searches per audit"}, "secrets": {"api_key": "API key (subscription token)"}, "required": ["api_key"],
        "default_settings": {"max_searches_per_audit": "8"},
        "docs": "https://api-dashboard.search.brave.com/app/documentation/web-search/get-started",
    },
    "search_suggestions": {
        "label": "Search suggestions (keyword discovery)", "group": "Research",
        "description": "Free keyword discovery from public search-suggestion endpoints (Google Autocomplete, DuckDuckGo, Bing). They return the phrases people "
                       "type and nothing else: no search volume, CPC or competition. The endpoints are unofficial and can change; requests are throttled and cached.",
        "settings": {"sources": "Sources, comma separated (google, duckduckgo, bing)"}, "secrets": {}, "required": [],
        "default_settings": {"sources": "google,duckduckgo"},  # Bing's endpoint returned no content (HTTP 204) in testing; it can be re-enabled here
        "subtests": [("google", "Google Autocomplete"), ("duckduckgo", "DuckDuckGo"), ("bing", "Bing")], "required_subtests": [],
        "docs": "",
    },
    "openstreetmap": {
        "label": "OpenStreetMap (geocoding & nearby places)", "group": "Research",
        "description": "Free geographic research: Nominatim geocoding and Overpass points of interest for nearby competitors and market density. "
                       "Coverage of small businesses is incomplete and there are no ratings or reviews. Public servers have usage policies: requests are "
                       "throttled and cached; set your own instance URLs for heavy use. Data © OpenStreetMap contributors (ODbL).",
        "settings": {"nominatim_url": "Nominatim URL", "overpass_url": "Overpass API URL", "contact_email": "Contact email sent to Nominatim (optional, recommended by its policy)"},
        "secrets": {}, "required": [],
        "default_settings": {"nominatim_url": "https://nominatim.openstreetmap.org", "overpass_url": "https://overpass-api.de/api/interpreter"},
        "subtests": [("geocode", "Nominatim geocoding"), ("pois", "Overpass points of interest")], "required_subtests": ["geocode"],
        "docs": "https://operations.osmfoundation.org/policies/nominatim/",
    },
    "geoapify": {
        "label": "Geoapify (places & geocoding)", "group": "Research",
        "description": "Optional geocoding and Places search built on OpenStreetMap data with a free daily credit allowance (check Geoapify's current plan; "
                       "limits and pricing can change). Attribution is required wherever its results are shown.",
        "settings": {}, "secrets": {"api_key": "API key"}, "required": ["api_key"],
        "docs": "https://apidocs.geoapify.com/docs/places/",
    },
    "smtp": {
        "label": "Email alerts (SMTP)", "group": "System", "system": True,
        "description": "Sends alert emails. Configured by a Super Admin here (password encrypted); SMTP_* environment variables are the fallback. Without it alerts stay in the app and are marked “not emailed”.",
        "settings": {"host": "SMTP host", "port": "Port", "security": "Security (starttls | ssl | none)", "username": "Username (optional)",
                     "from_email": "From email", "from_name": "From name (optional)"},
        "secrets": {"password": "Password"}, "required": ["host", "from_email"],
        "default_settings": {"port": "587", "security": "starttls"},
        "docs": "https://docs.python.org/3/library/smtplib.html",
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
    "web_fetch": {
        "label": "Web Fetch (single page)", "group": "Website research",
        "description": "Opens ONE known URL and extracts its readable content (title, headings, text). Used to read landing pages and to open sites that "
                       "Web Search found. It cannot find pages by itself. Built-in, no credentials: it requests the page directly from this server through "
                       "the SSRF guard (private addresses refused, every redirect re-checked, size and time limits).",
        "settings": {}, "secrets": {}, "required": [],
        "subtests": [("fetch", "Fetch and extract one page")], "required_subtests": ["fetch"],
        "docs": "",
    },
    "crawler": {
        "label": "Website crawler", "group": "Website research",
        "description": "Starts from a website you give it (the client's or a competitor's), reads robots.txt and the sitemap, and follows internal links "
                       "to visit and extract its pages within the crawl limits. Built-in, no credentials; uses Web Fetch for each page. It only reads sites "
                       "it is pointed at: it is not a web search and cannot discover sites it was not given.",
        "settings": {}, "secrets": {}, "required": [],
        "subtests": [("fetch", "Live page fetch"), ("crawl", "Crawl a small site")], "required_subtests": ["fetch"],
        "docs": "",
    },
}

STATUSES = ["NOT_CONNECTED", "CONFIGURATION_REQUIRED", "CONNECTED", "CONNECTION_FAILED", "AUTHORIZATION_EXPIRED",
            "SYNCHRONIZATION_REQUIRED", "REQUIRES_APPROVAL"]
# Settings that only the deployment (environment) can change; the page shows them read-only with instructions.
DEPLOYMENT_MANAGED = {"database", "redis", "google_login"}
MODES = ("read_only", "execution")


# --------------------------------------------------------------------------------------------- store
_workspace_override: ContextVar[str | None] = ContextVar("integration_workspace", default=None)


@contextmanager
def use_workspace(workspace_id: str):
    """Run integration reads/writes for another workspace (default: the single default workspace). Credentials never cross workspaces."""
    token = _workspace_override.set(workspace_id)
    try:
        yield
    finally:
        _workspace_override.reset(token)


def workspace_id(db: Session) -> str:
    from app.models import Workspace
    if _workspace_override.get():
        return _workspace_override.get()
    wid = db.scalar(select(Workspace.id).where(Workspace.slug == "default"))
    if wid is None:
        ws = Workspace(name="Default workspace", slug="default")
        db.add(ws)
        db.flush()
        wid = ws.id
    return wid


def _row(db: Session, provider: str, create: bool = False) -> IntegrationConfig | None:
    if provider not in PROVIDERS:
        raise NotFound("Unknown integration")
    wid = workspace_id(db)
    row = db.scalar(select(IntegrationConfig).where(IntegrationConfig.provider == provider, IntegrationConfig.workspace_id == wid))
    if not row and create:
        row = IntegrationConfig(provider=provider, workspace_id=wid, settings=dict(PROVIDERS[provider].get("default_settings", {})),
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


def secrets_unreadable(row: IntegrationConfig | None) -> bool:
    """True when credentials ARE stored but cannot be decrypted (TOKEN_ENCRYPTION_KEY changed or ciphertext damaged). Without this the
    page would silently show the fields as missing."""
    if not row or not row.secrets_ciphertext:
        return False
    try:
        decrypt_token(row.secrets_ciphertext)
        return False
    except Exception:  # noqa: BLE001
        return True


def save(db: Session, provider: str, *, settings: dict | None, secrets: dict | None, user_id: str | None) -> IntegrationConfig:
    """Write-only secrets: blank values mean 'keep existing'. Returns the row (status reset to needs-test)."""
    spec = PROVIDERS[provider]
    if any(v and str(v).strip() for v in (secrets or {}).values()):
        _require_safe_encryption_key()
    row = _row(db, provider, create=True)
    cur = dict(row.settings or {})
    previous_manager = cur.get("login_customer_id") or ""
    for k, v in (settings or {}).items():
        if k not in spec["settings"]:
            raise ValidationFailed(f"Unknown setting '{k}' for {provider}")
        new = (v or "").strip() if isinstance(v, str) else v
        if new in ("", None) and k in spec["required"] and cur.get(k):
            continue  # a blank required value keeps the saved one (like secrets); use Disconnect to remove a configuration
        cur[k] = new
    if provider == "smtp":
        if cur.get("security") and cur["security"] not in ("starttls", "ssl", "none"):
            raise ValidationFailed("SMTP security must be starttls, ssl or none")
        if cur.get("port") and not str(cur["port"]).isdigit():
            raise ValidationFailed("SMTP port must be a number")
        if cur.get("from_email") and not re.match(r"^[^@\s]+@[^@\s]+$", cur["from_email"]):
            raise ValidationFailed("From email must look like name@example.com")
    cur.pop("customer_id", None)  # retired: the client account is chosen in the application (workspace account selector)
    for k in ("login_customer_id", "research_customer_id"):
        if cur.get(k):
            digits = str(cur[k]).replace("-", "").strip()
            if not digits.isdigit() or len(digits) != 10:
                raise ValidationFailed(f"{spec['settings'][k]} must be a 10-digit number")
            cur[k] = digits
    if provider == "google_ads" and cur.get("login_customer_id") and cur["login_customer_id"] != previous_manager:
        # A newly saved manager ID is a manager selection: store it where the rest of the app reads it, so the two can never disagree.
        from app.models import Workspace
        ws = db.scalar(select(Workspace).where(Workspace.slug == "default"))
        if ws is not None and ws.google_ads_manager_customer_id != cur["login_customer_id"]:
            ws.google_ads_manager_customer_id, ws.google_ads_manager_name = cur["login_customer_id"], ""
            ws.discovery_status, ws.discovery_message = "NEVER_RUN", ""
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
    "keyword_planner": {"research_customer_id": "GOOGLE_ADS_RESEARCH_CUSTOMER_ID"},
    "google_ads": {"client_id": "GOOGLE_ADS_CLIENT_ID", "login_customer_id": "GOOGLE_ADS_LOGIN_CUSTOMER_ID",
                   "client_secret": "GOOGLE_ADS_CLIENT_SECRET", "refresh_token": "GOOGLE_ADS_REFRESH_TOKEN"},
    "groq": {"api_key": "GROQ_API_KEY", "model": "GROQ_MODEL", "challenger_model": "GROQ_CHALLENGER_MODEL"},
    "places": {"api_key": "GOOGLE_PLACES_API_KEY"},
    "tavily": {"api_key": "TAVILY_API_KEY"},
    "brave_search": {"api_key": "BRAVE_SEARCH_API_KEY"},
    "geoapify": {"api_key": "GEOAPIFY_API_KEY"},
    "pagespeed": {"api_key": "PAGESPEED_API_KEY"},
    "smtp": {"host": "SMTP_HOST", "port": "SMTP_PORT", "username": "SMTP_USER", "password": "SMTP_PASSWORD", "from_email": "SMTP_FROM"},
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
    out.pop("customer_id", None)
    for k in ("login_customer_id", "research_customer_id"):  # 123-456-7890 and 1234567890 are the same Google Ads ID
        if isinstance(out.get(k), str):
            out[k] = out[k].replace("-", "").strip()
    return out


_cache: dict[str, tuple[float, dict]] = {}


def invalidate() -> None:
    _cache.clear()
    from app.services import ai_roles  # AI roles can borrow an integration's key: their resolution is stale too
    ai_roles._cache.clear()
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
    ckey = f"{_workspace_override.get() or 'default'}:{provider}"
    hit = _cache.get(ckey)
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
    _cache[ckey] = (time.monotonic(), vals)
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
        self.requests = 0       # HTTP requests actually sent to the provider during this test
        self.contacted = False  # set by testers that reach a provider without HTTP (database, Redis, SMTP, crawler)
        self.failed = False
        self.auth_expired = False
        self.needs_approval = False  # the provider requires an access application/approval before the API works

    def ok(self, step: str, detail: str = "", *, sub: str | None = None, **data):
        self.items.append({"name": step, "status": "PASSED", "detail": _clean(detail), **({"sub": sub} if sub else {}),
                           **({"data": redact(data)} if data else {})})

    def fail(self, step: str, detail: str, *, hint: str = "", expired: bool = False, sub: str | None = None, approval: bool = False):
        self.items.append({"name": step, "status": "FAILED", "detail": _clean(detail), "hint": hint, **({"sub": sub} if sub else {}),
                           **({"needs_approval": True} if approval else {})})
        self.failed, self.auth_expired = True, self.auth_expired or expired
        self.needs_approval = self.needs_approval or approval

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
        return ("This Google Cloud project has Test access, which can only call Google Ads TEST accounts, and the account you entered is not a "
                "test account. Either enter a test client account (create a test manager + test client in Google Ads and use their IDs), or, to read "
                f"a real account, apply for a higher access level on the Google Ads API Overview page: {link}", "test_only")
    if "DEVELOPER_TOKEN_NOT_APPROVED" in both or "EXPLORER ACCESS" in both:
        return ("Google refused this method for the Cloud project's current API access level (Explorer). Keyword Planner "
                "(KeywordPlanIdeaService) needs Basic or Standard access: apply on the Google Ads API Center / API Overview page "
                f"({link}). OAuth, the customer ID and the account are not the problem.", "explorer_only")
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
    query = ("SELECT customer.id, customer.descriptive_name, customer.manager, customer.test_account, customer.status, "
             "customer.currency_code, customer.time_zone FROM customer LIMIT 1")
    described = []
    if target in ("all", "manager", "hierarchy"):
        # Describe every directly accessible account WITHOUT a login-customer-id: that is how a user reaches their own accounts.
        for cid in ids[:50]:
            row_ = {"customer_id": cid, "name": "", "manager": None, "status": "UNKNOWN", "test_account": False, "error": ""}
            try:
                r = _ads_search(http, base, headers, cid, query, login="")
            except httpx.HTTPError as exc:
                row_["error"] = type(exc).__name__
            else:
                if r.status_code == 200:
                    c = (r.json().get("results") or [{}])[0].get("customer", {})
                    row_.update({"name": c.get("descriptiveName", ""), "manager": bool(c.get("manager")), "status": c.get("status", "UNKNOWN"),
                                 "test_account": bool(c.get("testAccount"))})
                else:
                    row_["error"] = _ads_error_code(r)
                    if row_["error"] == "CLOUD_PROJECT_NOT_APPROVED_FOR_PRODUCTION":
                        access["level"] = "test"
                        access["basis"] = ("Google answered CLOUD_PROJECT_NOT_APPROVED_FOR_PRODUCTION for a non-test account: this project is approved "
                                           "for test accounts only.")
            described.append(row_)
        disc["accounts_checked"] = described
        managers = [d for d in described if d["manager"]]
        usable = [d for d in managers if d["status"] == "ENABLED"]
        disc["managers"] = managers
    if target in ("all", "manager"):
        if not ids:
            steps.fail("Manager accounts: find a manager (MCC)", "This Google user has no directly accessible Google Ads accounts",
                       hint="Authorise with a Google user that has access to your manager (MCC) account.", sub="manager")
        elif not usable:
            why = "; ".join(f"{d['customer_id']}: " + (f"{'manager, ' if d['manager'] else ''}{d['status'].lower()}" if not d["error"] else d["error"]) for d in described[:6])
            steps.fail("Manager accounts: find an enabled manager (MCC)", f"None of the {len(ids)} accessible account(s) is an enabled manager account ({why})",
                       hint="Authorise with a Google user who has access to an ENABLED manager (MCC) account. Closed, canceled or suspended managers cannot be used.",
                       sub="manager")
        else:
            steps.ok("Manager accounts: find an enabled manager (MCC)", f"{len(usable)} enabled manager account(s): " + ", ".join(f"{d['name'] or d['customer_id']} ({d['customer_id']})" for d in usable[:5]),
                     sub="manager", managers=[d["customer_id"] for d in usable])
    if target in ("all", "hierarchy"):
        mcc = _selected_manager(vals)
        known = {d["customer_id"]: d for d in described}
        if mcc and known.get(mcc, {}).get("manager") is False:
            steps.fail("Client accounts: the selected manager is a manager", f"{mcc} is a regular client account, not a manager (MCC) account",
                       hint="Choose a manager account on this card (or clear the default manager ID). Only manager accounts can list client accounts.", sub="hierarchy")
            return disc
        if mcc and mcc not in known and ids:
            steps.fail("Client accounts: the selected manager is reachable", f"{mcc} is not among the {len(ids)} account(s) this Google user can access directly",
                       hint="A manager you log in through must be directly accessible. Choose one of the managers listed above, or authorise with a Google user that has access to it.",
                       sub="hierarchy")
            return disc
        if not mcc:
            steps.skip("Client accounts: read the hierarchy", "No manager account has been chosen yet — choose one on this card to discover its client accounts", sub="hierarchy")
        else:
            q = "SELECT customer_client.id, customer_client.status, customer_client.manager, customer_client.level FROM customer_client WHERE customer_client.level <= 1"
            try:
                r = _ads_search(http, base, headers, mcc, q, login=mcc)
            except httpx.HTTPError as exc:
                steps.fail("Client accounts: read the hierarchy", f"Request failed: {type(exc).__name__}", sub="hierarchy")
            else:
                if r.status_code != 200:
                    hint, kind = _ads_hint(r, project)
                    steps.fail("Client accounts: read the hierarchy (customer_client)", _http_error(r), hint=hint, sub="hierarchy")
                else:
                    kids = [x["customerClient"] for x in r.json().get("results", []) if x.get("customerClient", {}).get("id") != mcc]
                    sub_mgrs = sum(1 for k in kids if k.get("manager"))
                    disc["hierarchy_preview"] = {"manager": mcc, "direct_children": len(kids), "client_accounts": len(kids) - sub_mgrs, "sub_managers": sub_mgrs}
                    steps.ok("Client accounts: read the hierarchy (customer_client)",
                             f"{len(kids)} account(s) directly under manager {mcc}: {len(kids) - sub_mgrs} client account(s), {sub_mgrs} manager account(s) "
                             "(the full tree is read when you refresh accounts)", sub="hierarchy", direct_children=len(kids),
                             client_accounts=len(kids) - sub_mgrs, sub_managers=sub_mgrs)
    return disc


def _selected_manager(vals: dict) -> str:
    """The workspace's chosen manager (database), else the optional deployment default (GOOGLE_ADS_LOGIN_CUSTOMER_ID)."""
    try:
        from app.models import Workspace
        with session_scope() as db:
            m = db.scalar(select(Workspace.google_ads_manager_customer_id).where(Workspace.slug == "default"))
    except Exception:  # noqa: BLE001 - the connection test must still run if the workspace table is unavailable
        m = ""
    return (m or vals.get("login_customer_id") or "").replace("-", "")


def _ads_error_code(r: httpx.Response) -> str:
    try:
        for d in r.json().get("error", {}).get("details", []):
            for e in d.get("errors", []):
                for v in (e.get("errorCode") or {}).values():
                    return str(v)
    except Exception:  # noqa: BLE001
        pass
    return f"HTTP_{r.status_code}"


def _test_ga4(vals: dict, http: httpx.Client, steps: Steps, row, target: str = "all") -> dict:
    """Admin API (discover properties) + Data API (real report on the selected property). Both APIs must be enabled in the Cloud project."""
    need = [k for k in ("client_id", "client_secret", "refresh_token") if not vals.get(k)]
    if need:
        steps.fail("Credentials present", f"Missing: {', '.join(need)} (the OAuth client comes from the Google Ads card)",
                   hint="Save the OAuth client on the Google Ads card, then use “Connect with Google” on this card (Analytics scope).")
        return {}
    steps.ok("Credentials present")
    token = refresh_access_token(vals, http, steps)
    if not token:
        return {}
    h = {"Authorization": f"Bearer {token}"}
    project = _cloud_project_number(vals.get("client_id", ""))
    enable = lambda svc: (f"Enable {svc} in Google Cloud project {project or '(your project)'}: https://console.cloud.google.com/apis/library/{svc}"  # noqa: E731
                          + (f"?project={project}" if project else ""))
    try:
        r = http.get("https://analyticsadmin.googleapis.com/v1beta/accountSummaries", headers=h, params={"pageSize": 200})
    except httpx.HTTPError as exc:
        steps.fail("List GA4 properties (Admin API)", f"Could not reach analyticsadmin.googleapis.com: {type(exc).__name__}")
        return {}
    if r.status_code != 200:
        steps.fail("List GA4 properties (Admin API)", _http_error(r),
                   hint=enable("analyticsadmin.googleapis.com") + ". The consent must include the analytics.readonly scope." if r.status_code == 403 else "")
        return {}
    props = [{"property": p.get("property", "").split("/")[-1], "name": p.get("displayName", ""), "account": a.get("displayName", "")}
             for a in r.json().get("accountSummaries", []) for p in a.get("propertySummaries", [])]
    steps.ok("List GA4 properties (Admin API)", f"{len(props)} propert(ies) visible to this Google user", properties=props[:100])
    pid = (vals.get("property_id") or "").replace("properties/", "")
    if not pid:
        steps.fail("Select a GA4 property", "No property is selected", hint="Choose one of the discovered properties on this card.")
        return {"properties": props}
    if props and pid not in {p["property"] for p in props}:
        steps.fail("Select a GA4 property", f"Property {pid} is not visible to the authorised Google user", hint="Pick a property from the list, or grant that user Viewer access in Analytics.")
        return {"properties": props}
    try:
        r = http.post(f"https://analyticsdata.googleapis.com/v1beta/properties/{pid}:runReport", headers=h, json={
            "dateRanges": [{"startDate": "7daysAgo", "endDate": "yesterday"}], "metrics": [{"name": "sessions"}, {"name": "keyEvents"}]})
    except httpx.HTTPError as exc:
        steps.fail("Run report (Data API)", f"Request failed: {type(exc).__name__}")
        return {"properties": props}
    if r.status_code != 200:
        steps.fail("Run report (Data API)", _http_error(r), hint=enable("analyticsdata.googleapis.com") + " and make sure the user has Viewer access to the property.")
    else:
        rows = r.json().get("rows", [])
        steps.ok("Run report (Data API)", f"Property {pid}: report answered ({len(rows)} row(s), sessions + key events, last 7 days). Never mixed into Ads conversions.")
    return {"properties": props}


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
    """Real, read-only Keyword Planner request under the configured RESEARCH customer (never a client's deployment account)."""
    need = [k for k in ("client_id", "client_secret", "refresh_token") if not vals.get(k)]
    if need:
        steps.fail("Google Ads configured", f"Needs the Google Ads OAuth credentials; missing {', '.join(need)}", hint="Configure Google Ads above first.")
        return {}
    customer = (vals.get("research_customer_id") or "").replace("-", "").strip()
    if not customer:
        steps.fail("Research customer configured", "No research customer ID is set",
                   hint="Keyword Planner requests need a Google Ads customer ID. Enter the account to use for research on this card. It is separate from any "
                        "client's deployment account and is never chosen automatically.")
        return {}
    steps.ok("Research customer configured", f"Research customer {customer} (separate from client deployment accounts)")
    from app.services import mcc_service
    with session_scope() as db_:
        login = mcc_service.effective_manager(db_)["customer_id"]
    token = refresh_access_token(vals, http, steps, "OAuth authentication")
    if not token:
        return {}
    project = _cloud_project_number(vals.get("client_id", ""))
    headers = {"Authorization": f"Bearer {token}", **({"login-customer-id": login} if login else {})}
    try:
        r = http.post(f"https://googleads.googleapis.com/{ADS_API_VERSION}/customers/{customer}:generateKeywordIdeas", headers=headers, timeout=45, json={
            "keywordSeed": {"keywords": ["plumber"]}, "language": "languageConstants/1000", "geoTargetConstants": ["geoTargetConstants/2840"], "pageSize": 5})
    except httpx.HTTPError as exc:
        steps.fail("Generate keyword ideas", f"Request failed: {type(exc).__name__}")
        return {}
    if r.status_code != 200:
        hint, access = _ads_hint(r, project)
        steps.fail("Generate keyword ideas", _http_error(r), hint=hint, approval=access == "explorer_only")
        return {"keyword_planner_access": access}
    n = len(r.json().get("results", []))
    steps.ok("Generate keyword ideas", f"Keyword Planner returned {n} idea(s) for the seed 'plumber' under research customer {customer} (read-only)")
    return {}


def _smtp_open(vals: dict, steps: Steps | None = None):
    """Open an authenticated SMTP connection with the saved/environment settings. Caller closes it."""
    import smtplib
    import ssl
    host, port = vals["host"], int(vals.get("port") or 587)
    security = (vals.get("security") or "starttls").lower()
    ctx = ssl.create_default_context()
    srv = smtplib.SMTP_SSL(host, port, timeout=15, context=ctx) if security == "ssl" else smtplib.SMTP(host, port, timeout=15)
    srv.ehlo()
    if security == "starttls":
        srv.starttls(context=ctx)
        srv.ehlo()
    if vals.get("username"):
        srv.login(vals["username"], vals.get("password", ""))
    return srv


def _test_smtp(vals: dict, http, steps: Steps, row, target: str = "all") -> dict:
    """Real connection: TCP + TLS (per setting) + EHLO + login. Sending a message is a separate, explicit action (test email)."""
    import smtplib
    if not vals.get("host") or not vals.get("from_email"):
        steps.fail("Credentials present", "SMTP host and From email are required", hint="Fill in the host, port, security and sender, then save.")
        return {}
    steps.ok("Credentials present", f"{vals['host']}:{vals.get('port') or 587} · {(vals.get('security') or 'starttls')} · login {'yes' if vals.get('username') else 'no'}")
    try:
        srv = _smtp_open(vals)
    except smtplib.SMTPAuthenticationError:
        steps.fail("Connect, secure and log in", "The server rejected the username or password", hint="Check the credentials (many providers need an app password).")
        return {}
    except (OSError, smtplib.SMTPException) as exc:
        steps.fail("Connect, secure and log in", f"{type(exc).__name__}: {str(exc)[:120]}", hint="Check host, port and security (587 = starttls, 465 = ssl) and that the server allows connections from here.")
        return {}
    try:
        steps.ok("Connect, secure and log in", f"Connected to {vals['host']} with {(vals.get('security') or 'starttls')}" + (" and authenticated" if vals.get("username") else ""))
    finally:
        try:
            srv.quit()
        except Exception:  # noqa: BLE001
            pass
    return {}


def send_test_email(db: Session, to: str) -> dict:
    """Send ONE real message to an address the administrator typed. Never used implicitly."""
    import smtplib
    from email.message import EmailMessage
    if not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", to or ""):
        raise ValidationFailed("Enter a valid recipient email address")
    vals = effective("smtp", _row(db, "smtp"))
    if not vals.get("host") or not vals.get("from_email"):
        raise ValidationFailed("Configure the SMTP host and From email first")
    msg = EmailMessage()
    msg["From"] = f"{vals.get('from_name')} <{vals['from_email']}>" if vals.get("from_name") else vals["from_email"]
    msg["To"], msg["Subject"] = to, "Google Ads AI Agent — SMTP test"
    msg.set_content("This is a test message from the Integrations page. If you received it, alert emails can be delivered.")
    try:
        srv = _smtp_open(vals)
        try:
            srv.send_message(msg)
        finally:
            srv.quit()
    except (OSError, smtplib.SMTPException) as exc:
        return {"sent": False, "error": f"{type(exc).__name__}: {str(exc)[:160]}"}
    return {"sent": True, "to": to}


def _test_database(vals: dict, http, steps: Steps, row, target: str = "all") -> dict:
    """Real checks on the database this process is actually connected to: query, TLS, write+read, migration state, pool."""
    from alembic.config import Config
    from alembic.script import ScriptDirectory
    from sqlalchemy import text

    from app.db.session import get_engine
    started = time.monotonic()
    try:
        with session_scope() as db:
            version = (db.execute(text("select version()")).scalar() or "")[:40]
            head = db.execute(text("select version_num from alembic_version")).scalar()
            ssl_on = db.execute(text("select coalesce((select ssl from pg_stat_ssl where pid = pg_backend_pid()), false)")).scalar()
            db.execute(text("create temporary table _gaa_probe (v int) on commit drop"))
            db.execute(text("insert into _gaa_probe values (42)"))
            rw = db.execute(text("select v from _gaa_probe")).scalar()
            db.rollback()
        steps.ok("Query the database", f"Answered in {int((time.monotonic() - started) * 1000)} ms ({version.split(' on ')[0]})")
        steps.ok("Write and read back", "A temporary table accepted a write and returned it (rolled back; no application data touched)") if rw == 42 else \
            steps.fail("Write and read back", "The write could not be read back", hint="Check that the database user can write.")
        (steps.ok("Encrypted connection", "The session uses TLS") if ssl_on else
         steps.skip("Encrypted connection", "This session is not using TLS (fine for local PostgreSQL; Supabase needs sslmode=require)"))
        try:
            import os
            cfg = Config(os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "alembic.ini"))
            cfg.set_main_option("script_location", os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "migrations"))
            code_head = ScriptDirectory.from_config(cfg).get_current_head()
            (steps.ok("Schema version", f"Migration {head} = latest ({code_head})") if head == code_head else
             steps.fail("Schema version", f"Database is at {head} but the code expects {code_head}", hint="Run: alembic upgrade head (the container does this at start-up)."))
        except Exception:  # noqa: BLE001
            steps.ok("Schema version", f"Migration {head}")
        pool = get_engine().pool
        steps.ok("Connection pool", f"{pool.checkedout()} in use, size {pool.size()}") if hasattr(pool, "checkedout") else None
    except Exception as exc:  # noqa: BLE001
        steps.fail("Query the database", f"{type(exc).__name__}", hint="Check DATABASE_URL, network access and that migrations ran.")
    return {}


def _test_redis(vals: dict, http, steps: Steps, row, target: str = "all") -> dict:
    """PING, Celery broker queue, live worker heartbeat, and the last scheduled-job run (the only beat signal that is observable)."""
    import redis as redis_lib
    try:
        started = time.monotonic()
        r = redis_lib.Redis.from_url(get_settings().REDIS_URL, socket_timeout=5)
        r.ping()
        steps.ok("PING", f"Answered in {int((time.monotonic() - started) * 1000)} ms")
    except Exception as exc:  # noqa: BLE001
        steps.fail("PING", f"{type(exc).__name__}", hint="Check REDIS_URL and that Redis is running.")
        return {}
    try:
        steps.ok("Broker queue", f"{r.llen('celery')} task(s) waiting in the Celery queue")
    except Exception as exc:  # noqa: BLE001
        steps.fail("Broker queue", type(exc).__name__)
    try:
        from app.workers.celery_app import celery
        replies = celery.control.inspect(timeout=2).ping() or {}
        (steps.ok("Worker heartbeat", f"{len(replies)} Celery worker(s) answered") if replies else
         steps.skip("Worker heartbeat", "WARNING: no Celery worker answered within 2 s. Redis itself is fine, but scheduled jobs and background tasks cannot run until a worker "
                                        "is started (docker compose up -d worker)."))
    except Exception as exc:  # noqa: BLE001
        steps.fail("Worker heartbeat", f"{type(exc).__name__}", hint="Start the worker: docker compose up -d worker.")
    try:
        from app.models import JobRun
        with session_scope() as db:
            last = db.scalar(select(JobRun).where(JobRun.job.like("%daily_sync%")).order_by(JobRun.created_at.desc()).limit(1))
        steps.ok("Scheduler (beat)", f"Last daily sync job: {last.status} at {last.created_at:%Y-%m-%d %H:%M} UTC") if last else \
            steps.skip("Scheduler (beat)", "No scheduled job has run yet (daily sync runs at 06:17 UTC). Beat cannot be probed directly.")
    except Exception:  # noqa: BLE001
        pass
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
    if vals["model"] not in ids:
        steps.fail("Model available", f"'{vals['model']}' is not in the list your key can use", hint="Pick a model from the list on this card.")
        return {"models": ids[:100]}
    steps.ok("Model available", vals["model"])
    started = time.monotonic()
    try:  # a real 1-token completion: listing models proves the key, not that the model answers
        r = http.post("https://api.anthropic.com/v1/messages", timeout=45, headers={"x-api-key": vals["api_key"], "anthropic-version": "2023-06-01"},
                      json={"model": vals["model"], "max_tokens": 8, "messages": [{"role": "user", "content": "Reply with OK"}]})
    except httpx.HTTPError as exc:
        steps.fail("Completion", f"Request failed: {type(exc).__name__}", hint="Check network access to api.anthropic.com.")
        return {"models": ids[:100]}
    if r.status_code != 200:
        steps.fail("Completion", _http_error(r), hint={401: "Check the API key.", 403: "The key lacks access to this model.", 429: "Rate limit or credit balance reached."}.get(r.status_code, ""))
        return {"models": ids[:100]}
    u = r.json().get("usage", {})
    steps.ok("Completion", f"The model answered in {int((time.monotonic() - started) * 1000)} ms ({u.get('input_tokens', 0)} in / {u.get('output_tokens', 0)} out tokens)")
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
        steps.fail("Text search", _http_error(r), hint="Enable “Places API (New)” (places.googleapis.com) in the key's Google Cloud project, turn on billing, and make sure the key's API restriction includes Places API (New) — Places API (New) is a different service from the legacy Places API and from the Business Profile API.")
    else:
        steps.ok("Text search", f"{len(r.json().get('places', []))} place(s) returned")
    return {}


def _test_crawler(vals: dict, http: httpx.Client, steps: Steps, row, target: str = "all") -> dict:
    """Fetches one safe public page through the SSRF-guarded crawler, and proves the guard works by refusing a private address."""
    from app.adapters.crawler import LiveCrawlerAdapter, SSRFBlocked, check_url_safe
    try:
        check_url_safe("http://169.254.169.254/latest/meta-data/")
        steps.fail("SSRF guard", "A cloud-metadata address was NOT blocked", hint="This is a security defect; do not enable the crawler.", sub="fetch")
        return {}
    except SSRFBlocked:
        steps.ok("SSRF guard", "Private/reserved addresses (e.g. 169.254.169.254) are refused before any request is made", sub="fetch")
    try:
        res = LiveCrawlerAdapter().fetch("https://example.com/")
        steps.contacted = True
        if getattr(res, "status", 0) and 200 <= res.status < 300:
            steps.ok("Fetch a page", f"HTTP {res.status} via the SSRF-guarded crawler (https://example.com/)", sub="fetch")
        else:
            steps.fail("Fetch a page", f"HTTP {getattr(res, 'status', '?')} {getattr(res, 'error', '')}".strip(), sub="fetch",
                       hint="The server could not fetch a public page: check outbound network access and DNS from the backend container.")
            return {}
    except Exception as exc:  # noqa: BLE001
        steps.fail("Fetch a page", f"{type(exc).__name__}: {getattr(exc, 'message', str(exc))}", sub="fetch")
        return {}
    try:
        from app.services import crawler_service
        crawl = crawler_service.crawl("https://example.com/", max_pages=3, max_depth=1)
        steps.ok("Crawl a small site", f"{len(crawl['pages'])} page(s) read from example.com; robots.txt {'found' if crawl['robots_found'] else 'not found'}; "
                 f"{len(crawl['errors'])} page error(s)", sub="crawl")
    except Exception as exc:  # noqa: BLE001
        steps.fail("Crawl a small site", f"{type(exc).__name__}: {getattr(exc, 'message', str(exc))}", sub="crawl")
    return {}


def _research_step(steps: Steps, name: str, fn, *, sub: str | None = None, hint: str = "") -> object:
    from app.adapters.base import AdapterError
    try:
        return fn()
    except AdapterError as exc:
        steps.fail(name, f"{exc.kind}: {exc.message}", sub=sub, hint=hint)
        return None


def _test_tavily(vals: dict, http: httpx.Client, steps: Steps, row, target: str = "all") -> dict:
    from app.adapters.research.tavily import TavilyAdapter
    if not vals.get("api_key"):
        steps.fail("Credentials present", "API key is required")
        return {}
    steps.ok("Credentials present")
    t = TavilyAdapter(vals["api_key"], http)
    res = _research_step(steps, "Run one basic web search", lambda: t.search("google ads campaign best practices", max_results=1, depth="basic"),
                         hint="Check the key on app.tavily.com; HTTP 432/433 means the plan's credits are used up.")
    if res is None:
        return {}
    steps.ok("Run one basic web search", f"{len(res['results'])} result(s); {res['credits']} credit(s) ({res['credits_source']})")
    u = _research_step(steps, "Read credit usage", t.usage)
    if u is not None:
        steps.ok("Read credit usage", f"{u.get('used')} of {u.get('limit') if u.get('limit') is not None else 'an unreported'} credits used")
        return {"usage": u}
    return {}


def _test_brave_search(vals: dict, http: httpx.Client, steps: Steps, row, target: str = "all") -> dict:
    from app.adapters.research.brave import BraveSearchAdapter
    if not vals.get("api_key"):
        steps.fail("Credentials present", "API key is required")
        return {}
    steps.ok("Credentials present")
    res = _research_step(steps, "Run one web search", lambda: BraveSearchAdapter(vals["api_key"], http).search("google ads campaign best practices", max_results=1),
                         hint="Check the subscription token in the Brave Search API dashboard; HTTP 402 means the plan's credit is used up.")
    if res is not None:
        steps.ok("Run one web search", f"{len(res['results'])} result(s) returned")
    return {}


def _test_web_fetch(vals: dict, http: httpx.Client, steps: Steps, row, target: str = "all") -> dict:
    """Opens one public page through the SSRF-guarded fetcher and extracts it; proves the guard refuses a private address."""
    from app.adapters.crawler import SSRFBlocked, check_url_safe
    from app.services import web_fetch
    try:
        check_url_safe("http://169.254.169.254/latest/meta-data/")
        steps.fail("SSRF guard", "A cloud-metadata address was NOT blocked", hint="This is a security defect; do not use Web Fetch.", sub="fetch")
        return {}
    except SSRFBlocked:
        steps.ok("SSRF guard", "Private/reserved addresses are refused before any request is made", sub="fetch")
    page = web_fetch.fetch("https://example.com/")
    steps.contacted = True
    if page.get("ok"):
        steps.ok("Fetch and extract one page", f"HTTP {page['status']} · title “{page['title']}” · {page['word_count']} words extracted from https://example.com/", sub="fetch")
    else:
        steps.fail("Fetch and extract one page", page.get("error") or "failed", sub="fetch",
                   hint="The server could not fetch a public page: check outbound network access and DNS from the backend container.")
    return {}


def _test_search_suggestions(vals: dict, http: httpx.Client, steps: Steps, row, target: str = "all") -> dict:
    from app.adapters.research.suggest import LABELS, SuggestionAdapter
    sources = [x.strip() for x in (vals.get("sources") or "").split(",") if x.strip() in LABELS]
    if not sources:
        steps.fail("Sources", "No valid source is enabled (google, duckduckgo, bing)")
        return {}
    from app.adapters.base import AdapterError
    a = SuggestionAdapter(http, enabled=tuple(sources), min_interval=0)
    got: dict[str, list | AdapterError] = {}
    for src in sources:
        try:
            got[src] = a.suggest(src, "plumber")
        except AdapterError as exc:
            got[src] = exc
    working = [s_ for s_, v in got.items() if not isinstance(v, AdapterError)]
    for src, v in got.items():  # sources are independent: one that does not answer is reported, and does not fail the others
        name = f"{LABELS[src]}: suggestions for 'plumber'"
        if not isinstance(v, AdapterError):
            steps.ok(name, f"{len(v)} suggestion(s), e.g. {', '.join(v[:3])}", sub=src)
        elif working:
            steps.skip(name, f"Unavailable ({v.kind}): {v.message}. Remove it from Sources to stop trying it.", sub=src)
        else:
            steps.fail(name, f"{v.kind}: {v.message}", sub=src)
    if not working:
        steps.fail("At least one source answered", "No suggestion source returned results from this server")
    return {"working_sources": working}


def _test_openstreetmap(vals: dict, http: httpx.Client, steps: Steps, row, target: str = "all") -> dict:
    from app.adapters.research.geo import OSMAdapter
    a = OSMAdapter(http, nominatim_url=vals.get("nominatim_url") or "https://nominatim.openstreetmap.org",
                   overpass_url=vals.get("overpass_url") or "https://overpass-api.de/api/interpreter", contact_email=vals.get("contact_email", ""))
    g = _research_step(steps, "Nominatim: geocode 'Austin, Texas'", lambda: a.geocode("Austin, Texas"), sub="geocode")
    if g is None:
        return {}
    steps.ok("Nominatim: geocode 'Austin, Texas'", f"{g['display_name'][:80]} ({g['lat']:.3f}, {g['lon']:.3f})", sub="geocode")
    p = _research_step(steps, "Overpass: plumbers within 5 km", lambda: a.nearby(g["lat"], g["lon"], 5000, [("craft", "plumber")], limit=5), sub="pois",
                       hint="The public Overpass server may be busy or unreachable from this server. Set your own Overpass URL, or rely on Geoapify.")
    if p is not None:
        steps.ok("Overpass: plumbers within 5 km", f"{len(p)} named business(es) returned", sub="pois")
    return {}


def _test_geoapify(vals: dict, http: httpx.Client, steps: Steps, row, target: str = "all") -> dict:
    from app.adapters.research.geo import GeoapifyAdapter
    if not vals.get("api_key"):
        steps.fail("Credentials present", "API key is required")
        return {}
    steps.ok("Credentials present")
    a = GeoapifyAdapter(vals["api_key"], http)
    g = _research_step(steps, "Geocode 'Austin, Texas'", lambda: a.geocode("Austin, Texas"), hint="Check the key in your Geoapify project.")
    if g is not None:
        steps.ok("Geocode 'Austin, Texas'", f"{g['display_name'][:80]}")
    return {}


_TESTERS: dict[str, Callable] = {"tavily": _test_tavily, "brave_search": _test_brave_search, "web_fetch": _test_web_fetch,
                                 "search_suggestions": _test_search_suggestions,
                                 "openstreetmap": _test_openstreetmap, "geoapify": _test_geoapify,"keyword_planner": _test_keyword_planner, "smtp": _test_smtp, "database": _test_database, "redis": _test_redis,
                                 "google_ads": _test_google_ads, "ga4": _test_ga4, "groq": _test_groq,
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
    miss = _missing_for_test(db, provider)
    if miss:  # nothing to test: do not contact the provider and do not record a test, so no timestamp suggests one happened
        return {"provider": provider, "target": target, "ok": False, "connected": False, "attempted": False, "status": serialize(db, provider)["status"],
                "steps": [{"name": "Credentials present", "status": "SKIPPED", "detail": f"Not configured — missing: {', '.join(miss)}",
                           "hint": _missing_hint(provider, miss)}],
                "summary": {"credentials_present": False, "contacted": False, "authenticated": None, "capability_available": None,
                            "failure_reason": f"Not configured: missing {', '.join(miss)}"}, "tested_at": None}
    row = _row(db, provider, create=True)
    steps = Steps()
    started = datetime.now(timezone.utc)
    if provider == "keyword_planner":
        vals = {**effective("google_ads", _row(db, "google_ads")), **effective("keyword_planner", row)}  # Ads authorisation + its own research customer
    else:
        vals = effective(provider, row)
    if spec.get("uses_google_client"):
        ads_vals = effective("google_ads", _row(db, "google_ads"))
        vals.setdefault("client_id", ads_vals.get("client_id", ""))
        vals.setdefault("client_secret", ads_vals.get("client_secret", ""))
    own = http is None
    client = http or httpx.Client(timeout=20)

    def _count(_request):
        steps.requests += 1
    client.event_hooks["request"] = [*client.event_hooks.get("request", []), _count]
    try:
        discovered = _TESTERS[provider](vals, client, steps, row, target) or {}
    finally:
        client.event_hooks["request"] = [h for h in client.event_hooks.get("request", []) if h is not _count]
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
    row.discovered = {**(row.discovered or {}), "test_summary": {**_summarize(steps, connected), "at": started.isoformat()}}
    if steps.failed:
        failed = next(s for s in steps.items if s["status"] == "FAILED")
        row.last_error = f"{failed['name']}: {failed['detail']}"
        row.status = "AUTHORIZATION_EXPIRED" if steps.auth_expired else (
            "REQUIRES_APPROVAL" if steps.needs_approval else
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
            "subtests": subs, "tested_at": started.isoformat(), "attempted": True, "summary": row.discovered["test_summary"]}


_AUTH_FAIL = re.compile(r"auth_failure|permission_denied|\b401\b|\b403\b|invalid[_ ]api[_ ]?key|unauthori[sz]ed|invalid_grant|expired|revoked|rejected", re.I)
_LOCAL_STEPS = {"Credentials present", "SSRF guard", "Sources", "Research customer configured"}


def _summarize(steps: Steps, connected: bool | None) -> dict:
    """What a person needs to know about one test: were credentials there, was the provider really contacted, did authentication work,
    is the needed capability available, and why it failed."""
    remote = [i for i in steps.items if i["name"] not in _LOCAL_STEPS and i["status"] in ("PASSED", "FAILED")]
    contacted = steps.requests > 0 or steps.contacted or bool(remote)
    failed = next((i for i in steps.items if i["status"] == "FAILED"), None)
    approval = any(i.get("needs_approval") for i in steps.items)  # authenticated fine, but the provider has not approved this use
    auth_failed = steps.auth_expired or any(i["status"] == "FAILED" and not i.get("needs_approval") and _AUTH_FAIL.search(i.get("detail", ""))
                                            for i in steps.items)
    return {"credentials_present": not any(i["name"] == "Credentials present" and i["status"] == "FAILED" for i in steps.items),
            "contacted": contacted, "requests": steps.requests,
            "authenticated": (None if not contacted else False if auth_failed else True if (approval or connected or any(i["status"] == "PASSED" for i in remote)) else None),
            "requires_approval": approval,
            "rate_limited": any(i["status"] == "FAILED" and re.search(r"rate.?limit|\b429\b", i.get("detail", ""), re.I) for i in steps.items),
            "capability_available": None if not contacted else bool(connected) if connected is not None else None,
            "failure_reason": f"{failed['name']}: {failed['detail']}" if failed else ""}


def _missing_hint(provider: str, miss: list[str]) -> str:
    if provider == "keyword_planner" and miss == ["research_customer_id"]:
        return ("Keyword Planner requests need a Google Ads customer ID. Enter the account to use for research on this card. It is separate from any "
                "client's deployment account and is never chosen automatically.")
    spec = PROVIDERS[provider]
    names = {**spec.get("settings", {}), **spec.get("secrets", {})}
    env = [_ENV.get(provider, {}).get(k) for k in miss if _ENV.get(provider, {}).get(k)]
    return (f"Enter {', '.join(names.get(k, k) for k in miss)} on this card and save" +
            (f", or set {', '.join(env)} in the backend's environment (the .env file used by Docker Compose) and restart the backend" if env else "") + ".")


def _missing_for_test(db: Session, provider: str) -> list[str]:
    spec = PROVIDERS[provider]
    if spec.get("readonly"):
        return readonly_missing(provider)
    row = _row(db, provider)
    miss = missing(provider, row)
    if provider == "keyword_planner":
        ads_vals = effective("google_ads", _row(db, "google_ads"))
        miss = [k for k in ("client_id", "client_secret", "refresh_token") if not ads_vals.get(k)] + miss
    return miss


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
LOCAL_REDIRECT = "http://localhost:5173/api/v1/integrations/google/callback"
CALLBACK_PATH = "/api/v1/integrations/google/callback"
AUTH_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"


def oauth_redirect_uri(origin: str = "") -> tuple[str, str]:
    """(redirect_uri, source). GOOGLE_ADS_REDIRECT_URI always wins. If it is blank, the address the browser is actually using (Origin) is
    used, so a Codespaces/forwarded URL works without editing .env; localhost is the last resort. The URI must be registered in the OAuth client."""
    configured = (get_settings().GOOGLE_ADS_REDIRECT_URI or "").strip()
    if configured:
        return configured, "environment (GOOGLE_ADS_REDIRECT_URI)"
    m = re.match(r"^(https?)://([^\s/?#:]+)(:\d+)?$", origin or "")
    if m and (m.group(1) == "https" or m.group(2) in ("localhost", "127.0.0.1")):  # Google rejects plain-http redirect URIs for any other host
        return origin.rstrip("/") + CALLBACK_PATH, "the address of this page"
    return LOCAL_REDIRECT, "default (localhost)"


def validate_redirect_uri(uri: str) -> str:
    from urllib.parse import urlsplit
    p = urlsplit(uri)
    if p.scheme not in ("http", "https") or not p.netloc or re.search(r"\s", uri) or p.fragment or p.query:
        raise ValidationFailed("GOOGLE_ADS_REDIRECT_URI must be one absolute http(s) URL without spaces, query or fragment, e.g. "
                               "https://your-host" + CALLBACK_PATH)
    if p.scheme == "http" and p.hostname not in ("localhost", "127.0.0.1"):
        raise ValidationFailed("Google only accepts http redirect URIs for localhost; use https for any other host")
    return uri


def build_authorization_url(*, client_id: str, redirect_uri: str, scopes: list[str], state: str, code_challenge: str) -> str:
    """The Google authorization request. Built with urlencode (every value percent-encoded) and then PARSED BACK and checked, so a request that
    is missing response_type=code, client_id, redirect_uri, scope, state or access_type=offline can never be sent to Google."""
    from urllib.parse import parse_qs, urlencode, urlsplit
    if not client_id.endswith(".apps.googleusercontent.com"):
        raise ValidationFailed("The OAuth client ID must end with .apps.googleusercontent.com")
    params = {"client_id": client_id, "redirect_uri": validate_redirect_uri(redirect_uri), "response_type": "code", "scope": " ".join(scopes),
              "access_type": "offline", "prompt": "consent", "state": state, "code_challenge": code_challenge,
              "code_challenge_method": "S256", "include_granted_scopes": "true"}
    url = AUTH_ENDPOINT + "?" + urlencode(params)
    got = {k: v[0] for k, v in parse_qs(urlsplit(url).query, keep_blank_values=True).items()}
    if got != params or got.get("response_type") != "code" or got.get("access_type") != "offline" or not all(got.get(k) for k in ("client_id", "redirect_uri", "scope", "state")):
        raise ValidationFailed("The Google authorization request could not be built correctly; nothing was sent")
    return url


def oauth_start(db: Session, providers: list[str], user_id: str, *, origin: str = "") -> dict:
    import hashlib
    import secrets

    from app.adapters.ads_oauth import new_pkce
    from app.models import OAuthState
    for p in providers:
        if not PROVIDERS.get(p, {}).get("oauth"):
            raise ValidationFailed(f"{p} does not use Google OAuth")
    ads = effective("google_ads", _row(db, "google_ads"))
    if not ads.get("client_id") or not ads.get("client_secret"):
        raise ValidationFailed("Save the OAuth client ID and secret on the Google Ads card first")
    scopes = sorted({sc for p in providers for sc in GOOGLE_SCOPES[p]} | {"openid", "email"})
    redirect_uri, source = oauth_redirect_uri(origin)
    verifier, challenge = new_pkce()
    state = secrets.token_urlsafe(32)
    url = build_authorization_url(client_id=ads["client_id"], redirect_uri=redirect_uri, scopes=scopes, state=state, code_challenge=challenge)
    db.add(OAuthState(state_hash=hashlib.sha256(state.encode()).hexdigest(), provider=",".join(providers),
                      verifier_ciphertext=encrypt_token(verifier), user_id=user_id, redirect_uri=redirect_uri,
                      expires_at=datetime.now(timezone.utc) + timedelta(minutes=10)))
    db.flush()
    return {"authorization_url": url, "redirect_uri": redirect_uri, "redirect_uri_source": source, "scopes": scopes}


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
# --------------------------------------------------------------------------------------------- dependencies
def dependencies(db: Session, provider: str) -> list[dict]:
    """What this integration needs from other integrations/state, evaluated live. Each: {key, label, ok, detail, action}.
    A dependent integration is never shown as Connected while one of these fails."""
    out: list[dict] = []

    def dep(key, label, ok, detail, action=""):
        out.append({"key": key, "label": label, "ok": bool(ok), "detail": detail, "action": action})
    if provider == "ga4":
        ads = effective("google_ads", _row(db, "google_ads"))
        have = bool(ads.get("client_id") and ads.get("client_secret"))
        dep("oauth_client", "Google OAuth client (shared, set on the Google Ads card)", have,
            "OAuth client ID and secret are saved" if have else "Save the OAuth client ID and secret on the Google Ads card first",
            "" if have else "google_ads")
        own = effective(provider, _row(db, provider))
        dep("authorisation", f"{PROVIDERS[provider]['label']} authorisation (own refresh token with its own scope)", bool(own.get("refresh_token")),
            "A refresh token with this integration's scope is saved" if own.get("refresh_token") else "Use “Connect with Google” on this card",
            "" if own.get("refresh_token") else provider)
    if provider == "keyword_planner":
        from app.services import mcc_service
        g = _row(db, "google_ads")
        connected = bool(g and g.last_test_ok is True)
        dep("google_ads", "Google Ads connected (real OAuth + enabled manager found)", connected,
            "Google Ads passed its connection test" if connected else "Run Test Connection on the Google Ads card", "google_ads")
        ws = mcc_service.default_workspace(db)
        dep("manager", "Manager (MCC) account chosen", bool(ws.google_ads_manager_customer_id),
            f"Manager {ws.google_ads_manager_customer_id}" if ws.google_ads_manager_customer_id else "Choose a manager on the Google Ads card", "google_ads")
        rc = (effective("keyword_planner", _row(db, "keyword_planner")).get("research_customer_id") or "")
        dep("research_customer", "Research customer ID set (separate from every client's deployment account)", bool(rc),
            f"Research customer {rc}" if rc else "Enter the research customer ID on this card. It is never chosen automatically and no client account is borrowed.",
            "" if rc else "keyword_planner")
        level = (((g.discovered or {}).get("cloud_access") or {}).get("level") if g else None) or "unknown"
        dep("access_level", "Google Ads API access level allows Keyword Planner (Basic or Standard)", level not in ("test",),
            "Level not known to be insufficient" if level != "test" else
            "The Cloud project is Test-access only. Keyword Planner services need Basic or Standard access", "google_ads")
    return out


def _display(provider: str, row: IntegrationConfig | None, miss: list[str], blocked: list[dict] | None = None, any_saved: bool = False) -> dict:
    """The status a person sees. Connected is shown ONLY after a real successful request AND with every dependency satisfied."""
    spec = PROVIDERS[provider]
    config = "Not configured" if miss else "No credentials needed" if not spec["required"] and not spec.get("secrets") and not spec.get("readonly") else "Configured"
    if spec.get("testable") is False:  # e.g. Google sign-in: only a real sign-in proves it, and that is not a health check of anything else
        label = "Not configured" if miss else ("Verified by signing in" if _signed_in_with(provider) else "Not verified — sign in with Google to verify")
        return {"config": config, "connection": label, "label": label, "testable": False}
    if row and row.status == "DISABLED":
        label = "Disabled"
    elif miss:
        label = "Not configured"
    elif [d for d in (blocked or []) if not d["ok"]]:
        label = "Blocked by dependency"
    elif row and row.status == "REQUIRES_APPROVAL":
        label = "Requires approval"
    elif row and row.last_test_ok is True:
        label = "Connected"
    elif row and row.last_test_ok is False and ((row.discovered or {}).get("test_summary") or {}).get("rate_limited"):
        label = "Rate limited"
    elif row and row.last_test_ok is False:
        label = "Connection failed"
    else:
        label = "Configured, not tested"  # credentials or settings exist, but no real request has succeeded or failed yet
    return {"config": config, "connection": label, "label": label, "testable": True}


def _signed_in_with(provider: str) -> bool:
    if provider != "google_login":
        return False
    from app.models import ActivityLog
    with session_scope() as db:
        return db.scalar(select(ActivityLog.id).where(ActivityLog.action == "auth.login", ActivityLog.details["provider"].as_string().ilike("%google%"))
                         .limit(1)) is not None


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
        miss = [k for k in ("client_id", "client_secret", "refresh_token") if not ads_vals.get(k)] + miss  # + research_customer_id
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
    summary = discovered.pop("test_summary", None)
    if summary is None and row and row.last_test_at:
        legacy = Steps()
        legacy.items = row.last_test_steps or []
        summary = {**_summarize(legacy, row.last_test_ok), "at": row.last_test_at.isoformat(), "inferred": True}
    info = _readonly_info(provider) if spec.get("readonly") else {}
    if provider == "google_ads":
        disc = discovered or {}
        num = _cloud_project_number(vals.get("client_id", ""))
        info = {"cloud_project_number": num or None, "console_url": f"{ADS_CONSOLE}?project={num}" if num else ADS_CONSOLE,
                "developer_token": "Not required (sunset 2026-09-09): access is granted to the Google Cloud project that owns the OAuth client",
                "api_access": disc.get("cloud_access") or {"api_enabled": None, "level": "not tested", "basis": "Run Test Connection"},
                "manager_id_set": bool(_selected_manager(vals)), "managers": disc.get("managers"),
                "hierarchy": disc.get("hierarchy_preview")}
    if provider in ("tavily", "brave_search", "openstreetmap", "geoapify", "search_suggestions"):
        from app.services import provider_calls
        names = {"search_suggestions": ["suggest_google", "suggest_duckduckgo", "suggest_bing"]}.get(provider, [provider])
        info = {"usage_30d": {n: provider_calls.usage(n) for n in names}}
    if provider == "claude":
        info = {"usage": ai_usage(db, "claude:%"), "models": (row.discovered or {}).get("models", []) if row else [],
                "independent": "Claude uses its own API key and model and drafts the same inputs separately from Groq; each side then reviews the other's draft. "
                               "If Claude is unavailable the plan is created from the single available draft and says so."}
    if provider == "ga4":
        info = {"shared_oauth_client": "Uses the OAuth client ID/secret saved on the Google Ads card; has its OWN refresh token and scope.",
                "properties": (row.discovered or {}).get("properties", []) if row else [],
                "required_apis": ["analyticsadmin.googleapis.com", "analyticsdata.googleapis.com"]}
    if provider == "places":
        info = {"required_apis": ["places.googleapis.com (Places API (New))"], "note": "Billing must be enabled and the key restricted to Places API (New). No Business Profile API is used anywhere."}
    if provider == "crawler":
        from app.adapters.crawler import MAX_BYTES
        info = {"guarantees": ["Resolves DNS itself and refuses private, loopback, link-local and reserved addresses", "Re-checks every redirect hop",
                               f"Response size limit {MAX_BYTES // 1_000_000} MB", f"Timeout {get_settings().CRAWLER_TIMEOUT_SECONDS:g} s per request",
                               "Needs no credentials"]}
    if provider == "smtp":
        info = {"transport_security": (vals.get("security") or "starttls"), "login": bool(vals.get("username")),
                "alerts_are_emailed": bool(vals.get("host") and vals.get("from_email"))}
    if provider == "groq":
        info = {"primary_model": vals.get("model") or "", "challenger_model": vals.get("challenger_model") or "",
                "challenger_falls_back": not vals.get("challenger_model"), "rate_limits": (row.discovered or {}).get("rate_limits") if row else None,
                "active_in_app": registry_ai_active()}
    deps = dependencies(db, provider)
    saved_settings = {k for k, v in ((row.settings if row else None) or {}).items() if v not in (None, "")}
    any_saved = bool(saved_settings or fps)

    def setting_source(k: str) -> str:
        if k in saved_settings:
            return "saved"
        if env_vals.get(k) and k in _ENV.get(provider, {}):
            return "environment"
        return "default" if vals.get(k) else "missing"
    return {
        "provider": provider, "label": spec["label"], "group": spec["group"], "description": spec["description"],
        "status": status, "enabled": bool(row and row.enabled), "readonly": bool(spec.get("readonly")),
        "deployment_managed": provider in DEPLOYMENT_MANAGED, "credentials_unreadable": secrets_unreadable(row),
        "dependencies": deps, "blocked_by": [d for d in deps if not d["ok"]],
        "display": _display(provider, row, miss, deps, any_saved), "subtests": subtests, "targets": spec.get("targets", {}), "info": info,
        "settings": {k: {"label": lbl, "value": (vals.get(k) if k in (row.settings if row else {}) or k in env_vals else
                                                  vals.get(k, "")) or "", "source": setting_source(k), "required": k in spec["required"]}
                     for k, lbl in spec["settings"].items()},
        "secrets": {k: {"label": lbl, "saved": bool(fps.get(k)) or bool(env_vals.get(k) and k in _ENV.get(provider, {})),
                        "fingerprint": fps.get(k) or (fingerprint(str(env_vals[k]))[:8] if env_vals.get(k) and k in _ENV.get(provider, {}) else ""),
                        "source": "saved" if fps.get(k) else ("environment" if env_vals.get(k) and k in _ENV.get(provider, {}) else ""),
                        "required": k in spec["required"]}
                    for k, lbl in spec["secrets"].items()},
        "required": spec["required"], "missing": miss, "scopes": (row.scopes if row else []) or spec.get("scopes", []),
        "oauth": bool(spec.get("oauth")), "docs": spec["docs"],
        "connected_account": row.connected_account if row else "", "account_id": row.account_id if row else "",
        "last_test_at": row.last_test_at.isoformat() if row and row.last_test_at else None,
        "last_test_ok": row.last_test_ok if row else None, "last_test_steps": row.last_test_steps if row else [],
        "last_success_at": row.last_success_at.isoformat() if row and row.last_success_at else None,
        "last_sync_at": row.last_sync_at.isoformat() if row and row.last_sync_at else None,
        "last_error": row.last_error if row else "", "discovered": discovered, "test": summary,
    }


def ai_usage(db: Session, prefix: str) -> dict:
    """Token usage of one AI provider over the last 30 days from our own run log. No price is invented: cost is in the provider's console."""
    from sqlalchemy import func

    from app.core import clock
    from app.models import AIRun
    since = clock.now() - timedelta(days=30)
    q = select(func.count(AIRun.id), func.coalesce(func.sum(AIRun.tokens_in), 0), func.coalesce(func.sum(AIRun.tokens_out), 0),
               func.coalesce(func.sum(func.cast(AIRun.validation_ok, Integer)), 0)).where(AIRun.created_at >= since, AIRun.provider.like(prefix))
    n, tin, tout, ok = db.execute(q).one()
    return {"window_days": 30, "runs": int(n), "valid_outputs": int(ok), "tokens_in": int(tin), "tokens_out": int(tout),
            "cost_note": "Tokens are counted from this application's own calls. Billing is shown in the provider's console."}


def options(db: Session, provider: str, *, http: httpx.Client | None = None) -> dict:
    """Real, read-only discovery of the choices a user must pick from (GA4 properties, Claude models).
    Nothing is saved and no connection status changes."""
    if provider not in ("ga4", "claude"):
        raise ValidationFailed(f"{provider} has no selectable options")
    row = _row(db, provider)
    vals = effective(provider, row)
    if provider == "ga4":
        ads = effective("google_ads", _row(db, "google_ads"))
        vals.setdefault("client_id", ads.get("client_id", ""))
        vals.setdefault("client_secret", ads.get("client_secret", ""))
    steps = Steps()
    own = http is None
    client = http or httpx.Client(timeout=20)
    try:
        found = _TESTERS[provider](vals, client, steps, row, "all") or {}
    finally:
        if own:
            client.close()
    failed = next((x for x in steps.items if x["status"] == "FAILED"), None)
    return {"provider": provider, "ok": failed is None or provider == "ga4" and bool(found.get("properties")),
            "error": ({"step": failed["name"], "detail": failed["detail"], "hint": failed.get("hint", "")} if failed else None),
            "properties": found.get("properties", []),
            "models": [m for m in found.get("models", []) if not NOT_CLAUDE_CHAT.search(m)] if provider == "claude" else []}


NOT_CLAUDE_CHAT = re.compile(r"embed|moderation", re.I)


def registry_ai_active() -> bool:
    """True when the app can actually use Groq (configured and not disabled)."""
    from app.core.errors import IntegrationNotConfigured
    try:
        ensure_configured("groq")
        return True
    except IntegrationNotConfigured:
        return False


def listed() -> list[str]:
    """Integrations shown and tested on the Integrations page. Groq and Claude are superseded by the Main AI / Challenger AI roles
    (which can still use their stored or environment keys by reference); their settings remain readable for that purpose."""
    return [p for p, spec in PROVIDERS.items() if not spec.get("superseded_by")]


def health(db: Session) -> dict:
    items = [serialize(db, p) for p in listed()]
    counts: dict[str, int] = {}
    for i in items:
        counts[i["display"]["label"]] = counts.get(i["display"]["label"], 0) + 1
    return {"items": [{**{k: i[k] for k in ("provider", "label", "group", "status", "enabled", "last_test_at", "last_test_ok",
                                           "last_error", "connected_account")}, "display": i["display"]} for i in items],
            "counts": counts, "mode": mode_summary(db)}


def test_all(db: Session, user_id: str | None, *, http: httpx.Client | None = None) -> list[dict]:
    out = []
    for p in listed():
        spec = PROVIDERS[p]
        row = _row(db, p)
        if spec.get("testable") is False:
            continue
        if missing(p, row) and p not in ("pagespeed", "crawler", "keyword_planner"):
            out.append({"provider": p, "ok": False, "connected": False, "status": serialize(db, p)["status"], "skipped": True,
                        "steps": [{"name": "Credentials present", "status": "SKIPPED", "detail": "Not configured"}]})
        else:
            out.append(test_connection(db, p, http=http, user_id=user_id))
    return out
