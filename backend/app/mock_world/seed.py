"""Deterministic reset + reseed of the simulated world and demo data.

    python -m app.cli reset-mock        # wipe everything except users, then seed
    python -m app.cli seed              # seed only if empty

All clients, businesses, people and data are FICTIONAL and labelled SIMULATED.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select, text

from app.adapters import registry
from app.core import clock
from app.db.session import session_scope
from app.models import (Base, Business, BusinessService, Client, Competitor, GoogleAdsAccount, MockChangeEvent, MockCustomer, MockResource,
                        PlaybookRule, RegressionCase, Role, User, UserClientAssignment)

MCC = "1000000000"
PLUMB, HVAC, DENT, LAW = ("www.northsideplumbing.example", "www.northsidehvac.example", "www.brightsmiledental.example",
                          "www.summitinjurylaw.example")
NETWORK = {"target_google_search": True, "target_search_network": False, "target_content_network": False,
           "target_partner_search_network": False}

USERS = [("superadmin@napollo.example", "Sam Super (demo)", Role.SUPER_ADMIN), ("admin@napollo.example", "Alex Admin (demo)", Role.ADMIN),
         ("backup.admin@napollo.example", "Blair Backup (demo)", Role.ADMIN),
         ("manager@napollo.example", "Morgan Manager (demo)", Role.CAMPAIGN_MANAGER), ("analyst@napollo.example", "Avery Analyst (demo)", Role.ANALYST)]


def _heads(service, city, weak=False):
    if weak:
        return [f"{service}", "Call Us", "Contact Our Office", "Learn More Today"]
    return [f"{service} in {city}", f"Local {service} Team", "Request a Quote Today", f"Book {service} Today", "Speak With Our Team"]


ACCOUNTS = [
    {"cid": "1110000001", "name": "Northside Home Services (SIMULATED)", "city": "Columbus",
     "conversions": [("Plumbing - Quote form", "WEBPAGE", "SUBMIT_LEAD_FORM", "ONE_PER_CLICK", None, PLUMB, True),
                     ("Plumbing - Calls from ads", "AD_CALL", "PHONE_CALL_LEAD", "ONE_PER_CLICK", 60, PLUMB, True),
                     ("HVAC - Contact form", "WEBPAGE", "SUBMIT_LEAD_FORM", "ONE_PER_CLICK", None, HVAC, True),
                     ("HVAC - Calls from ads", "AD_CALL", "PHONE_CALL_LEAD", "ONE_PER_CLICK", None, HVAC, True)],
     "campaigns": [
         {"name": "Plumbing | Drain Cleaning & Water Heaters | Search", "scenario": "healthy", "daily": 55, "days_ago": 120, "host": PLUMB, "target": 5,
          "groups": [("Drain Cleaning", "/drain-cleaning", ["drain cleaning", "drain cleaning service", "clogged drain repair", "drain cleaning near me"], True),
                     ("Water Heater Repair", "/water-heater-repair", ["water heater repair", "water heater repair near me", "hot water heater repair"], False)]},
         {"name": "Plumbing | Emergency | Search", "scenario": "budget_limited", "daily": 30, "days_ago": 90, "host": PLUMB, "target": 4, "manual": True,
          "groups": [("Emergency Plumbing", "/emergency-plumbing", ["emergency plumber", "emergency plumbing", "24 hour plumber", "burst pipe repair"], False)]},
         {"name": "HVAC | AC & Furnace Repair | Search", "scenario": "insufficient_data", "daily": 40, "days_ago": 4, "host": HVAC, "target": 4,
          "groups": [("AC Repair", "/ac-repair", ["ac repair", "air conditioner repair", "ac repair near me"], False),
                     ("Furnace Repair", "/furnace-repair", ["furnace repair", "furnace repair near me", "heating repair"], False)]},
         {"name": "Northside | Brand | Performance Max", "type": "PERFORMANCE_MAX", "scenario": "healthy", "daily": 20, "days_ago": 200, "host": PLUMB, "groups": []},
     ]},
    {"cid": "1110000002", "name": "Brightsmile Dental Group (SIMULATED)", "city": "Phoenix",
     "conversions": [("Website - Appointment request", "WEBPAGE", "SUBMIT_LEAD_FORM", "MANY_PER_CLICK", None, DENT, True),
                     ("GA4 - generate_lead (imported)", "GOOGLE_ANALYTICS_4_CUSTOM", "SUBMIT_LEAD_FORM", "ONE_PER_CLICK", None, DENT, True)],
     "campaigns": [
         {"name": "Dental | Implants | Search", "scenario": "broken_tracking", "daily": 70, "days_ago": 150, "host": DENT, "target": 3,
          "groups": [("Dental Implants", "/dental-implants", ["dental implants", "dental implants cost", "tooth implant", "implant dentist near me"], False)]},
         {"name": "Dental | Teeth Whitening | Search", "scenario": "irrelevant_terms", "daily": 35, "days_ago": 90, "host": DENT, "target": 3,
          "groups": [("Teeth Whitening", "/teeth-whitening", ["teeth whitening", "professional teeth whitening", "teeth whitening near me", "laser teeth whitening"], True)]},
     ]},
    {"cid": "1110000003", "name": "Summit Legal Partners (SIMULATED)", "city": "Denver",
     "conversions": [("Case review form", "WEBPAGE", "SUBMIT_LEAD_FORM", "ONE_PER_CLICK", None, LAW, True),
                     ("Calls from ads (90s+)", "AD_CALL", "PHONE_CALL_LEAD", "ONE_PER_CLICK", 90, LAW, True)],
     "campaigns": [
         {"name": "Injury | Car Accidents | Search", "scenario": "overspend", "daily": 150, "days_ago": 200, "host": LAW, "target": 4,
          "groups": [("Car Accident Lawyer", "/car-accident-lawyer", ["car accident lawyer", "car accident attorney", "auto accident lawyer near me"], False)]},
         {"name": "Injury | Personal Injury | Search", "scenario": "landing_outage", "daily": 120, "days_ago": 180, "host": LAW, "target": 3,
          "groups": [("Personal Injury Lawyer", "/personal-injury", ["personal injury lawyer", "personal injury attorney", "injury lawyer near me"], False)]},
         {"name": "Summit | Brand | Performance Max", "type": "PERFORMANCE_MAX", "scenario": "healthy", "daily": 25, "days_ago": 300, "host": LAW, "groups": []},
     ]},
]

CLIENTS = [
    {"name": "Northside Home Services", "cid": "1110000001", "tag": "Jordan Lee (web agency) — jordan@northside-web.example",
     "businesses": [
         {"name": "Northside Plumbing", "type": "home_services", "host": PLUMB, "phone": "(555) 201-4400", "address": "1200 Example Ave, Columbus, OH",
          "services": [("Drain Cleaning", "/drain-cleaning"), ("Water Heater Repair", "/water-heater-repair"), ("Emergency Plumbing", "/emergency-plumbing"),
                       ("Leak Detection", "")], "excluded": ["Septic Tank Pumping"],
          "claims": ["Licensed & Insured", "Same-Day Service", "Family owned since 2004"],
          "rules": ["Avoid: cheapest, guaranteed lowest price", "Always mention licensed and insured when space allows"],
          "competitors": [("Rapid Rooter Columbus", "www.rapidrooter.example"), ("BlueLine Plumbing Co", "www.bluelineplumbing.example"),
                          ("Buckeye Drain Pros", "www.buckeyedrain.example")],
          "ads_text": "Rapid Rooter: $49 Drain Special | 24/7 Emergency Plumbers | No Overtime Charges\nBlueLine: Upfront Pricing — 1-Year Labor Warranty"},
         {"name": "Northside Heating & Air", "type": "home_services", "host": HVAC, "phone": "(555) 201-4410", "address": "1200 Example Ave, Columbus, OH",
          "services": [("AC Repair", "/ac-repair"), ("Furnace Repair", "/furnace-repair"), ("HVAC Maintenance", "/hvac-maintenance")], "excluded": [],
          "claims": ["Licensed & Insured"], "rules": [],
          "competitors": [("CoolAir Columbus", "www.coolaircolumbus.example"), ("Comfort First Heating", "www.comfortfirst.example"),
                          ("ArcticPro HVAC", "www.arcticprohvac.example")], "ads_text": ""}]},
    {"name": "Brightsmile Dental Group", "cid": "1110000002", "tag": "Dr. Riley Chen (practice owner) — office@brightsmile.example",
     "businesses": [
         {"name": "Brightsmile Dental", "type": "dental", "host": DENT, "phone": "(555) 340-7720", "address": "88 Sample Rd, Phoenix, AZ",
          "services": [("Dental Implants", "/dental-implants"), ("Teeth Whitening", "/teeth-whitening"), ("Emergency Dentist", "/emergency-dentist"),
                       ("Invisalign", "/invisalign")], "excluded": ["Pediatric Dentistry"],
          "claims": ["Board-certified dentists", "Most insurance accepted"], "rules": ["Avoid: painless, best dentist"],
          "competitors": [("Desert Smile Dental", "www.desertsmile.example"), ("Camelback Family Dentistry", "www.camelbackfamily.example"),
                          ("Sonoran Dental Studio", "www.sonorandental.example")],
          "ads_text": "Desert Smile: Free Implant Consultation | Same-Day Crowns | Financing Available"}]},
    {"name": "Summit Legal Partners", "cid": "1110000003", "tag": "Casey Morgan (marketing) — marketing@summitlaw.example",
     "businesses": [
         {"name": "Summit Injury Law", "type": "legal", "host": LAW, "phone": "(555) 610-3300", "address": "500 Fictional St, Denver, CO",
          "services": [("Car Accident Lawyer", "/car-accident-lawyer"), ("Personal Injury Lawyer", "/personal-injury"),
                       ("Truck Accident Lawyer", "/truck-accident-lawyer"), ("Wrongful Death Lawyer", "/wrongful-death-lawyer")],
          "excluded": ["Criminal Defense"], "claims": ["No fee unless we win", "Free consultation"],
          "rules": ["Avoid: guaranteed settlement, best lawyer"],
          "competitors": [("Front Range Injury Attorneys", "www.frontrangeinjury.example"), ("Mile High Accident Law", "www.milehighaccident.example"),
                          ("Peak Legal Group", "www.peaklegalgroup.example")], "ads_text": ""}]},
]

HOURS = {"mon-fri": "07:00-19:00", "sat": "08:00-16:00", "sun": "closed"}


def reset(include_users: bool = False) -> None:
    keep = set() if include_users else {"users", "sessions"}
    with session_scope() as db:
        names = [t.name for t in reversed(Base.metadata.sorted_tables) if t.name not in keep]
        db.execute(text("TRUNCATE " + ", ".join(f'"{n}"' for n in names) + " RESTART IDENTITY CASCADE"))
    registry.reset()


def _mock_world(db) -> None:
    today = clock.today()
    db.add(MockCustomer(customer_id=MCC, descriptive_name="Napollo Agency MCC (SIMULATED)", is_manager=True))
    rid = 10000
    for acc in ACCOUNTS:
        cid = acc["cid"]
        db.add(MockCustomer(customer_id=cid, descriptive_name=acc["name"], manager_customer_id=MCC, auto_tagging_enabled=True))
        for name, typ, cat, counting, secs, host, primary in acc["conversions"]:
            rid += 1
            db.add(MockResource(customer_id=cid, resource_type="conversion_action", resource_id=rid,
                                resource_name=f"customers/{cid}/conversionActions/{rid}", status="ENABLED",
                                data={"name": name, "type": typ, "category": cat, "counting_type": counting, "primary_for_goal": primary,
                                      "phone_call_duration_seconds": secs, "business_host": host,
                                      "origin": "GOOGLE_ANALYTICS" if "GA4" in name else ("CALL_FROM_ADS" if typ == "AD_CALL" else "WEBSITE")}))
        for camp in acc["campaigns"]:
            rid += 1
            b_rn = f"customers/{cid}/campaignBudgets/{rid}"
            db.add(MockResource(customer_id=cid, resource_type="campaign_budget", resource_id=rid, resource_name=b_rn, status="ENABLED",
                                data={"name": f"{camp['name']} budget", "amount_micros": camp["daily"] * 1_000_000, "delivery_method": "STANDARD"}))
            rid += 1
            c_rn = f"customers/{cid}/campaigns/{rid}"
            start = today - timedelta(days=camp["days_ago"])
            db.add(MockResource(customer_id=cid, resource_type="campaign", resource_id=rid, resource_name=c_rn, status="ENABLED",
                                data={"name": camp["name"], "advertising_channel_type": camp.get("type", "SEARCH"), "campaign_budget": b_rn,
                                      "maximize_conversions": {}, "network_settings": NETWORK, "scenario": camp["scenario"],
                                      "start_date": start.isoformat(), "weekly_conversion_target": camp.get("target", 0),
                                      "bidding_strategy_system_status": "LEARNING_NEW" if camp["days_ago"] < 10 else "ENABLED",
                                      "geo_target_type_setting": {"positive_geo_target_type": "PRESENCE"},
                                      "status_history": [{"date": (start - timedelta(days=1)).isoformat(), "status": "ENABLED"}]}))
            rid += 1
            db.add(MockResource(customer_id=cid, resource_type="campaign_criterion", resource_id=rid,
                                resource_name=f"customers/{cid}/campaignCriteria/{rid}", parent_resource_name=c_rn, status="ENABLED",
                                data={"campaign": c_rn, "type": "PROXIMITY", "proximity": {"radius": 20, "radius_units": "MILES",
                                                                                           "address": {"city_name": acc["city"]}}}))
            for gname, path, kws, weak in camp["groups"]:
                rid += 1
                g_rn = f"customers/{cid}/adGroups/{rid}"
                db.add(MockResource(customer_id=cid, resource_type="ad_group", resource_id=rid, resource_name=g_rn, parent_resource_name=c_rn,
                                    status="ENABLED", data={"name": gname, "campaign": c_rn, "service": gname, "type": "SEARCH_STANDARD"}))
                for kw in kws:
                    for mt in ("EXACT", "PHRASE"):
                        rid += 1
                        db.add(MockResource(customer_id=cid, resource_type="ad_group_criterion", resource_id=rid,
                                            resource_name=f"customers/{cid}/adGroupCriteria/{rid}", parent_resource_name=g_rn, status="ENABLED",
                                            data={"ad_group": g_rn, "type": "KEYWORD", "keyword": {"text": kw, "match_type": mt}}))
                for i, is_weak in enumerate([False] + ([True] if weak else [])):
                    rid += 1
                    db.add(MockResource(customer_id=cid, resource_type="ad_group_ad", resource_id=rid,
                                        resource_name=f"customers/{cid}/adGroupAds/{rid}", parent_resource_name=g_rn, status="ENABLED",
                                        data={"ad_group": g_rn, "weak": is_weak,
                                              "ad": {"final_urls": [f"https://{camp['host']}{path}"],
                                                     "responsive_search_ad": {"headlines": _heads(gname, acc["city"], is_weak),
                                                                              "descriptions": [f"Professional {gname.lower()} in {acc['city']}. Request a quote online today.",
                                                                                               "Talk to our team today. Clear options and honest advice."],
                                                                              "path1": gname.lower().split()[0][:15], "path2": ""}}}))
            if camp.get("manual"):
                for d, what in ((3, "Budget raised in the Google Ads UI"), (6, "Keyword paused manually")):
                    db.add(MockChangeEvent(customer_id=cid, resource_name=c_rn if d == 6 else b_rn, change_type="UPDATE",
                                           user_email="owner@northside-home.example", client_type="GOOGLE_ADS_WEB_CLIENT",
                                           changed_at=clock.now() - timedelta(days=d), details={"note": f"SIMULATED: {what}"}))


def seed(run_workflows: bool = True) -> dict:
    from app.services import (accounts_service, emergency, monitoring, optimization, reporting, tasks_service, tracking_gate)
    with session_scope() as db:
        if db.scalar(select(MockCustomer.id)):
            return {"seeded": False, "reason": "already seeded"}
        users = {}
        for email, name, role in USERS:
            u = db.scalar(select(User).where(User.email == email))
            if not u:
                u = User(email=email, name=name, role=role)
                db.add(u)
            users[email] = u
        db.flush()
        _mock_world(db)
        db.flush()
    with session_scope() as db:
        su = db.scalar(select(User).where(User.email == "superadmin@napollo.example"))
        admin = db.scalar(select(User).where(User.email == "admin@napollo.example"))
        backup = db.scalar(select(User).where(User.email == "backup.admin@napollo.example"))
        accounts_service.discover(db, su)
        db.flush()
        for spec in CLIENTS:
            c = Client(name=spec["name"], owner_user_id=admin.id, backup_owner_user_id=backup.id, owner_contact=admin.email,
                       backup_contact=backup.email, tag_access_contact=spec["tag"], created_by_id=su.id)
            db.add(c)
            db.flush()
            acct = db.scalar(select(GoogleAdsAccount).where(GoogleAdsAccount.customer_id == spec["cid"]))
            acct.client_id = c.id
            for bs in spec["businesses"]:
                base = f"https://{bs['host']}"
                b = Business(client_id=c.id, name=bs["name"], business_type=bs["type"], website_url=base + "/",
                             gbp_url=f"https://maps.google.com/?cid=sim-{bs['host'].split('.')[1]}", phone=bs["phone"], address=bs["address"],
                             lead_form_url=base + "/contact", thank_you_url=base + "/thank-you", default_radius_miles=20,
                             business_hours=HOURS, verified_claims=bs["claims"], brand_rules=bs["rules"],
                             competitor_ads_text=bs["ads_text"], google_ads_account_id=acct.id)
                db.add(b)
                db.flush()
                for name, path in bs["services"]:
                    db.add(BusinessService(business_id=b.id, name=name, landing_page_url=(base + path) if path else ""))
                for name in bs["excluded"]:
                    db.add(BusinessService(business_id=b.id, name=name, is_excluded=True))
                for name, host in bs["competitors"]:
                    db.add(Competitor(business_id=b.id, name=name, website_url=f"https://{host}/"))
            db.flush()
        mgr = db.scalar(select(User).where(User.email == "manager@napollo.example"))
        ana = db.scalar(select(User).where(User.email == "analyst@napollo.example"))
        for c in db.scalars(select(Client)):
            if c.name != "Summit Legal Partners":
                db.add(UserClientAssignment(user_id=mgr.id, client_id=c.id))
            db.add(UserClientAssignment(user_id=ana.id, client_id=c.id))
        for t in ("Never use 'cheap' or 'cheapest' in ad copy for any client.",
                  "For home-services clients, always include an 'Emergency' callout only if the business offers emergency service.",
                  "Legal clients: mention 'Free consultation' only when it is a verified claim."):
            db.add(PlaybookRule(text=t, scope="GLOBAL", author_id=admin.id, source_note="Seeded from repeated approval edits"))
        db.add(RegressionCase(name="Plumbing build — V1 policy", created_by_id=admin.id,
                              inputs={"business_name": "Northside Plumbing", "website": f"https://{PLUMB}/", "city": "Columbus", "phone": "(555) 201-4400",
                                      "verified_claims": ["Licensed & Insured", "Same-Day Service"], "excluded_services": ["Septic Tank Pumping"],
                                      "services": [{"name": "Drain Cleaning", "landing_page": f"https://{PLUMB}/drain-cleaning"},
                                                   {"name": "Water Heater Repair", "landing_page": f"https://{PLUMB}/water-heater-repair"}],
                                      "keyword_table": [], "tracking_state": "GREEN", "monthly_budget": 1500},
                              expectations={"no_broad_match": True, "max_headline_length": 30, "min_ad_groups": 2,
                                            "must_not_contain": ["septic", "cheapest"], "must_include_negatives": ["jobs", "diy"],
                                            "only_verified_claims": True, "verified_claims": ["Licensed & Insured", "Same-Day Service"]}))
        db.add(RegressionCase(name="Dental build — excluded services respected", created_by_id=admin.id,
                              inputs={"business_name": "Brightsmile Dental", "website": f"https://{DENT}/", "city": "Phoenix",
                                      "verified_claims": ["Board-certified dentists"], "excluded_services": ["Pediatric Dentistry"],
                                      "services": [{"name": "Dental Implants", "landing_page": f"https://{DENT}/dental-implants"}],
                                      "keyword_table": [], "tracking_state": "AMBER", "monthly_budget": 2000},
                              expectations={"no_broad_match": True, "max_headline_length": 30, "must_not_contain": ["pediatric"],
                                            "must_include_negatives": ["jobs"]}))
        for acct in db.scalars(select(GoogleAdsAccount).where(GoogleAdsAccount.client_id.is_not(None))):
            accounts_service.sync_account(db, acct)
    if not run_workflows:
        return {"seeded": True}
    with session_scope() as db:
        for b in db.scalars(select(Business)):
            tracking_gate.evaluate(db, b)
    with session_scope() as db:
        monitoring.run_daily_all(db)
    with session_scope() as db:
        for b in db.scalars(select(Business)):
            tracking_gate.evaluate(db, b)  # second pass: GREEN now based on recorded conversions
        for c in db.scalars(select(Client)):
            optimization.run_for_client(db, c)
        emergency.hourly_check(db)
        for c in db.scalars(select(Client)):
            reporting.generate(db, c)
    # seeded approval records produced by real workflows
    with session_scope() as db:
        mgr = db.scalar(select(User).where(User.email == "manager@napollo.example"))
        from app.models import Campaign
        emerg = db.scalar(select(Campaign).where(Campaign.name == "Plumbing | Emergency | Search"))
        whiten = db.scalar(select(Campaign).where(Campaign.name == "Dental | Teeth Whitening | Search"))
        t1 = tasks_service.create(db, mgr, {"task_type": "CHANGE_BUDGET", "client_id": emerg.client_id, "campaign_id": emerg.id,
                                            "fields": {"monthly_budget": 1400}, "note": "Campaign is losing impression share to budget"})
        from app.models import AdGroup, Ad
        g = db.scalar(select(AdGroup).where(AdGroup.campaign_id == whiten.id))
        weak = db.scalar(select(Ad).where(Ad.ad_group_id == g.id).order_by(Ad.external_id.desc()))
        t2 = tasks_service.create(db, mgr, {"task_type": "PAUSE_AD", "client_id": whiten.client_id, "campaign_id": whiten.id,
                                            "ad_group_id": g.id, "ad_id": weak.id, "fields": {"reason": "Low CTR ad"}})
        ids = (t1.id, t2.id)
    for tid in ids:
        tasks_service.run_task(tid)
    with session_scope() as db:
        from app.models import Task
        from app.services import approvals
        admin = db.scalar(select(User).where(User.email == "admin@napollo.example"))
        t2 = db.get(Task, ids[1])
        if t2.approval_id:
            approvals.reject(db, admin, t2.approval_id, "Keep this ad running until the new angle test is ready (seeded example)")
    return {"seeded": True, "at": datetime.now(timezone.utc).isoformat()}
