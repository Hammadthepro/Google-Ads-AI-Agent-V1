"""Whether real Google Ads writes are possible, and exactly which key is missing.

Real writes need ALL of the following. Each is a separate, deliberate decision by a different control, so no single switch, setting or
conversational request can publish anything:
  1. Deployment permit   — GOOGLE_ADS_WRITES_ENABLED=true in the server environment (the operator of the deployment decides).
  2. Google Ads connected — OAuth connection enabled and passing its real connection test.
  3. Operating mode       — "Validated execution", chosen by an admin on the Integrations page.
  4. Super Admin key      — real_writes_authorized, with a written reason (audited).
  5. No kill switch       — global / client / account emergency switches all off.
  6. Account allowlist    — the deployment account itself is on the write allowlist (Super Admin, audited), enabled, not a manager.
  7. Google API access    — Google decides: the Cloud project needs an access level and permissible use that include campaign management.
                            This can only be confirmed by Google; the app reports it as unverified until a write or validate-only call succeeds.
Then every individual change still needs its own approval of the exact version, is created paused where supported, and is read back.
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models import GoogleAdsAccount
from app.services import integrations_service, settings_service


def _check(key: str, label: str, ok: bool | None, detail: str, action: str = "", who: str = "") -> dict:
    return {"key": key, "label": label, "status": "met" if ok else ("unverified" if ok is None else "missing"), "detail": detail,
            "action": "" if ok else action, "who": who}


def readiness(db: Session, account: GoogleAdsAccount | None = None) -> dict:
    s = get_settings()
    row = integrations_service._row(db, "google_ads")
    connected = bool(row and row.enabled and row.last_test_ok)
    mode = integrations_service.operating_mode(db)
    authorized = bool(settings_service.get_value("real_writes_authorized", False, db))
    switches = settings_service.active_kill_switches(db, account.client_id if account else None, account.id if account else None)
    checks = [
        _check("deployment_permit", "Deployment permit (server environment)", s.GOOGLE_ADS_WRITES_ENABLED,
               "GOOGLE_ADS_WRITES_ENABLED is true." if s.GOOGLE_ADS_WRITES_ENABLED else "GOOGLE_ADS_WRITES_ENABLED is false in the server environment.",
               "Whoever operates this deployment sets GOOGLE_ADS_WRITES_ENABLED=true and restarts the backend. It cannot be changed from the app.", "Operator"),
        _check("connection", "Google Ads connection tested", connected,
               "Connected and passing its connection test." if connected else "The Google Ads connection is not enabled or has not passed Test Connection.",
               "Open Integrations › Google Ads and run Test Connection.", "Admin"),
        _check("mode", "Operating mode: validated execution", mode == "execution",
               "Validated execution is on." if mode == "execution" else "The app is in read-only mode.",
               "Integrations › Operating mode › Validated execution (needs the deployment permit and a tested connection first).", "Admin"),
        _check("super_admin", "Super Admin authorization", authorized,
               "A Super Admin has authorized real writes." if authorized else "No Super Admin has authorized real writes.",
               "Settings › real_writes_authorized, with a written reason (audited).", "Super Admin"),
        _check("kill_switches", "Emergency switches off", not switches,
               "No emergency switch is on." if not switches else f"Kill switch on: {', '.join(switches)}.",
               "Turn the kill switch off on the Emergency page once the cause is resolved.", "Admin"),
    ]
    if account is None:
        checks.append(_check("account", "Deployment account on the write allowlist", False, "No deployment account was given for this check.",
                             "Link the client's Google Ads account, then allow it for writes on the Google Ads accounts page.", "Super Admin"))
    else:
        usable = account.status == "ENABLED" and not account.is_manager
        checks.append(_check("account", f"Account {account.customer_id} on the write allowlist", bool(account.writes_allowed and usable),
                             "Allowed for writes." if account.writes_allowed and usable else
                             ("A manager or non-enabled account can never receive campaign writes." if not usable else "Not on the write allowlist."),
                             "Google Ads accounts › Allow writes (Super Admin, with a reason)." if usable else "", "Super Admin"))
    checks.append(_check("google_access", "Google Ads API access for campaign management", None,
                         "Google grants API access to the Cloud project that owns the OAuth client. Campaign management on production accounts needs an "
                         "access level and permissible use that include it; a valid OAuth token alone does not prove this. Google confirms it only "
                         "when a validate-only or real mutate succeeds.",
                         "Check the access level and permissible use of the Cloud project in the Google Ads API Center; apply for Basic or Standard "
                         "access with campaign management if needed.", "Google"))
    app_keys = [c for c in checks if c["key"] != "google_access"]
    ready = all(c["status"] == "met" for c in app_keys)
    missing = [c["label"] for c in app_keys if c["status"] != "met"]
    return {"ready": ready, "checks": checks, "missing": missing,
            "summary": ("Real writes are possible for approved changes; Google still has to accept each one." if ready else
                        f"Real Google Ads writes are off: {len(missing)} of {len(app_keys)} keys missing ({', '.join(missing)})."),
            "per_change": ["Each change needs its own approval of the exact version and account.", "Campaigns are created paused.",
                           "Results are read back from Google Ads and compared with the approved version before anything is reported as published."]}
