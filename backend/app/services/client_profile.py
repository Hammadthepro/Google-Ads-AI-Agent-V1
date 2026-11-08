"""Client profile intake: the single form that creates (and edits) a client + its business in one step.

Validation is deterministic and returns a field → message map, so the form can show errors as the user types.
Normalisation (phone, URLs, budget "1.5k", "Austin, TX 78701" → city/state/zip, GTM id from pasted tag code) happens here,
once, so every downstream step (intake check, audits, campaign builder) sees clean data.
"""
from __future__ import annotations

import re
from urllib.parse import urlparse

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import NotFound, ValidationFailed
from app.models import Business, BusinessService, Client, Competitor, User
from app.services import activity, auth_service, clients_service

STATES = {
    "alabama": "AL", "alaska": "AK", "arizona": "AZ", "arkansas": "AR", "california": "CA", "colorado": "CO", "connecticut": "CT",
    "delaware": "DE", "florida": "FL", "georgia": "GA", "hawaii": "HI", "idaho": "ID", "illinois": "IL", "indiana": "IN", "iowa": "IA",
    "kansas": "KS", "kentucky": "KY", "louisiana": "LA", "maine": "ME", "maryland": "MD", "massachusetts": "MA", "michigan": "MI",
    "minnesota": "MN", "mississippi": "MS", "missouri": "MO", "montana": "MT", "nebraska": "NE", "nevada": "NV", "new hampshire": "NH",
    "new jersey": "NJ", "new mexico": "NM", "new york": "NY", "north carolina": "NC", "north dakota": "ND", "ohio": "OH",
    "oklahoma": "OK", "oregon": "OR", "pennsylvania": "PA", "rhode island": "RI", "south carolina": "SC", "south dakota": "SD",
    "tennessee": "TN", "texas": "TX", "utah": "UT", "vermont": "VT", "virginia": "VA", "washington": "WA", "west virginia": "WV",
    "wisconsin": "WI", "wyoming": "WY", "district of columbia": "DC",
}
ABBR = set(STATES.values())
TYPE_HINTS = (("home_services", ("plumb", "hvac", "heating", "cooling", "electric", "roof", "landscap", "pest", "clean", "handyman",
                                 "contractor", "garage", "remodel", "paint", "locksmith", "restoration", "pool", "tree", "fence")),
              ("dental", ("dent", "orthodon", "oral")), ("legal", ("law", "attorney", "lawyer", "legal")))
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]{2,}$")
ID_RE = re.compile(r"\b(GTM-[A-Z0-9]{4,8}|G-[A-Z0-9]{6,12}|AW-\d{6,12})\b")
ZIP_RE = re.compile(r"\b(\d{5})(?:-\d{4})?\b")


def business_type_for(category: str) -> str:
    c = (category or "").lower()
    for t, words in TYPE_HINTS:
        if any(w in c for w in words):
            return t
    return "default"


def parse_budget(raw) -> float | None:
    if raw in (None, ""):
        return None
    if isinstance(raw, (int, float)):
        return float(raw)
    t = str(raw).strip().lower().replace("$", "").replace(",", "").replace(" ", "")
    mult = 1000.0 if t.endswith("k") else 1.0
    t = t.rstrip("k")
    try:
        return round(float(t) * mult, 2)
    except ValueError:
        return None


def normalize_phone(raw: str) -> str | None:
    s = (raw or "").strip()
    digits = re.sub(r"\D", "", s)
    if len(digits) == 11 and digits.startswith("1"):  # +1 512 555 0100 and 1-512-555-0100 are US numbers
        digits = digits[1:]
    elif s.startswith("+"):
        return f"+{digits}" if 8 <= len(digits) <= 15 else None
    if len(digits) == 10:
        return f"({digits[:3]}) {digits[3:6]}-{digits[6:]}"
    return None


def normalize_url(raw: str, base: str = "") -> str | None:
    s = (raw or "").strip()
    if not s:
        return ""
    if s.startswith("/") and base:
        s = base.rstrip("/") + s
    if not re.match(r"^https?://", s, re.I):
        s = "https://" + s
    p = urlparse(s)
    host = p.netloc.lower()
    if not host or "." not in host or " " in host:
        return None
    return s if p.path else s + "/"


