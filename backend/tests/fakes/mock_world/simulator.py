"""Deterministic performance simulator for the mock Google Ads world.

Every number is derived from (MOCK_SEED, customer, entity, date) so a scenario reproduces exactly.
All output is SIMULATED data and is stored only in mock_* tables.
"""
from __future__ import annotations

import hashlib
import random
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import MockCallRecord, MockMetricDaily, MockResource, MockSearchTerm

PROFILES: dict[str, dict] = {
    "healthy": {"spend": 0.92, "cpc": (6.0, 9.0), "cvr": 0.09, "ctr": (0.05, 0.08), "is": 0.62, "blis": 0.08, "rlis": 0.30, "junk": 0.08},
    "budget_limited": {"spend": 0.99, "cpc": (5.0, 7.5), "cvr": 0.095, "ctr": (0.05, 0.08), "is": 0.36, "blis": 0.41, "rlis": 0.23, "junk": 0.06},
    "overspend": {"spend": 1.02, "cpc": (18.0, 28.0), "cvr": 0.07, "ctr": (0.04, 0.06), "is": 0.52, "blis": 0.12, "rlis": 0.36, "junk": 0.08, "today_spike": 2.4},
    "no_conversions": {"spend": 0.88, "cpc": (6.5, 10.0), "cvr": 0.0, "ctr": (0.03, 0.05), "is": 0.48, "blis": 0.05, "rlis": 0.47, "junk": 0.20},
    "broken_tracking": {"spend": 0.9, "cpc": (6.0, 9.5), "cvr": 0.08, "ctr": (0.04, 0.07), "is": 0.55, "blis": 0.07, "rlis": 0.38, "junk": 0.10, "outage_days": 12},
    "irrelevant_terms": {"spend": 0.9, "cpc": (3.5, 6.0), "cvr": 0.035, "ctr": (0.04, 0.06), "is": 0.58, "blis": 0.05, "rlis": 0.37, "junk": 0.42},
    "insufficient_data": {"spend": 0.35, "cpc": (5.0, 8.0), "cvr": 0.06, "ctr": (0.04, 0.06), "is": 0.30, "blis": 0.02, "rlis": 0.68, "junk": 0.08},
    "landing_outage": {"spend": 0.9, "cpc": (20.0, 32.0), "cvr": 0.08, "ctr": (0.04, 0.06), "is": 0.5, "blis": 0.1, "rlis": 0.4, "junk": 0.06},
    "manual_changes": {"spend": 0.9, "cpc": (6.0, 9.0), "cvr": 0.08, "ctr": (0.05, 0.07), "is": 0.57, "blis": 0.1, "rlis": 0.33, "junk": 0.08},
    "new": {"spend": 0.78, "cpc": (5.0, 9.0), "cvr": 0.065, "ctr": (0.045, 0.07), "is": 0.42, "blis": 0.06, "rlis": 0.52, "junk": 0.12},
}

JUNK_PATTERNS = ["{s} jobs", "diy {s}", "free {s}", "how to {s}", "{s} salary", "{s} course", "{s} training"]
GOOD_PATTERNS = ["{k}", "{k} near me", "{k} cost", "best {k}", "{k} company", "local {k}", "{k} service"]


def rng(*parts) -> random.Random:
    raw = "42|" + "|".join(str(p) for p in parts)
    return random.Random(int(hashlib.sha256(raw.encode()).hexdigest()[:16], 16))


def lag_factor(days_since: int) -> float:
    """Conversions are recorded days after the click; mimic delayed reporting."""
    return {0: 0.55, 1: 0.7, 2: 0.85}.get(days_since, 1.0) if days_since >= 0 else 0.0


def _split(total: int, weights: list[float]) -> list[int]:
    if not weights:
        return []
    s = sum(weights) or 1.0
    raw = [total * w / s for w in weights]
    out = [int(x) for x in raw]
    rem = total - sum(out)
    order = sorted(range(len(raw)), key=lambda i: raw[i] - out[i], reverse=True)
    for i in order[:rem]:
        out[i] += 1
    return out


def _children(db: Session, cid: str, rtype: str, parent: str) -> list[MockResource]:
    return list(db.scalars(select(MockResource).where(
        MockResource.customer_id == cid, MockResource.resource_type == rtype,
        MockResource.parent_resource_name == parent, MockResource.status != "REMOVED",
    ).order_by(MockResource.resource_id)))


def profile_for(campaign: MockResource) -> dict:
    return PROFILES.get(campaign.data.get("scenario", "new"), PROFILES["new"])


def campaign_budget_amount(db: Session, campaign: MockResource) -> float:
    budget_rn = campaign.data.get("campaign_budget", "")
    budget = db.scalar(select(MockResource).where(MockResource.resource_name == budget_rn))
    return (budget.data.get("amount_micros", 0) if budget else 0) / 1_000_000


