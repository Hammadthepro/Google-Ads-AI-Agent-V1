"""Enough-data gate (12), safe negative keywords (13a) and approval-based big changes (13b).

Only safe negatives may ever be automatic, and only in ACTIVE mode with reliable conversion data; V1 starts in SHADOW
(proposals only). Everything else becomes an approval card. Deletes are never proposed."""
from __future__ import annotations

from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core import clock
from app.core.config import get_settings
from app.core.errors import NotFound, SafetyBlocked, ValidationFailed
from app.core.util import normalize_term
from app.models import (AdGroup, Business, Campaign, Client, GoogleAdsAccount, Keyword, NegativeKeyword, PerformanceSnapshot,
                        Recommendation, SearchTermRow, User)
from app.services import activity, approvals, execution, settings_service, measurement, validation

PATTERNS = ["jobs", "job", "diy", "free", "salary", "course", "training", "how to"]


def _days(dt) -> int | None:
    return (clock.now() - dt).days if dt else None


def target_cpl(c: Campaign) -> float | None:
    if c.weekly_conversion_target and c.monthly_budget_micros:
        return c.monthly_budget_micros / 1e6 / (c.weekly_conversion_target * 4.345)
    return None


def enough_data(db: Session, campaign: Campaign, *, item_changed_at=None, needs_conversions: bool = True) -> dict:
    """All conditions must pass. Thresholds are configurable test values (D-18)."""
    g = lambda k, d: settings_service.get_value(k, d, db)  # noqa: E731
    end = clock.today() - timedelta(days=1)
    clicks, cost = db.execute(select(func.coalesce(func.sum(PerformanceSnapshot.clicks), 0),
                                     func.coalesce(func.sum(PerformanceSnapshot.cost_micros), 0)).where(
        PerformanceSnapshot.level == "CAMPAIGN", PerformanceSnapshot.entity_external_id == campaign.external_id,
        PerformanceSnapshot.google_ads_account_id == campaign.google_ads_account_id,
        PerformanceSnapshot.date >= end - timedelta(days=29))).one()
    cost = float(cost) / 1e6
    cpl = target_cpl(campaign)
    min_clicks, mult = int(g("enough_data.min_clicks", 100)), float(g("enough_data.min_spend_multiple_of_cpl", 2.0))
    delay, since_change = int(g("enough_data.conversion_delay_days", 7)), int(g("enough_data.days_since_change", 7))
    launched_days = _days(campaign.launched_at)
    changed = item_changed_at or campaign.last_changed_at
    business = db.get(Business, campaign.business_id) if campaign.business_id else None
    checks = [
        {"key": "volume", "label": f"≥{min_clicks} clicks or spend ≥ {mult}× target CPL (30 days)",
         "passed": clicks >= min_clicks or (cpl is not None and cost >= mult * cpl),
         "value": f"{int(clicks)} clicks, ${cost:,.2f}" + (f" (target CPL ${cpl:,.2f})" if cpl else "")},
        {"key": "conversion_delay", "label": f"Lead-recording delay passed (≥{delay} days live)",
         "passed": launched_days is None or launched_days >= delay, "value": f"{launched_days} days since launch" if launched_days is not None else "existing campaign"},
        {"key": "learning", "label": "Not in Google's learning phase",
         "passed": not (campaign.bidding_system_status or "").startswith("LEARNING"), "value": campaign.bidding_system_status or "n/a"},
        {"key": "since_change", "label": f"≥{since_change} days since the last change to this item",
         "passed": changed is None or _days(changed) >= since_change, "value": f"{_days(changed)} days" if changed else "no recorded change"},
    ]
    if needs_conversions:
        ok, why = measurement.conversion_data_reliable(db, business)  # this recommendation is judged by conversion counts
        checks.append({"key": "conversion_data", "label": "Conversions are recorded and counted once", "passed": ok, "value": why})
    go = all(c["passed"] for c in checks)
    return {"decision": "GO" if go else "HOLD", "checks": checks,
            "hold_reason": "; ".join(f"{c['label']} — {c['value']}" for c in checks if not c["passed"])}