def host_of(url: str) -> str:
    return (urlparse(url).netloc or "").lower().removeprefix("www.")


def parse_state(raw: str) -> str | None:
    t = (raw or "").strip()
    if t.upper() in ABBR:
        return t.upper()
    return STATES.get(t.lower())


def parse_area(raw: str) -> dict | None:
    """'Austin, TX 78701' → {label, city:'Austin', state:'TX', zip:'78701'}; '78701' and 'Austin' also work."""
    s = (raw or "").strip().strip(",")
    if not s:
        return None
    z = ZIP_RE.search(s)
    zip_code = z.group(1) if z else ""
    rest = ZIP_RE.sub("", s).strip(" ,")
    state = ""
    parts = [p.strip() for p in rest.split(",") if p.strip()]
    if len(parts) >= 2 and parse_state(parts[-1]):
        state, parts = parse_state(parts[-1]), parts[:-1]
    elif len(parts) == 1:
        words = parts[0].split()
        if len(words) >= 2 and parse_state(words[-1]) and words[-1].upper() in ABBR:
            state, parts = words[-1].upper(), [" ".join(words[:-1])]
    city = ", ".join(parts)
    if not city and not zip_code:
        return None
    label = ", ".join(x for x in [city, (state + (" " + zip_code if zip_code else "")).strip()] if x) if (city or state) else zip_code
    if not city and zip_code and not state:
        label = zip_code
    return {"label": label, "city": city, "state": state, "zip": zip_code}


def _clean_list(items, limit=60, maxlen=120) -> list[str]:
    out, seen = [], set()
    for x in items or []:
        t = re.sub(r"\s+", " ", str(x)).strip()
        if t and t.lower() not in seen:
            seen.add(t.lower())
            out.append(t[:maxlen])
    return out[:limit]


