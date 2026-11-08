"""API & Account Integrations: encrypted credential store, operating mode, stepwise REAL connection tests.

Rules
- Secrets are Fernet-encrypted at rest and are NEVER returned; the API shows fingerprints only.
- A connection is "Connected" only after a genuine successful request to the provider. Never inferred.
- Each test returns ordered diagnostic steps; error text is redacted before it is stored or returned.
- Credentials saved here are used by the adapters (DB first, environment variable as fallback).
"""
from __future__ import annotations

import json
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
        "description": "Account discovery, reporting, Keyword Planner and (only when explicitly authorized) campaign creation.",
        "settings": {"client_id": "OAuth client ID", "login_customer_id": "Manager (MCC) customer ID",
                     "customer_id": "Default customer ID"},
        "secrets": {"developer_token": "Developer token", "client_secret": "OAuth client secret", "refresh_token": "Refresh token"},
        "required": ["client_id", "developer_token", "client_secret", "refresh_token"],
        "oauth": True, "scopes": GOOGLE_SCOPES["google_ads"],
        "docs": "https://developers.google.com/google-ads/api/docs/oauth/overview",
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
        "description": "Uses the Google Ads connection above (same authorization); no separate credentials.",
        "settings": {}, "secrets": {}, "required": [], "depends_on": "google_ads",
        "docs": "https://developers.google.com/google-ads/api/docs/keyword-planning/overview",
    },
    "groq": {
        "label": "Groq (internal AI)", "group": "AI",
        "description": "The internal AI used for drafting and for the first side of the AI competition.",
        "settings": {"model": "Model", "challenger_model": "Challenger model (optional)"},
        "secrets": {"api_key": "API key"}, "required": ["model", "api_key"],
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
    "crawler": {
        "label": "Website crawler", "group": "Website research",
        "description": "SSRF-guarded crawler. 'live' fetches real sites; 'fixture' uses bundled demo pages.",
        "settings": {"mode": "fixture | live"}, "secrets": {}, "required": [],
        "default_settings": {"mode": "fixture"},
        "docs": "",
    },
}

STATUSES = ["NOT_CONNECTED", "CONFIGURATION_REQUIRED", "CONNECTED", "CONNECTION_FAILED", "AUTHORIZATION_EXPIRED",
            "SYNCHRONIZATION_REQUIRED"]
MODES = ("mock", "read_only", "execution")


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


def missing(provider: str, row: IntegrationConfig | None, env_fallback: bool = True) -> list[str]:
    spec = PROVIDERS[provider]
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
                   "customer_id": "GOOGLE_ADS_CUSTOMER_ID", "developer_token": "GOOGLE_ADS_DEVELOPER_TOKEN",
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


def resolve(provider: str) -> dict:
    """Credentials for adapters (own short session; cached ~15s; cleared on every save/disconnect)."""
    hit = _cache.get(provider)
    if hit and time.monotonic() - hit[0] < 15:
        return hit[1]
    with session_scope() as db:
        row = _row(db, provider)
        vals = effective(provider, row)
        vals["_enabled"] = bool(row and row.enabled)
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
    mode = settings_service.get_value("operating_mode", "mock", db)
    if mode == "mock" and get_settings().mode_for("GOOGLE_ADS") == "real":
        return "read_only"  # legacy env switch means real reads, never writes
    return mode if mode in MODES else "mock"


def set_operating_mode(db: Session, mode: str, user_id: str | None) -> dict:
    from app.services import settings_service
    if mode not in MODES:
        raise ValidationFailed(f"mode must be one of {MODES}")
    if mode != "mock":
        row = _row(db, "google_ads")
        if not (row and row.enabled and row.last_test_ok):
            raise ValidationFailed("Real modes need a Google Ads connection that has passed Test Connection and is enabled")
    if mode == "execution":
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
            "label": {"mock": "Mock mode", "read_only": "Real account — read-only",
                      "execution": "Validated execution"}[mode],
            "real_reads": mode != "mock",
            "real_writes_possible": mode == "execution" and key1 and key2,
            "write_keys": {"environment_enabled": key1, "super_admin_authorized": key2},
            "explanation": {
                "mock": "Everything runs against simulated data. Nothing can reach Google Ads.",
                "read_only": "Real Google Ads data is read. Campaign writes are impossible in this mode.",
                "execution": ("Real reads plus validated, approved writes. Campaigns are created PAUSED; enabling needs a "
                              "separate approval. Writes also need both safety keys.")}[mode]}


