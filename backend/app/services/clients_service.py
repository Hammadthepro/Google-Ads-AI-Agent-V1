"""Client and business intake."""
from __future__ import annotations

from urllib.parse import urlparse

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import NotFound, ValidationFailed
from app.models import Business, BusinessService, Client, Competitor, User
from app.services import activity, auth_service

BUSINESS_FIELDS = ("name", "business_type", "website_url", "gbp_url", "phone", "address", "lead_form_url",
                   "thank_you_url", "default_radius_miles", "zip_codes", "business_hours", "verified_claims",
                   "brand_rules", "competitor_ads_text", "google_ads_account_id")
CLIENT_FIELDS = ("name", "owner_user_id", "backup_owner_user_id", "owner_contact", "backup_contact", "tag_access_contact",
                 "weekly_conversion_target", "monthly_budget_usd")


def _check_url(value: str, field: str) -> None:
    if value:
        p = urlparse(value)
        if p.scheme not in ("http", "https") or not p.netloc:
            raise ValidationFailed(f"{field} must be an http(s) URL")


def serialize_client(c: Client, with_businesses: bool = True) -> dict:
    data = {"id": c.id, "client_code": c.client_code, "name": c.name, "owner_user_id": c.owner_user_id, "backup_owner_user_id": c.backup_owner_user_id,
            "owner_contact": c.owner_contact, "backup_contact": c.backup_contact, "tag_access_contact": c.tag_access_contact,
            "weekly_conversion_target": c.weekly_conversion_target, "monthly_budget_usd": c.monthly_budget_usd,
            "automation_frozen": c.automation_frozen, "flagged": c.flagged, "flag_reason": c.flag_reason,
            "created_at": c.created_at.isoformat()}
    if with_businesses:
        data["businesses"] = [serialize_business(b) for b in c.businesses]
    return data


def serialize_business(b: Business) -> dict:
    return {"id": b.id, "client_id": b.client_id, **{f: getattr(b, f) for f in BUSINESS_FIELDS},
            "services": [{"id": s.id, "name": s.name, "landing_page_url": s.landing_page_url, "is_excluded": s.is_excluded}
                         for s in b.services],
            "competitors": [{"id": c.id, "name": c.name, "website_url": c.website_url} for c in b.competitors],
            "created_at": b.created_at.isoformat()}


def list_clients(db: Session, user: User) -> list[Client]:
    ids = auth_service.accessible_client_ids(db, user)
    q = select(Client).order_by(Client.name)
    if ids is not None:
        q = q.where(Client.id.in_(ids or {"__none__"}))
    return list(db.scalars(q))


def get_client(db: Session, user: User, client_id: str) -> Client:
    c = db.get(Client, client_id)
    if not c:
        raise NotFound("Client not found")
    auth_service.require_client_access(db, user, c.id)
    return c


NUMERIC_CLIENT_FIELDS = ("weekly_conversion_target", "monthly_budget_usd")


def _coerce_numbers(data: dict) -> None:
    for k in NUMERIC_CLIENT_FIELDS:
        if k in data:
            try:
                v = float(data[k] or 0)
            except (TypeError, ValueError) as exc:
                raise ValidationFailed(f"{k} must be a number") from exc
            if v < 0:
                raise ValidationFailed(f"{k} cannot be negative")
            data[k] = v


def create_client(db: Session, user: User, data: dict) -> Client:
    auth_service.require(user, "clients.create")
    if not (data.get("name") or "").strip():
        raise ValidationFailed("Client name is required")
    _coerce_numbers(data)
    c = Client(**{k: data.get(k) or (0.0 if k in NUMERIC_CLIENT_FIELDS else "" if k not in ("owner_user_id", "backup_owner_user_id") else None)
                  for k in CLIENT_FIELDS}, created_by_id=user.id)
    db.add(c)
    db.flush()
    activity.log(db, "client.created", actor=user, entity="client", entity_id=c.id, client_id=c.id, details={"name": c.name})
    return c


def update_client(db: Session, user: User, client_id: str, data: dict) -> Client:
    auth_service.require(user, "clients.edit")
    c = get_client(db, user, client_id)
    _coerce_numbers(data)
    for k in CLIENT_FIELDS:
        if k in data:
            setattr(c, k, data[k])
    activity.log(db, "client.updated", actor=user, entity="client", entity_id=c.id, client_id=c.id, details=data)
    return c


def _apply_business(db: Session, b: Business, data: dict) -> None:
    for f in ("website_url", "gbp_url", "lead_form_url", "thank_you_url"):
        if f in data:
            _check_url(data[f] or "", f)
    for k in BUSINESS_FIELDS:
        if k in data and data[k] is not None:
            setattr(b, k, data[k])
    if "services" in data:
        b.services.clear()
        db.flush()
        for s in data["services"]:
            if (s.get("name") or "").strip():
                _check_url(s.get("landing_page_url") or "", "landing_page_url")
                b.services.append(BusinessService(name=s["name"].strip(), landing_page_url=s.get("landing_page_url") or "",
                                                  is_excluded=bool(s.get("is_excluded"))))
    if "competitors" in data:
        comps = [c for c in data["competitors"] if (c.get("name") or "").strip()]
        if len(comps) > 3:
            raise ValidationFailed("A business can have at most three competitors")
        b.competitors.clear()
        db.flush()
        for c in comps:
            _check_url(c.get("website_url") or "", "competitor website_url")
            b.competitors.append(Competitor(name=c["name"].strip(), website_url=c.get("website_url") or ""))


def create_business(db: Session, user: User, client_id: str, data: dict) -> Business:
    auth_service.require(user, "clients.edit")
    get_client(db, user, client_id)
    if not (data.get("name") or "").strip():
        raise ValidationFailed("Business name is required")
    b = Business(client_id=client_id, name=data["name"].strip())
    db.add(b)
    _apply_business(db, b, data)
    db.flush()
    activity.log(db, "business.created", actor=user, entity="business", entity_id=b.id, client_id=client_id,
                 details={"name": b.name})
    return b


def get_business(db: Session, user: User, business_id: str) -> Business:
    b = db.get(Business, business_id)
    if not b:
        raise NotFound("Business not found")
    auth_service.require_client_access(db, user, b.client_id)
    return b


def update_business(db: Session, user: User, business_id: str, data: dict) -> Business:
    auth_service.require(user, "clients.edit")
    b = get_business(db, user, business_id)
    _apply_business(db, b, data)
    activity.log(db, "business.updated", actor=user, entity="business", entity_id=b.id, client_id=b.client_id,
                 details={k: v for k, v in data.items() if k not in ("services", "competitors")})
    return b


def active_services(b: Business) -> list[BusinessService]:
    return [s for s in b.services if not s.is_excluded]


def excluded_services(b: Business) -> list[str]:
    return [s.name for s in b.services if s.is_excluded]


def city_of(b: Business) -> str:
    parts = [p.strip() for p in (b.address or "").split(",")]
    return parts[-2] if len(parts) >= 2 else (parts[0] if parts and parts[0] else "your area")
