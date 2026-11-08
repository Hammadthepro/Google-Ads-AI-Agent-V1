"""System settings (thresholds/policies) and kill switches.

Defaults are the documented TEST VALUES from docs/DECISIONS_REQUIRED.md — configurable, not final policy.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.session import session_scope
from app.models import KillSwitch, SystemSetting

DEFAULTS: dict[str, Any] = {
    # tracking gate (D-01, D-02, D-03)
    "tracking.green_min_conversions_30d": 3,
    "tracking.call_min_seconds": 60,
    "tracking.override_roles": ["SUPER_ADMIN", "ADMIN"],
    # approvals (D-04, D-07, D-08)
    "approvals.reminder_hours": 24,
    "approvals.expiry_hours": 72,
    "approvals.allow_self_approval": False,
    "approvals.budget_super_admin_pct": 20,
    "approvals.budget_super_admin_abs_usd": 500,
    # feasibility (D-13, D-15)
    "feasibility.cvr_by_business_type": {"default": [0.03, 0.06, 0.10], "home_services": [0.04, 0.07, 0.11],
                                         "dental": [0.03, 0.055, 0.09], "legal": [0.02, 0.04, 0.07]},
    "feasibility.review_adjust_max": 0.20,
    "feasibility.weeks_per_month": 4.345,
    # enough-data gate (D-18)
    "enough_data.min_clicks": 100,
    "enough_data.min_spend_multiple_of_cpl": 2.0,
    "enough_data.conversion_delay_days": 7,
    "enough_data.days_since_change": 7,
    "enough_data.min_days_since_launch": 14,
    # negatives (§22A)
    "negatives.lookback_days": 90,
    "negatives.min_clicks": 5,
    "negatives.mode": "shadow",
    # wasted spend (D-16)
    "wasted.window_days": 30,
    "wasted.min_clicks": 10,
    # monitoring
    "monitoring.budget_limited_is": 0.20,
    "monitoring.rank_limited_is": 0.30,
    "monitoring.anomaly_multiple": 2.0,
    "monitoring.weak_ad_ctr_ratio": 0.6,
    # emergency brake (D-19)
    "emergency.alert_multiple": 1.5,
    "emergency.freeze_multiple": 2.0,
    "emergency.conversion_drop_pct": 70,
    "emergency.business_hours_end_utc": 23,
    # experiments
    "experiments.min_clicks_per_arm": 100,
    "experiments.min_conversions_per_arm": 10,
    "intake.min_monthly_budget": 150,
    # operating mode: mock | read_only | execution (changed only through Integrations)
    "operating_mode": "mock",
    # real writes (second key) — only a Super Admin may change this
    "real_writes_authorized": False,
    "real_emergency_writes_authorized": False,
}

DESCRIPTIONS = {
    "real_writes_authorized": "Second key for real Google Ads writes (also needs GOOGLE_ADS_WRITES_ENABLED=true).",
    "negatives.mode": "shadow = proposals only; active = apply safe negatives (also needs AUTO_NEGATIVES_MODE=active).",
}
SUPER_ADMIN_ONLY = {"operating_mode", "real_writes_authorized", "real_emergency_writes_authorized", "tracking.override_roles"}


def get_value(key: str, default: Any = None, db: Session | None = None) -> Any:
    def _read(s: Session):
        row = s.get(SystemSetting, key)
        if row is not None:
            return row.value.get("value")
        return DEFAULTS.get(key, default)
    if db is not None:
        return _read(db)
    with session_scope() as s:
        return _read(s)


def set_value(db: Session, key: str, value: Any, user_id: str | None) -> None:
    if key not in DEFAULTS:
        raise KeyError(key)
    row = db.get(SystemSetting, key)
    if row:
        row.value = {"value": value}
        row.updated_by_id = user_id
    else:
        db.add(SystemSetting(key=key, value={"value": value}, updated_by_id=user_id))


def all_settings(db: Session) -> list[dict]:
    rows = {r.key: r for r in db.scalars(select(SystemSetting))}
    out = []
    for k, default in DEFAULTS.items():
        r = rows.get(k)
        out.append({"key": k, "value": r.value.get("value") if r else default, "default": default,
                    "is_default": r is None, "super_admin_only": k in SUPER_ADMIN_ONLY,
                    "description": DESCRIPTIONS.get(k, ""), "updated_at": r.updated_at.isoformat() if r else None})
    return out


# ---------------------------------------------------------------- kill switches
def kill_switch_on(db: Session, scope: str, scope_id: str = "") -> bool:
    row = db.scalar(select(KillSwitch).where(KillSwitch.scope == scope, KillSwitch.scope_id == scope_id))
    return bool(row and row.is_on)


def set_kill_switch(db: Session, scope: str, scope_id: str, on: bool, reason: str, user_id: str | None) -> KillSwitch:
    row = db.scalar(select(KillSwitch).where(KillSwitch.scope == scope, KillSwitch.scope_id == scope_id))
    if not row:
        row = KillSwitch(scope=scope, scope_id=scope_id)
        db.add(row)
    row.is_on, row.reason, row.set_by_id = on, reason, user_id
    db.flush()
    return row


def active_kill_switches(db: Session, client_id: str | None, account_id: str | None) -> list[str]:
    hits = []
    if kill_switch_on(db, "GLOBAL", ""):
        hits.append("GLOBAL")
    if client_id and kill_switch_on(db, "CLIENT", client_id):
        hits.append("CLIENT")
    if account_id and kill_switch_on(db, "ACCOUNT", account_id):
        hits.append("ACCOUNT")
    return hits
