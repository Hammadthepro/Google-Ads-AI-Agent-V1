"""WriteGuard — the single checkpoint every Google Ads mutation passes, including safe negatives and emergency actions."""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.adapters import registry
from app.core.config import get_settings
from app.core.errors import SafetyBlocked
from app.core.util import payload_hash
from app.models import Approval, Client, GoogleAdsAccount, User
from app.services import settings_service, measurement

POLICIES = ("APPROVAL", "AUTO_NEGATIVE", "EMERGENCY", "INTERNAL")


def check(db: Session, *, policy: str, client: Client | None, account: GoogleAdsAccount | None,
          approval: Approval | None = None, actor: User | None = None, business_id: str | None = None,
          payload: dict | None = None) -> dict:
    """Return a dict of passed checks or raise SafetyBlocked listing every failed reason."""
    reasons: list[str] = []
    ads = registry.get("google_ads")
    s = get_settings()
    if policy not in POLICIES:
        reasons.append(f"Unknown write policy {policy}")
    # 1. approval binding
    if policy == "APPROVAL":
        if not approval:
            reasons.append("No approval record")
        else:
            if approval.status != "APPROVED":
                reasons.append(f"Approval status is {approval.status}, not APPROVED")
            current = payload_hash(approval.payload)
            if current != approval.payload_hash or current != approval.approved_payload_hash:
                reasons.append("Payload hash does not match the approved version (approval invalid)")
            if payload is not None and payload_hash(payload) != approval.approved_payload_hash:
                reasons.append("Payload to execute differs from the approved payload")
    # 2. actor still authorised
    if actor is not None and (not actor.is_active or actor.role not in ("SUPER_ADMIN", "ADMIN")):
        reasons.append("Executing user is not an active Admin/Super Admin")
    # 3. kill switches
    hits = settings_service.active_kill_switches(db, client.id if client else None, account.id if account else None)
    if hits and policy != "INTERNAL":
        reasons.append(f"Kill switch on: {', '.join(hits)}")
    # 4. automation policies
    if policy == "AUTO_NEGATIVE":
        if s.AUTO_NEGATIVES_MODE != "active" or settings_service.get_value("negatives.mode", "shadow", db) != "active":
            reasons.append("Auto-negatives are in SHADOW mode (proposals only)")
        if client and client.automation_frozen:
            reasons.append("Automation is frozen for this client (emergency brake)")
        if business_id:  # a negative is judged by the ABSENCE of conversions, which only means something if conversions are being counted
            from app.models import Business
            ok, why = measurement.conversion_data_reliable(db, db.get(Business, business_id))
            if not ok:
                reasons.append(f"Automatic negatives rely on conversion data, and {why}")
    if policy == "EMERGENCY":
        if s.EMERGENCY_BRAKE_WRITE_MODE != "real" or not settings_service.get_value("real_emergency_writes_authorized", False, db):
            reasons.append("Emergency write policy not enabled (actions are simulated)")
    # 5. real-write keys (defence in depth: the real adapter re-checks)
    if ads.is_real and policy != "INTERNAL":
        if not s.GOOGLE_ADS_WRITES_ENABLED:
            reasons.append("GOOGLE_ADS_WRITES_ENABLED is false")
        if not settings_service.get_value("real_writes_authorized", False, db):
            reasons.append("Super Admin has not authorized real writes")
        if account and not account.writes_allowed:
            reasons.append(f"Account {account.customer_id} is not on the write allowlist")
    if reasons:
        raise SafetyBlocked("Write blocked by safety checks", details={"reasons": reasons, "policy": policy})
    return {"policy": policy, "adapter": ads.name, "mode": "REAL" if ads.is_real else "MOCK", "checks": "all passed"}