def _rec(db, *, client_id, account_id, campaign_id, category, size, title, proposal, evidence, gate, dedupe_key, source="RULES"):
    existing = db.scalar(select(Recommendation).where(Recommendation.dedupe_key == dedupe_key,
                                                      Recommendation.status.in_(["OPEN", "PROPOSED", "SHADOW", "HELD"])))
    if existing:
        existing.enough_data, existing.decision = gate, gate["decision"]
        existing.hold_reason, existing.evidence = gate.get("hold_reason", ""), evidence
        if existing.status in ("OPEN", "HELD"):
            existing.status = "HELD" if gate["decision"] == "HOLD" else "OPEN"
        return existing
    r = Recommendation(client_id=client_id, google_ads_account_id=account_id, campaign_id=campaign_id, category=category, size=size,
                       source=source, title=title, proposal=proposal, evidence=evidence, enough_data=gate, decision=gate["decision"],
                       hold_reason=gate.get("hold_reason", ""), status="HELD" if gate["decision"] == "HOLD" else "OPEN",
                       dedupe_key=dedupe_key)
    db.add(r)
    db.flush()
    return r


def negative_candidates(db: Session, account: GoogleAdsAccount, campaign: Campaign) -> list[dict]:
    lookback_ok = int(settings_service.get_value("negatives.lookback_days", 90, db))
    min_clicks = int(settings_service.get_value("negatives.min_clicks", 5, db))
    terms = list(db.scalars(select(SearchTermRow).where(SearchTermRow.google_ads_account_id == account.id,
                                                        SearchTermRow.campaign_external_id == campaign.external_id,
                                                        SearchTermRow.window_days == 30)))
    converting90 = [normalize_term(t.term) for t in db.scalars(select(SearchTermRow).where(
        SearchTermRow.google_ads_account_id == account.id, SearchTermRow.window_days == 90, SearchTermRow.conversions > 0))]
    groups = {g.external_id: g for g in db.scalars(select(AdGroup).where(AdGroup.campaign_id == campaign.id))}
    active_kw = {normalize_term(k.text) for g in groups.values()
                 for k in db.scalars(select(Keyword).where(Keyword.ad_group_id == g.id, Keyword.status != "REMOVED"))}
    existing_neg = {normalize_term(n.text) for n in db.scalars(select(NegativeKeyword).where(NegativeKeyword.google_ads_account_id == account.id))}
    term_groups: dict[str, set] = {}
    for t in terms:
        term_groups.setdefault(normalize_term(t.term), set()).add(t.ad_group_external_id)
    out, seen = [], set()
    for t in terms:
        n = normalize_term(t.term)
        if n in seen or n in existing_neg:
            continue
        seen.add(n)
        irrelevant = any(p in n.split() or n.startswith(p) for p in PATTERNS)
        if t.clicks < min_clicks or t.conversions > 0:
            continue
        if not irrelevant and t.clicks < int(settings_service.get_value("wasted.min_clicks", 10, db)):
            continue
        safety = []
        safety.append({"check": f"No lead in the last {lookback_ok} days", "passed": (t.conversions_90d or 0) == 0})
        safety.append({"check": "Does not match an active keyword", "passed": n not in active_kw})
        safety.append({"check": "Does not block a converting search term", "passed": n not in converting90})
        level = "CAMPAIGN" if len(term_groups.get(n, ())) > 1 or len(groups) <= 1 else "AD_GROUP"
        out.append({"term": t.term, "match_type": "EXACT", "level": level, "ad_group_external_id": t.ad_group_external_id,
                    "clicks": t.clicks, "cost": round(t.cost_micros / 1e6, 2), "irrelevant_pattern": irrelevant,
                    "safety": safety, "safe": all(s["passed"] for s in safety)})
    # phrase negatives from word patterns, tested against every converting term
    for p in PATTERNS:
        hits = [c for c in out if p in normalize_term(c["term"]).split()]
        if len(hits) >= 2 and p not in existing_neg:
            blocks = [c for c in converting90 if p in c.split()]
            out.append({"term": p, "match_type": "PHRASE", "level": "SHARED_LIST", "clicks": sum(h["clicks"] for h in hits),
                        "cost": round(sum(h["cost"] for h in hits), 2), "irrelevant_pattern": True, "pattern_hits": [h["term"] for h in hits],
                        "safety": [{"check": "Pattern matches no converting search term (90 days)", "passed": not blocks,
                                    "detail": blocks[:5]},
                                   {"check": "Does not match an active keyword", "passed": not any(p in k.split() for k in active_kw)}],
                        "safe": not blocks and not any(p in k.split() for k in active_kw)})
    return out


