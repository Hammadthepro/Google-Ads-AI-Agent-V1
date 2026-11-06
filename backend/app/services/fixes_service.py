"""Client fix list + fix tracker (problems outside Google Ads: tracking, reviews, page speed, missing pages)."""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.adapters import registry
from app.adapters.base import AdapterError
from app.core import clock
from app.core.errors import NotFound, ValidationFailed
from app.models import Business, FixTrackerItem

STATUSES = ("OPEN", "IN_PROGRESS", "RESOLVED")


def upsert(db: Session, client_id: str, business_id: str | None, *, issue: str, category: str, priority: str = "MEDIUM",
           owner: str = "client", source: str = "audit", dedupe_key: str = "", evidence: str = "",
           auto_recheck_type: str = "") -> FixTrackerItem:
    row = db.scalar(select(FixTrackerItem).where(FixTrackerItem.dedupe_key == dedupe_key)) if dedupe_key else None
    if row:
        if row.status == "RESOLVED":
            row.status, row.resolved_at = "OPEN", None  # regression: re-open
        row.issue, row.evidence, row.priority = issue, evidence or row.evidence, priority
        return row
    row = FixTrackerItem(client_id=client_id, business_id=business_id, issue=issue, category=category, priority=priority,
                         owner=owner, source=source, dedupe_key=dedupe_key, evidence=evidence,
                         auto_recheck_type=auto_recheck_type)
    db.add(row)
    db.flush()
    return row


def resolve_by_key(db: Session, dedupe_key: str, evidence: str) -> None:
    row = db.scalar(select(FixTrackerItem).where(FixTrackerItem.dedupe_key == dedupe_key))
    if row and row.status != "RESOLVED":
        row.status, row.resolved_at = "RESOLVED", clock.now()
        row.evidence = f"{row.evidence}\nResolved: {evidence}".strip()


def update(db: Session, fix_id: str, data: dict) -> FixTrackerItem:
    row = db.get(FixTrackerItem, fix_id)
    if not row:
        raise NotFound("Fix not found")
    if "status" in data:
        if data["status"] not in STATUSES:
            raise ValidationFailed("Invalid status")
        row.status = data["status"]
        row.resolved_at = clock.now() if data["status"] == "RESOLVED" else None
    for k in ("owner", "priority", "evidence", "issue"):
        if k in data and data[k] is not None:
            setattr(row, k, data[k])
    return row


def serialize(f: FixTrackerItem) -> dict:
    return {"id": f.id, "client_id": f.client_id, "business_id": f.business_id, "issue": f.issue, "category": f.category,
            "priority": f.priority, "owner": f.owner, "status": f.status, "evidence": f.evidence, "source": f.source,
            "auto_recheck_type": f.auto_recheck_type, "created_at": f.created_at.isoformat(),
            "resolved_at": f.resolved_at.isoformat() if f.resolved_at else None}


def recheck_measurable(db: Session) -> int:
    """Worker job: re-check measurable fixes (page speed, review count) and resolve them when met."""
    n = 0
    for f in db.scalars(select(FixTrackerItem).where(FixTrackerItem.status != "RESOLVED", FixTrackerItem.auto_recheck_type != "")):
        b = db.get(Business, f.business_id) if f.business_id else None
        if not b:
            continue
        try:
            if f.auto_recheck_type.startswith("pagespeed_mobile:"):
                target = int(f.auto_recheck_type.split(":")[1])
                score = registry.get("pagespeed").run(b.website_url, "mobile")["performance_score"]
                f.evidence = f"{f.evidence}\nRe-check {clock.today()}: mobile score {score} (target {target})".strip()
                if score >= target:
                    f.status, f.resolved_at = "RESOLVED", clock.now()
            elif f.auto_recheck_type.startswith("reviews:"):
                target = int(f.auto_recheck_type.split(":")[1])
                place = registry.get("places").find_business(b.name, b.website_url, b.address)
                count = (place or {}).get("user_rating_count", 0)
                f.evidence = f"{f.evidence}\nRe-check {clock.today()}: {count} reviews (target {target})".strip()
                if count >= target:
                    f.status, f.resolved_at = "RESOLVED", clock.now()
            n += 1
        except AdapterError:
            continue
    return n