# --------------------------------------------------------------------------------------------- tests
class Steps:
    """Collects ordered diagnostic steps. Messages are redacted before leaving this class."""

    def __init__(self):
        self.items: list[dict] = []
        self.failed = False
        self.auth_expired = False

    def ok(self, step: str, detail: str = "", **data):
        self.items.append({"name": step, "status": "PASSED", "detail": _clean(detail), **({"data": redact(data)} if data else {})})

    def fail(self, step: str, detail: str, *, hint: str = "", expired: bool = False):
        self.items.append({"name": step, "status": "FAILED", "detail": _clean(detail), "hint": hint})
        self.failed, self.auth_expired = True, self.auth_expired or expired

    def skip(self, step: str, why: str):
        self.items.append({"name": step, "status": "SKIPPED", "detail": why})


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


def refresh_access_token(vals: dict, http: httpx.Client, steps: Steps, name: str = "Refresh OAuth access token") -> str | None:
    try:
        r = http.post("https://oauth2.googleapis.com/token", data={
            "client_id": vals.get("client_id", ""), "client_secret": vals.get("client_secret", ""),
            "refresh_token": vals.get("refresh_token", ""), "grant_type": "refresh_token"})
    except httpx.HTTPError as exc:
        steps.fail(name, f"Could not reach oauth2.googleapis.com: {type(exc).__name__}",
                   hint="Check network access to oauth2.googleapis.com from the server.")
        return None
    if r.status_code != 200:
        expired = "invalid_grant" in r.text
        steps.fail(name, _http_error(r), expired=expired,
                   hint=("The refresh token was revoked or expired — reconnect Google." if expired else
                         "Check the OAuth client ID/secret and that the refresh token belongs to this client."))
        return None
    data = r.json()
    steps.ok(name, "Access token issued", scope=data.get("scope", ""), expires_in=data.get("expires_in"))
    return data.get("access_token")


def _test_google_ads(vals: dict, http: httpx.Client, steps: Steps, row: IntegrationConfig) -> dict:
    need = [k for k in PROVIDERS["google_ads"]["required"] if not vals.get(k)]
    if need:
        steps.fail("Credentials present", f"Missing: {', '.join(need)}", hint="Fill in the missing fields and save.")
        return {}
    steps.ok("Credentials present", "All required credentials are saved")
    token = refresh_access_token(vals, http, steps)
    if not token:
        return {}
    base = f"https://googleads.googleapis.com/{ADS_API_VERSION}"
    headers = {"Authorization": f"Bearer {token}", "developer-token": vals["developer_token"]}
    try:
        r = http.get(f"{base}/customers:listAccessibleCustomers", headers=headers)
    except httpx.HTTPError as exc:
        steps.fail("List accessible customers", f"Could not reach googleads.googleapis.com: {type(exc).__name__}")
        return {}
    if r.status_code != 200:
        hint = ""
        if r.status_code in (401, 403):
            hint = ("The developer token may be missing/unapproved (test-level tokens only reach test accounts) or the "
                    "Google account lacks Google Ads access.")
        steps.fail("List accessible customers", _http_error(r), hint=hint)
        return {}
    names = r.json().get("resourceNames", [])
    ids = [n.split("/")[-1] for n in names]
    steps.ok("List accessible customers", f"{len(ids)} accessible customer(s)", customers=ids[:50])
    discovered = {"accessible_customers": ids}
    target = vals.get("customer_id") or vals.get("login_customer_id") or (ids[0] if ids else "")
    if not target:
        steps.fail("Customer accessible", "No customer ID configured and none accessible", hint="Set a customer ID.")
        return discovered
    if vals.get("customer_id") and vals["customer_id"] not in ids and not vals.get("login_customer_id"):
        steps.fail("Customer accessible", f"Customer {vals['customer_id']} is not directly accessible to this login",
                   hint="Set the manager (MCC) customer ID as login customer, or grant this user access.")
        return discovered
    steps.ok("Customer accessible", f"Using customer {target}")
    if vals.get("login_customer_id"):
        headers["login-customer-id"] = vals["login_customer_id"]

    def search(q: str):
        return http.post(f"{base}/customers/{target}/googleAds:search", headers=headers, json={"query": q, "pageSize": 5})
    try:
        r = search("SELECT customer.id, customer.descriptive_name, customer.manager, customer.currency_code, "
                   "customer.time_zone FROM customer LIMIT 1")
    except httpx.HTTPError as exc:
        steps.fail("Read account information", f"Request failed: {type(exc).__name__}")
        return discovered
    if r.status_code != 200:
        steps.fail("Read account information", _http_error(r),
                   hint="If this is a manager account, query a client account or set the login customer ID.")
        return discovered
    res = (r.json().get("results") or [{}])[0].get("customer", {})
    discovered["customer"] = {"id": res.get("id", target), "name": res.get("descriptiveName", ""),
                              "manager": res.get("manager", False), "currency": res.get("currencyCode", ""),
                              "time_zone": res.get("timeZone", "")}
    steps.ok("Read account information", f"{res.get('descriptiveName') or target}", **discovered["customer"])
    if res.get("manager"):
        steps.skip("Read campaigns", "Manager accounts hold no campaigns — link a client account to read them")
    else:
        try:
            r = search("SELECT campaign.id, campaign.name, campaign.status FROM campaign "
                       "WHERE campaign.status != 'REMOVED' LIMIT 5")
        except httpx.HTTPError as exc:
            steps.fail("Read campaigns", f"Request failed: {type(exc).__name__}")
            return discovered
        if r.status_code != 200:
            steps.fail("Read campaigns", _http_error(r))
        else:
            steps.ok("Read campaigns", f"{len(r.json().get('results', []))} campaign(s) readable (first page)")
    return discovered