def _negative_op(campaign: Campaign, groups: dict, cand: dict, shared_rn: str | None) -> dict | None:
    kw = {"text": cand["term"].lower(), "match_type": cand["match_type"]}
    if cand["level"] == "AD_GROUP" and cand.get("ad_group_external_id") in groups:
        return {"resource": "ad_group_criterion", "op": "create",
                "fields": {"ad_group": groups[cand["ad_group_external_id"]].external_resource_name, "type": "KEYWORD", "negative": True, "keyword": kw}}
    if cand["level"] == "SHARED_LIST" and shared_rn:
        return {"resource": "shared_criterion", "op": "create", "fields": {"shared_set": shared_rn, "type": "KEYWORD", "keyword": kw}}
    return {"resource": "campaign_criterion", "op": "create",
            "fields": {"campaign": campaign.external_resource_name, "type": "KEYWORD", "negative": True, "keyword": kw}}


def run_for_client(db: Session, client: Client) -> dict:
    from app.adapters import registry
    from app.services.planner import SHARED_LIST_NAME
    ads = registry.get("google_ads")
    limit = get_settings().AUTO_NEGATIVES_DAILY_LIMIT
    summary = {"negatives_proposed": 0, "negatives_applied": 0, "negatives_held": 0, "big_changes": 0, "held": 0, "mode": ""}
    mode = "active" if (settings_service.get_value("negatives.mode", "shadow", db) == "active" and
                        get_settings().AUTO_NEGATIVES_MODE == "active") else "shadow"
    summary["mode"] = mode
    today_start = clock.now().replace(hour=0, minute=0, second=0, microsecond=0)
    applied_today = db.scalar(select(func.count(Recommendation.id)).where(Recommendation.client_id == client.id,
                                                                          Recommendation.category == "NEGATIVE_KEYWORD",
                                                                          Recommendation.status == "APPLIED",
                                                                          Recommendation.updated_at >= today_start)) or 0
    for account in db.scalars(select(GoogleAdsAccount).where(GoogleAdsAccount.client_id == client.id, GoogleAdsAccount.is_manager.is_(False))):
        shared = [s for s in ads.list_resources(account.customer_id, "shared_set") if s.get("name") == SHARED_LIST_NAME]
        shared_rn = shared[0]["resource_name"] if shared else None
        for c in db.scalars(select(Campaign).where(Campaign.google_ads_account_id == account.id, Campaign.campaign_type == "SEARCH")):
            groups = {g.external_id: g for g in db.scalars(select(AdGroup).where(AdGroup.campaign_id == c.id))}
            gate = enough_data(db, c)
            to_apply = []
            for cand in negative_candidates(db, account, c):
                if not cand["safe"]:
                    continue
                key = f"neg:{c.id}:{cand['level']}:{normalize_term(cand['term'])}"
                op = _negative_op(c, groups, cand, shared_rn)
                if cand["level"] == "SHARED_LIST" and not shared_rn:
                    op = _negative_op(c, groups, {**cand, "level": "CAMPAIGN"}, None)
                rec = _rec(db, client_id=client.id, account_id=account.id, campaign_id=c.id, category="NEGATIVE_KEYWORD", size="SMALL",
                           title=f"Add {cand['match_type'].lower()} negative '{cand['term']}' ({cand['level'].lower().replace('_', ' ')}) to {c.name}",
                           proposal={"operation": op, "candidate": cand}, evidence={"clicks": cand["clicks"], "cost": cand["cost"],
                                                                                   "safety": cand["safety"]}, gate=gate, dedupe_key=key)
                if rec.status == "HELD":
                    summary["negatives_held"] += 1
                    continue
                if mode == "shadow":
                    if rec.status == "OPEN":
                        rec.status = "SHADOW"
                    summary["negatives_proposed"] += 1
                elif applied_today + len(to_apply) < limit:
                    to_apply.append((rec, op))
            if to_apply:
                b = db.get(Business, c.business_id) if c.business_id else None
                try:
                    op_row = execution.execute_policy(db, policy="AUTO_NEGATIVE", client=client, account=account,
                                                      operations=[o for _, o in to_apply], business_id=b.id if b else None,
                                                      reason="Safe negative keywords", evidence={"recommendations": [r.id for r, _ in to_apply]})
                    for r, _ in to_apply:
                        r.status = "APPLIED" if op_row.state == "SUCCEEDED" else "OPEN"
                        r.operation_id = op_row.id
                    summary["negatives_applied"] += len(to_apply) if op_row.state == "SUCCEEDED" else 0
                    applied_today += len(to_apply)
                except SafetyBlocked as exc:
                    for r, _ in to_apply:
                        r.status, r.hold_reason = "SHADOW", "; ".join(exc.details.get("reasons", []))
            big = _big_changes(db, client, account, c, gate)
            summary["big_changes"] += big["proposed"]
            summary["held"] += big["held"]
    activity.log(db, "optimization.run", entity="client", entity_id=client.id, client_id=client.id, details=summary)
    db.flush()
    return summary