def _active_on(campaign: MockResource, d: date) -> bool:
    start = campaign.data.get("start_date")
    if start and d < date.fromisoformat(start):
        return False
    history = campaign.data.get("status_history") or []
    if not history:
        return campaign.status == "ENABLED"
    # status on day d = last history entry dated <= d (status changes apply from the next full day)
    status = "PAUSED"
    for entry in history:
        if date.fromisoformat(entry["date"]) < d:
            status = entry["status"]
    return status == "ENABLED"


def generate_day(db: Session, cid: str, campaign: MockResource, d: date) -> None:
    exists = db.scalar(select(MockMetricDaily.id).where(
        MockMetricDaily.customer_id == cid, MockMetricDaily.level == "CAMPAIGN",
        MockMetricDaily.entity_id == campaign.resource_id, MockMetricDaily.date == d))
    if exists or not _active_on(campaign, d):
        return
    p = profile_for(campaign)
    r = rng(cid, campaign.resource_id, d.isoformat())
    budget = campaign_budget_amount(db, campaign)
    if budget <= 0:
        return
    ramp = 1.0
    if campaign.data.get("scenario") == "new" and campaign.data.get("start_date"):
        age = (d - date.fromisoformat(campaign.data["start_date"])).days
        ramp = min(1.0, 0.35 + 0.1 * age)
    spend = budget * p["spend"] * ramp * (0.85 + 0.3 * r.random())
    cpc = r.uniform(*p["cpc"])
    clicks = max(0, int(spend / cpc))
    cost_micros = int(clicks * cpc * 1_000_000)
    ctr = r.uniform(*p["ctr"])
    impressions = int(clicks / ctr) if clicks else int(r.uniform(20, 60))
    cvr = p["cvr"]
    if "outage_days" in p:
        from app.core import clock
        if (clock.today() - d).days < p["outage_days"]:
            cvr = 0.0
    expected = clicks * cvr * (0.75 + 0.5 * r.random())
    final_conv = float(int(expected) + (1 if r.random() < expected - int(expected) else 0)) if cvr else 0.0
    form_share = r.uniform(0.5, 0.7)
    jitter = lambda v: max(0.0, min(0.95, v + r.uniform(-0.04, 0.04)))  # noqa: E731
    db.add(MockMetricDaily(customer_id=cid, campaign_id=campaign.resource_id, level="CAMPAIGN",
                           entity_id=campaign.resource_id, date=d, impressions=impressions, clicks=clicks,
                           cost_micros=cost_micros, final_conversions=final_conv, form_share=form_share,
                           search_impression_share=jitter(p["is"]), budget_lost_is=jitter(p["blis"]),
                           rank_lost_is=jitter(p["rlis"])))

    ad_groups = [g for g in _children(db, cid, "ad_group", campaign.resource_name) if g.status == "ENABLED"]
    if not ad_groups:
        return
    weights = [rng(cid, g.resource_id).uniform(0.5, 1.5) for g in ad_groups]
    g_clicks = _split(clicks, weights)
    g_conv = _split(int(final_conv), weights)
    for g, gc, gv in zip(ad_groups, g_clicks, g_conv):
        g_cost = int(cost_micros * gc / clicks) if clicks else 0
        db.add(MockMetricDaily(customer_id=cid, campaign_id=campaign.resource_id, level="AD_GROUP",
                               entity_id=g.resource_id, date=d, impressions=int(gc / ctr) if gc else 0,
                               clicks=gc, cost_micros=g_cost, final_conversions=float(gv), form_share=form_share))
        _generate_children(db, cid, campaign, g, d, gc, g_cost, gv, ctr, p)