def _test_ga4(vals: dict, http: httpx.Client, steps: Steps, row) -> dict:
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


def _test_gbp(vals: dict, http: httpx.Client, steps: Steps, row) -> dict:
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


def _test_groq(vals: dict, http: httpx.Client, steps: Steps, row) -> dict:
    if not vals.get("api_key") or not vals.get("model"):
        steps.fail("Credentials present", "API key and model are required")
        return {}
    steps.ok("Credentials present")
    try:
        r = http.get("https://api.groq.com/openai/v1/models", headers={"Authorization": f"Bearer {vals['api_key']}"})
    except httpx.HTTPError as exc:
        steps.fail("Authenticate / list models", f"Could not reach api.groq.com: {type(exc).__name__}")
        return {}
    if r.status_code != 200:
        steps.fail("Authenticate / list models", _http_error(r), hint="Check the API key.")
        return {}
    ids = sorted(m.get("id", "") for m in r.json().get("data", []))
    steps.ok("Authenticate / list models", f"{len(ids)} model(s) available")
    for key, label in (("model", "Model available"), ("challenger_model", "Challenger model available")):
        if vals.get(key):
            if vals[key] in ids:
                steps.ok(label, vals[key])
            else:
                steps.fail(label, f"'{vals[key]}' is not in your Groq model list", hint="Pick one of the listed models.")
    return {"models": ids}


def _test_claude(vals: dict, http: httpx.Client, steps: Steps, row) -> dict:
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


def _test_pagespeed(vals: dict, http: httpx.Client, steps: Steps, row) -> dict:
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
        steps.fail("Run PageSpeed", _http_error(r), hint="Enable the PageSpeed Insights API for the key's project.")
    else:
        score = r.json().get("lighthouseResult", {}).get("categories", {}).get("performance", {}).get("score")
        steps.ok("Run PageSpeed", f"Performance score {score}")
    return {}


def _test_places(vals: dict, http: httpx.Client, steps: Steps, row) -> dict:
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


def _test_crawler(vals: dict, http: httpx.Client, steps: Steps, row) -> dict:
    mode = vals.get("mode", "fixture")
    steps.ok("Mode", mode)
    if mode != "live":
        steps.skip("Fetch a page", "Fixture mode uses bundled pages; switch to live to test real fetching")
        return {}
    from app.adapters.crawler import LiveCrawlerAdapter
    try:
        res = LiveCrawlerAdapter().fetch("https://example.com/")
        if getattr(res, "status", 0) and 200 <= res.status < 300:
            steps.ok("Fetch a page", f"HTTP {res.status} via the SSRF-guarded crawler")
        else:
            steps.fail("Fetch a page", f"HTTP {getattr(res, 'status', '?')}")
    except Exception as exc:  # noqa: BLE001
        steps.fail("Fetch a page", f"{type(exc).__name__}: {getattr(exc, 'message', str(exc))}")
    return {}


_TESTERS: dict[str, Callable] = {"google_ads": _test_google_ads, "ga4": _test_ga4, "gbp": _test_gbp, "groq": _test_groq,
                                 "claude": _test_claude, "pagespeed": _test_pagespeed, "places": _test_places,
                                 "crawler": _test_crawler}