def validate_profile(db: Session, payload: dict, *, exclude_client_id: str | None = None) -> tuple[dict, dict]:
    """Returns (clean, errors). `errors` maps field → message; empty means valid."""
    e: dict[str, str] = {}
    p = payload or {}
    name = (p.get("name") or "").strip()
    category = (p.get("category") or "").strip()
    if not name:
        e["name"] = "Business name is required"
    elif len(name) > 200:
        e["name"] = "Business name is too long"
    if not category:
        e["category"] = "Business category is required"
    phone = normalize_phone(p.get("phone") or "") if (p.get("phone") or "").strip() else None
    if not (p.get("phone") or "").strip():
        e["phone"] = "Business phone is required"
    elif phone is None:
        e["phone"] = "Enter a valid phone, e.g. (512) 555-0100 or +1 512 555 0100"
    email = (p.get("email") or "").strip()
    if email and not EMAIL_RE.match(email):
        e["email"] = "Enter a valid email address"
    website = normalize_url(p.get("website") or "")
    if not (p.get("website") or "").strip():
        e["website"] = "Website is required"
    elif website is None:
        e["website"] = "Enter a valid website, e.g. business.com"
    lead = normalize_url(p.get("lead_form_url") or "", website or "")
    if lead is None:
        e["lead_form_url"] = "Enter a path like /contact or a full link"
    thanks = normalize_url(p.get("thank_you_url") or "", website or "")
    if thanks is None:
        e["thank_you_url"] = "Enter a path like /thank-you or a full link"
    gtm_raw = (p.get("gtm_id") or "").strip()
    gtm = ""
    if gtm_raw:
        ids = ID_RE.findall(gtm_raw.upper() if len(gtm_raw) < 20 else gtm_raw)
        if not ids:
            e["gtm_id"] = "Use GTM-XXXXXXX, G-XXXXXXXXXX or AW-XXXXXXXXX, or paste the tag code"
        else:
            gtm = ids[0]
    has_gbp = p.get("has_gbp")
    if has_gbp not in (True, False):
        e["has_gbp"] = "Choose Yes or No"
    services = _clean_list(p.get("services"), limit=30, maxlen=100)
    if not services:
        e["services"] = "Add at least one service"
    points = _clean_list(p.get("selling_points"), limit=15, maxlen=140)
    areas, bad_areas = [], []
    for raw in _clean_list(p.get("service_areas"), limit=40, maxlen=100):
        a = parse_area(raw)
        (areas if a else bad_areas).append(a or raw)
    if not areas:
        e["service_areas"] = "Add at least one city or zip code"
    elif bad_areas:
        e["service_areas"] = f"Could not read: {', '.join(bad_areas[:3])}"
    states, bad_states = [], []
    for raw in _clean_list(p.get("states"), limit=50, maxlen=40):
        s = parse_state(raw)
        (states if s else bad_states).append(s or raw)
    if bad_states:
        e["states"] = f"Unknown state: {', '.join(bad_states[:3])}"
    mode = p.get("targeting_mode")
    if mode not in ("AREAS", "RADIUS"):
        e["targeting_mode"] = "Choose how locations are targeted"
    radius = 20.0
    if mode == "RADIUS":
        try:
            radius = float(p.get("radius_miles") or 20)
            if not 1 <= radius <= 500:
                raise ValueError
        except (TypeError, ValueError):
            e["radius_miles"] = "Radius must be between 1 and 500 miles"
    budget = parse_budget(p.get("monthly_budget"))
    if p.get("monthly_budget") in (None, ""):
        e["monthly_budget"] = "Monthly ad budget is required"
    elif budget is None or budget <= 0:
        e["monthly_budget"] = "Enter an amount like 1500, 1,500 or 1.5k"
    elif budget > 1_000_000:
        e["monthly_budget"] = "That budget looks too large — check the amount"
    comps = []
    raw_comps = [c for c in (p.get("competitors") or []) if (c.get("name") or c.get("website") or c.get("maps_url") or "").strip()]
    if len(raw_comps) > 3:
        e["competitors"] = "Up to 3 competitors"
    for i, c in enumerate(raw_comps[:3]):
        cname, cweb, cmaps = (c.get("name") or "").strip(), normalize_url(c.get("website") or ""), (c.get("maps_url") or "").strip()
        if not cname and not (c.get("website") or "").strip():
            e[f"competitors.{i}"] = "Add a name or a website"
        elif cweb is None:
            e[f"competitors.{i}.website"] = "Enter a valid website"
        elif cmaps and "google." not in cmaps.lower() and "goo.gl" not in cmaps.lower():
            e[f"competitors.{i}.maps_url"] = "Paste a Google Maps link"
        else:
            comps.append({"name": cname or host_of(cweb or ""), "website": cweb or "", "maps_url": cmaps})
    if website and not e.get("website"):
        other = db.scalars(select(Business).where(Business.website_url.ilike(f"%{host_of(website)}%"))).all()
        clash = next((b for b in other if host_of(b.website_url) == host_of(website) and b.client_id != exclude_client_id), None)
        if clash:
            code = db.get(Client, clash.client_id).client_code
            e["website"] = f"This website already belongs to client {code} ({clash.name})"
    clean = {"name": name, "category": category, "phone": phone or "", "email": email, "website": website or "", "lead_form_url": lead or "",
             "thank_you_url": thanks or "", "gtm_id": gtm, "has_gbp": has_gbp, "services": services, "selling_points": points,
             "service_areas": areas, "states": states, "targeting_mode": mode, "radius_miles": radius, "monthly_budget": budget or 0,
             "competitors": comps, "notes": (p.get("notes") or "").strip()[:4000]}
    return clean, e


def _apply(db: Session, b: Business, c: dict) -> None:
    first = c["service_areas"][0]
    b.name, b.category, b.business_type = c["name"], c["category"], business_type_for(c["category"])
    b.phone, b.email, b.website_url, b.lead_form_url, b.thank_you_url, b.gtm_id = (
        c["phone"], c["email"], c["website"], c["lead_form_url"], c["thank_you_url"], c["gtm_id"])
    b.has_gbp = c["has_gbp"]
    if c["has_gbp"] is False:
        b.gbp_url = ""
    b.verified_claims = c["selling_points"]
    b.service_areas, b.states, b.targeting_mode = c["service_areas"], c["states"], c["targeting_mode"]
    b.zip_codes = sorted({a["zip"] for a in c["service_areas"] if a["zip"]})
    b.address = first["label"]  # city-level anchor used for ad copy and radius targeting
    b.default_radius_miles = c["radius_miles"] if c["targeting_mode"] == "RADIUS" else (b.default_radius_miles or 20)
    b.monthly_budget_usd = c["monthly_budget"]
    b.notes = c["notes"]
    # services: keep landing pages and the "do not advertise" list that the form does not show
    old = {s.name.lower(): s for s in b.services}
    keep_excluded = [s for s in b.services if s.is_excluded and s.name.lower() not in {x.lower() for x in c["services"]}]
    b.services.clear()
    db.flush()
    for name in c["services"]:
        prev = old.get(name.lower())
        b.services.append(BusinessService(name=name, landing_page_url=prev.landing_page_url if prev else "", is_excluded=False))
    for s in keep_excluded:
        b.services.append(BusinessService(name=s.name, landing_page_url=s.landing_page_url, is_excluded=True))
    prev_c = {x.name.lower(): x for x in b.competitors}
    b.competitors.clear()
    db.flush()
    for comp in c["competitors"]:
        old_c = prev_c.get(comp["name"].lower())
        b.competitors.append(Competitor(name=comp["name"], website_url=comp["website"], maps_url=comp["maps_url"],
                                        place_id=old_c.place_id if old_c else ""))