def _big_changes(db: Session, client: Client, account: GoogleAdsAccount, c: Campaign, gate: dict) -> dict:
    end = clock.today() - timedelta(days=1)
    q = select(func.sum(PerformanceSnapshot.clicks), func.sum(PerformanceSnapshot.cost_micros), func.sum(PerformanceSnapshot.conversions),
               func.avg(PerformanceSnapshot.budget_lost_is), func.avg(PerformanceSnapshot.rank_lost_is)).where(
        PerformanceSnapshot.level == "CAMPAIGN", PerformanceSnapshot.entity_external_id == c.external_id,
        PerformanceSnapshot.google_ads_account_id == account.id, PerformanceSnapshot.date >= end - timedelta(days=29))
    clicks, cost, conv, blis, rlis = db.execute(q).one()
    clicks, cost, conv = int(clicks or 0), float(cost or 0) / 1e6, float(conv or 0)
    blis, rlis = (float(x) if x is not None else None for x in (blis, rlis))
    cpl = cost / conv if conv else None
    tcpl = target_cpl(c)
    out = {"proposed": 0, "held": 0}

    def add(category, title, proposal, evidence, key, needs_conv=True):
        g = gate if needs_conv else enough_data(db, c, needs_conversions=False)
        r = _rec(db, client_id=client.id, account_id=account.id, campaign_id=c.id, category=category, size="BIG", title=title,
                 proposal=proposal, evidence=evidence, gate=g, dedupe_key=key)
        out["held" if r.status == "HELD" else "proposed"] += 1

    blim = float(settings_service.get_value("monitoring.budget_limited_is", 0.2, db))
    if blis and blis > blim and cpl and (tcpl is None or cpl <= tcpl * 1.1):
        new_daily = round(c.daily_budget_micros / 1e6 * 1.2, 2)
        add("BUDGET", f"Raise {c.name} budget 20% (losing {blis:.0%} impression share to budget)",
            {"kind": "CHANGE_BUDGET", "new_monthly": round(new_daily * 30.4, 2)},
            {"budget_lost_is": round(blis, 3), "cost_per_lead": round(cpl, 2), "target_cpl": round(tcpl, 2) if tcpl else None},
            f"budget:{c.id}:{clock.today().isocalendar()[1]}")
    rlim = float(settings_service.get_value("monitoring.rank_limited_is", 0.3, db))
    if rlis and rlis > rlim + 0.1:
        add("RANK", f"{c.name} loses {rlis:.0%} impression share to ad rank — improve ad relevance / landing page",
            {"kind": "ADVICE"}, {"rank_lost_is": round(rlis, 3)}, f"rank:{c.id}:{clock.today().isocalendar()[1]}", needs_conv=False)
    # promote converting search terms not yet keywords
    kws = {normalize_term(k.text) for g in db.scalars(select(AdGroup).where(AdGroup.campaign_id == c.id))
           for k in db.scalars(select(Keyword).where(Keyword.ad_group_id == g.id))}
    for t in db.scalars(select(SearchTermRow).where(SearchTermRow.google_ads_account_id == account.id, SearchTermRow.window_days == 30,
                                                    SearchTermRow.campaign_external_id == c.external_id, SearchTermRow.conversions >= 2)):
        if normalize_term(t.term) not in kws:
            add("PROMOTE_TERM", f"Add converting search term '{t.term}' as an exact keyword in {c.name}",
                {"kind": "ADD_KEYWORDS", "ad_group_external_id": t.ad_group_external_id, "text": t.term, "match_type": "EXACT"},
                {"conversions_30d": t.conversions, "clicks": t.clicks}, f"promote:{c.id}:{normalize_term(t.term)}")
    # pause zero-conversion keywords with material spend
    if tcpl:
        for g in db.scalars(select(AdGroup).where(AdGroup.campaign_id == c.id)):
            for k in db.scalars(select(Keyword).where(Keyword.ad_group_id == g.id, Keyword.status == "ENABLED")):
                kc, kconv = db.execute(select(func.sum(PerformanceSnapshot.cost_micros), func.sum(PerformanceSnapshot.conversions)).where(
                    PerformanceSnapshot.level == "KEYWORD", PerformanceSnapshot.entity_external_id == k.external_id,
                    PerformanceSnapshot.google_ads_account_id == account.id, PerformanceSnapshot.date >= end - timedelta(days=29))).one()
                kc = float(kc or 0) / 1e6
                if kc >= 2 * tcpl and not (kconv or 0):
                    add("PAUSE_KEYWORD", f"Pause keyword '{k.text}' ({k.match_type}) — ${kc:,.0f} spent, 0 conversions in 30 days",
                        {"kind": "PAUSE_KEYWORD", "keyword_id": k.id}, {"cost_30d": round(kc, 2), "target_cpl": round(tcpl, 2)},
                        f"pausekw:{k.id}")
    # weak ads (CTR well below the ad group average)
    ratio = float(settings_service.get_value("monitoring.weak_ad_ctr_ratio", 0.6, db))
    from app.models import Ad
    for g in db.scalars(select(AdGroup).where(AdGroup.campaign_id == c.id, AdGroup.status == "ENABLED")):
        stats = []
        for a in db.scalars(select(Ad).where(Ad.ad_group_id == g.id, Ad.status == "ENABLED")):
            imp, clk = db.execute(select(func.sum(PerformanceSnapshot.impressions), func.sum(PerformanceSnapshot.clicks)).where(
                PerformanceSnapshot.level == "AD", PerformanceSnapshot.entity_external_id == a.external_id,
                PerformanceSnapshot.google_ads_account_id == account.id, PerformanceSnapshot.date >= end - timedelta(days=29))).one()
            if imp:
                stats.append((a, float(clk or 0) / float(imp)))
        if len(stats) >= 2:
            avg = sum(s for _, s in stats) / len(stats)
            for a, ctr in stats:
                if ctr < ratio * avg:
                    add("REPLACE_AD", f"Replace weak ad in {g.name} (CTR {ctr:.1%} vs ad group {avg:.1%})",
                        {"kind": "EDIT_AD", "ad_id": a.id}, {"ctr": round(ctr, 4), "ad_group_ctr": round(avg, 4)}, f"weakad:{a.id}",
                        needs_conv=False)
    return out