def test_connection(db: Session, provider: str, *, http: httpx.Client | None = None, user_id: str | None = None) -> dict:
    """Run a REAL request sequence. Persists the outcome. `http` is injectable for tests only."""
    spec = PROVIDERS[provider]
    row = _row(db, provider, create=True)
    steps = Steps()
    started = datetime.now(timezone.utc)
    if provider == "keyword_planner":
        ads = _row(db, "google_ads")
        if ads and ads.enabled and ads.last_test_ok:
            steps.ok("Google Ads connection", "Keyword Planner uses the connected Google Ads authorization")
            steps.skip("Generate ideas", "Keyword ideas are exercised during a campaign task (needs a customer ID)")
        else:
            steps.fail("Google Ads connection", "Google Ads is not connected", hint="Connect and test Google Ads first.")
        discovered: dict = {}
    else:
        vals = effective(provider, row)
        if spec.get("uses_google_client"):
            ads_vals = effective("google_ads", _row(db, "google_ads"))
            vals.setdefault("client_id", ads_vals.get("client_id", ""))
            vals.setdefault("client_secret", ads_vals.get("client_secret", ""))
        own = http is None
        client = http or httpx.Client(timeout=20)
        try:
            discovered = _TESTERS[provider](vals, client, steps, row) or {}
        finally:
            if own:
                client.close()
    row.last_test_at = started
    row.last_test_steps = steps.items
    row.last_test_ok = not steps.failed
    if discovered:
        row.discovered = {**(row.discovered or {}), **discovered}
    if steps.failed:
        failed = next(s for s in steps.items if s["status"] == "FAILED")
        row.last_error = f"{failed['name']}: {failed['detail']}"
        row.status = "AUTHORIZATION_EXPIRED" if steps.auth_expired else (
            "CONFIGURATION_REQUIRED" if failed["name"] == "Credentials present" else "CONNECTION_FAILED")
        row.enabled = False
    else:
        row.last_error, row.last_success_at = "", started
        row.status = "CONNECTED"
        info = (discovered or {}).get("customer")
        if provider == "google_ads" and info:
            row.connected_account, row.account_id = info.get("name", ""), info.get("id", "")
        row.scopes = spec.get("scopes", row.scopes)
    db.flush()
    invalidate()
    return {"provider": provider, "ok": not steps.failed, "status": row.status, "steps": steps.items,
            "tested_at": started.isoformat(), "mock": False}


def set_enabled(db: Session, provider: str, enabled: bool, user_id: str | None) -> IntegrationConfig:
    row = _row(db, provider, create=True)
    if enabled and not row.last_test_ok:
        raise ValidationFailed("Run Test Connection successfully before enabling this integration")
    row.enabled, row.updated_by_id = enabled, user_id
    if not enabled and provider == "google_ads":
        from app.services import settings_service
        if settings_service.get_value("operating_mode", "mock", db) != "mock":
            settings_service.set_value(db, "operating_mode", "mock", user_id)  # fall back to safe mode
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
def serialize(db: Session, provider: str) -> dict:
    spec = PROVIDERS[provider]
    row = _row(db, provider)
    vals = effective(provider, row)
    env_vals = effective(provider, None)
    miss = missing(provider, row)
    status = row.status if row else "NOT_CONNECTED"
    if provider == "keyword_planner":
        ads = _row(db, "google_ads")
        status = "CONNECTED" if (ads and ads.enabled and ads.last_test_ok) else ("NOT_CONNECTED" if not ads else "CONFIGURATION_REQUIRED")
    elif miss and status in ("NOT_CONNECTED", "SYNCHRONIZATION_REQUIRED") and (row or env_vals):
        status = "CONFIGURATION_REQUIRED" if (row and (row.settings or row.secret_fingerprints)) else status
    elif not row and any(env_vals.get(k) for k in spec["required"]):
        status = "SYNCHRONIZATION_REQUIRED"  # credentials exist only in the environment: never auto-"connected"
    fps = dict(row.secret_fingerprints) if row else {}
    return {
        "provider": provider, "label": spec["label"], "group": spec["group"], "description": spec["description"],
        "status": status, "enabled": bool(row and row.enabled),
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
        "last_error": row.last_error if row else "", "discovered": (row.discovered if row else {}) or {},
    }


def health(db: Session) -> dict:
    items = [serialize(db, p) for p in PROVIDERS]
    counts: dict[str, int] = {}
    for i in items:
        counts[i["status"]] = counts.get(i["status"], 0) + 1
    return {"items": [{k: i[k] for k in ("provider", "label", "group", "status", "enabled", "last_test_at", "last_test_ok",
                                         "last_error", "connected_account")} for i in items],
            "counts": counts, "mode": mode_summary(db)}


def test_all(db: Session, user_id: str | None, *, http: httpx.Client | None = None) -> list[dict]:
    out = []
    for p in PROVIDERS:
        row = _row(db, p)
        if p != "keyword_planner" and not missing(p, row) or p in ("crawler", "pagespeed"):
            out.append(test_connection(db, p, http=http, user_id=user_id))
        else:
            out.append({"provider": p, "ok": False, "status": serialize(db, p)["status"], "skipped": True,
                        "steps": [{"name": "Credentials present", "status": "SKIPPED", "detail": "Not configured"}]})
    return out