def _generate_children(db, cid, campaign, g, d, clicks, cost, conv, ctr, p) -> None:
    kws = [k for k in _children(db, cid, "ad_group_criterion", g.resource_name)
           if k.data.get("type") == "KEYWORD" and not k.data.get("negative") and k.status == "ENABLED"]
    ads = [a for a in _children(db, cid, "ad_group_ad", g.resource_name) if a.status == "ENABLED"]
    # keyword level: the last keyword is a deliberate "money pit" in weak scenarios
    if kws:
        w = [rng(cid, k.resource_id).uniform(0.4, 1.6) for k in kws]
        kc = _split(clicks, w)
        conv_w = list(w)
        if campaign.data.get("scenario") in ("irrelevant_terms", "no_conversions", "healthy") and len(kws) > 2:
            conv_w[-1] = 0.0
        kv = _split(conv, conv_w)
        for k, c, v in zip(kws, kc, kv):
            db.add(MockMetricDaily(customer_id=cid, campaign_id=campaign.resource_id, level="KEYWORD",
                                   entity_id=k.resource_id, date=d, impressions=int(c / ctr) if c else 0, clicks=c,
                                   cost_micros=int(cost * c / clicks) if clicks else 0, final_conversions=float(v)))
    if ads:
        w = [rng(cid, a.resource_id, "ad").uniform(0.5, 1.5) for a in ads]
        ac = _split(clicks, w)
        conv_w = [x * (0.35 if a.data.get("weak") else 1.0) for x, a in zip(w, ads)]
        av = _split(conv, conv_w)
        for a, c, v, wt in zip(ads, ac, av, w):
            ad_ctr = ctr * (0.45 if a.data.get("weak") else 1.0)
            db.add(MockMetricDaily(customer_id=cid, campaign_id=campaign.resource_id, level="AD",
                                   entity_id=a.resource_id, date=d, impressions=int(c / ad_ctr) if c else 0,
                                   clicks=c, cost_micros=int(cost * c / clicks) if clicks else 0,
                                   final_conversions=float(v)))
    # search terms
    r = rng(cid, g.resource_id, d.isoformat(), "terms")
    service = g.data.get("service") or g.data.get("name", "service").split("|")[0].strip().lower()
    good = []
    for k in kws[:4]:
        for pat in GOOD_PATTERNS[:3]:
            good.append((pat.format(k=k.data["keyword"]["text"]).strip(), k.data["keyword"]["text"]))
    if not good:
        good = [(service, service)]
    good = list(dict(good).items())  # dedupe identical term variants (exact + phrase keywords)
    junk = [(pat.format(s=service.lower()), "") for pat in JUNK_PATTERNS]
    junk_clicks = int(round(clicks * p["junk"]))
    good_clicks = clicks - junk_clicks
    chosen_good = r.sample(good, k=min(len(good), 4))
    chosen_junk = r.sample(junk, k=min(len(junk), 3 if p["junk"] > 0.2 else 1))
    agg: dict[str, list] = {}
    weights = [1.0 + i * 0.3 for i in range(len(chosen_good))]
    for (term, kw), c, v in zip(chosen_good, _split(good_clicks, weights), _split(conv, weights)):
        if c:
            a = agg.setdefault(term, [kw, 0, 0.0])
            a[1] += c
            a[2] += float(v)
    fallback_kw = kws[0].data["keyword"]["text"] if kws else ""
    for (term, kw), c in zip(chosen_junk, _split(junk_clicks, [1.0] * len(chosen_junk))):
        if c:
            a = agg.setdefault(term, [kw or fallback_kw, 0, 0.0])
            a[1] += c
    for term, (kw, c, v) in agg.items():
        _add_term(db, cid, campaign, g, term, kw, d, c, int(cost * c / clicks) if clicks else 0, v, ctr)
    # calls (no caller numbers are ever stored)
    calls = int(conv * 0.45 + r.random() * 1.5)
    for i in range(calls):
        rc = rng(cid, g.resource_id, d.isoformat(), "call", i)
        answered = rc.random() > 0.18
        db.add(MockCallRecord(customer_id=cid, campaign_id=campaign.resource_id,
                              started_at=datetime(d.year, d.month, d.day, 9 + int(rc.random() * 9), int(rc.random() * 59), tzinfo=timezone.utc),
                              duration_seconds=int(rc.uniform(20, 420)) if answered else 0,
                              status="ANSWERED" if answered else "MISSED"))


def _add_term(db, cid, campaign, g, term, kw, d, clicks, cost, conv, ctr) -> None:
    existing = db.scalar(select(MockSearchTerm).where(
        MockSearchTerm.customer_id == cid, MockSearchTerm.ad_group_id == g.resource_id,
        MockSearchTerm.term == term, MockSearchTerm.date == d))
    if existing:
        existing.clicks += clicks
        existing.cost_micros += cost
        existing.conversions += conv
        existing.impressions += int(clicks / ctr)
        return
    db.add(MockSearchTerm(customer_id=cid, campaign_id=campaign.resource_id, ad_group_id=g.resource_id, term=term,
                          matched_keyword=kw, date=d, impressions=int(clicks / ctr), clicks=clicks,
                          cost_micros=cost, conversions=conv))


def generate_range(db: Session, cid: str, start: date, end: date) -> int:
    """Generate simulated days [start, end] for every campaign. Idempotent."""
    campaigns = list(db.scalars(select(MockResource).where(
        MockResource.customer_id == cid, MockResource.resource_type == "campaign")))
    n = 0
    d = start
    while d <= end:
        for c in campaigns:
            generate_day(db, cid, c, d)
        db.flush()
        n += 1
        d += timedelta(days=1)
    return n


def today_spend(db: Session, cid: str, now: datetime) -> list[dict]:
    """Spend so far today (simulated, not stored)."""
    out = []
    frac = (now.hour + now.minute / 60) / 24
    for c in db.scalars(select(MockResource).where(MockResource.customer_id == cid,
                                                   MockResource.resource_type == "campaign",
                                                   MockResource.status == "ENABLED")):
        p = profile_for(c)
        budget = campaign_budget_amount(db, c)
        r = rng(cid, c.resource_id, now.date().isoformat(), "today")
        spend = budget * p["spend"] * frac * (0.9 + 0.2 * r.random()) * p.get("today_spike", 1.0)
        out.append({"campaign_resource": c.resource_name, "campaign_id": str(c.resource_id), "name": c.data.get("name"),
                    "cost_micros": int(spend * 1_000_000), "daily_budget_micros": int(budget * 1_000_000),
                    "as_of": now.isoformat()})
    return out