def create_approval_for(db: Session, actor: User, rec_id: str):
    """Turn a GO recommendation into an approval card (never executes directly)."""
    from app.services import planner
    r = db.get(Recommendation, rec_id)
    if not r:
        raise NotFound("Recommendation not found")
    if r.decision != "GO" and r.category != "NEGATIVE_KEYWORD":
        raise ValidationFailed(f"Recommendation is on HOLD: {r.hold_reason}")
    if r.approval_id:
        return approvals.get(db, r.approval_id)
    c = db.get(Campaign, r.campaign_id)
    account = db.get(GoogleAdsAccount, r.google_ads_account_id)
    b = db.get(Business, c.business_id) if c.business_id else None
    p = r.proposal
    if r.category == "NEGATIVE_KEYWORD":
        payload = {"kind": "ADD_NEGATIVES", "customer_id": account.customer_id, "operations": [p["operation"]],
                   "summary": {"negative": p["candidate"]["term"], "level": p["candidate"]["level"]}, "internal": {}}
        atype = "NEGATIVES"
    elif r.category == "BUDGET":
        from app.adapters import registry
        camp = registry.get("google_ads").get_resource(account.customer_id, c.external_resource_name)
        payload = {"kind": "CHANGE_BUDGET", "customer_id": account.customer_id,
                   "operations": [{"resource": "campaign_budget", "op": "update", "resource_name": camp["campaign_budget"],
                                   "fields": {"amount_micros": int(round(p["new_monthly"] / 30.4, 2) * 1e6)}}],
                   "summary": {"campaign": c.name, "new_monthly": p["new_monthly"]}, "internal": {"monthly_budget": p["new_monthly"]},
                   "target": {"campaign_id": c.id}}
        atype = "BUDGET"
    elif r.category == "PROMOTE_TERM":
        g = db.scalar(select(AdGroup).where(AdGroup.campaign_id == c.id, AdGroup.external_id == p["ad_group_external_id"]))
        payload = {"kind": "ADD_KEYWORDS", "customer_id": account.customer_id,
                   "operations": planner._kw_ops(g.external_resource_name, [{"text": p["text"], "match_type": "EXACT"}], []),
                   "summary": {"ad_group": g.name, "keyword": p["text"], "status_on_create": "PAUSED"}, "internal": {}}
        atype = "CREATE"
    elif r.category == "PAUSE_KEYWORD":
        k = db.get(Keyword, p["keyword_id"])
        g = db.get(AdGroup, k.ad_group_id)
        payload = {"kind": "PAUSE_KEYWORD", "customer_id": account.customer_id,
                   "operations": [{"resource": "ad_group_criterion", "op": "update",
                                   "resource_name": k.external_resource_name or _criterion_rn(account.customer_id, k.external_id),
                                   "fields": {"status": "PAUSED"}}],
                   "summary": {"keyword": k.text, "match_type": k.match_type}, "internal": {}}
        atype = "PAUSE"
    else:
        raise ValidationFailed("This recommendation is advice only or must be handled through a task (e.g. Edit an ad)")
    v = validation.validate(db, payload, b, approval_id=None, check_landing_pages=False) if b else None
    a = approvals.create(db, client_id=r.client_id, approval_type=atype, title=r.title, payload=payload, requested_by=actor,
                         business_id=b.id if b else None, account_id=account.id, current_state={}, proposed_state=payload["summary"],
                         reason="Optimization recommendation", expected_effect="See evidence", risks=[], validation=v,
                         context={"recommendation_id": r.id, "evidence": r.evidence, "enough_data": r.enough_data,
                                  "measurement": measurement.serialize_for(db, b) if b else None})
    r.approval_id, r.status = a.id, "PROPOSED"
    return a


def _criterion_rn(customer_id: str, external_id: str) -> str:
    return f"customers/{customer_id}/adGroupCriteria/{external_id}"


def serialize(r: Recommendation) -> dict:
    return {"id": r.id, "client_id": r.client_id, "campaign_id": r.campaign_id, "category": r.category, "size": r.size, "source": r.source,
            "title": r.title, "proposal": r.proposal, "evidence": r.evidence, "enough_data": r.enough_data, "decision": r.decision,
            "hold_reason": r.hold_reason, "status": r.status, "approval_id": r.approval_id, "operation_id": r.operation_id,
            "source_type": r.source_type, "created_at": r.created_at.isoformat(), "updated_at": r.updated_at.isoformat()}