def _fail(errors: dict) -> None:
    raise ValidationFailed("Please fix the highlighted fields", code="profile_invalid", details={"fields": errors})


def create(db: Session, user: User, payload: dict) -> tuple[Client, Business]:
    auth_service.require(user, "clients.create")
    clean, errors = validate_profile(db, payload)
    if errors:
        _fail(errors)
    client = Client(name=clean["name"], owner_contact=user.email, created_by_id=user.id, monthly_budget_usd=clean["monthly_budget"])
    db.add(client)
    db.flush()
    b = Business(client_id=client.id, name=clean["name"])
    db.add(b)
    _apply(db, b, clean)
    db.flush()
    activity.log(db, "client.created", actor=user, entity="client", entity_id=client.id, client_id=client.id,
                 details={"client_code": client.client_code, "business": b.name})
    return client, b


def get(db: Session, user: User, client_id: str, business_id: str | None = None) -> dict:
    client = clients_service.get_client(db, user, client_id)
    b = db.get(Business, business_id) if business_id else (client.businesses[0] if client.businesses else None)
    if not b or b.client_id != client.id:
        raise NotFound("This client has no business profile")
    return to_form(client, b)


def to_form(client: Client, b: Business) -> dict:
    return {"client_id": client.id, "client_code": client.client_code, "business_id": b.id, "name": b.name, "category": b.category or b.business_type,
            "phone": b.phone, "email": b.email, "website": b.website_url, "lead_form_url": b.lead_form_url,
            "thank_you_url": b.thank_you_url, "gtm_id": b.gtm_id, "has_gbp": b.has_gbp,
            "services": [s.name for s in b.services if not s.is_excluded], "selling_points": list(b.verified_claims or []),
            "service_areas": [a["label"] for a in (b.service_areas or [])] or ([b.address] if b.address else []),
            "states": list(b.states or []), "targeting_mode": b.targeting_mode or "AREAS", "radius_miles": b.default_radius_miles,
            "monthly_budget": b.monthly_budget_usd or client.monthly_budget_usd or "",
            "competitors": [{"name": c.name, "website": c.website_url, "maps_url": c.maps_url} for c in b.competitors],
            "notes": b.notes, "updated_at": b.updated_at.isoformat(), "created_at": client.created_at.isoformat()}


def update(db: Session, user: User, client_id: str, business_id: str | None, payload: dict) -> tuple[Client, Business, list[str]]:
    auth_service.require(user, "clients.edit")
    client = clients_service.get_client(db, user, client_id)
    b = db.get(Business, business_id) if business_id else (client.businesses[0] if client.businesses else None)
    if not b or b.client_id != client.id:
        raise NotFound("This client has no business profile")
    clean, errors = validate_profile(db, payload, exclude_client_id=client.id)
    if errors:
        _fail(errors)
    before = to_form(client, b)
    _apply(db, b, clean)
    client.monthly_budget_usd = clean["monthly_budget"]
    if len(client.businesses) == 1:
        client.name = clean["name"]
    db.flush()
    after = to_form(client, b)
    changed = sorted(k for k in after if k not in ("updated_at",) and after[k] != before[k])
    activity.log(db, "client.profile_updated", actor=user, entity="client", entity_id=client.id, client_id=client.id,
                 details={"client_code": client.client_code, "changed": changed})
    return client, b, changed
