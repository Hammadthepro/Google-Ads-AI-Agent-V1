"""#26 emergency brake, #27 negative keyword safety, #28 enough-data hold, #29 weekly reporting, #30 experiments,
#31 learning loop, monitoring 30-day refresh, manual-change detection."""
from __future__ import annotations

from sqlalchemy import select

from app.models import Campaign, Client, Operation, Recommendation, SearchTermRow
from app.services import optimization
from tests.conftest import ADMIN, MANAGER, SUPER, Api


def _client(api, name):
    return next(c for c in api.get("/clients").json() if c["name"] == name)


def test_emergency_brake_rules_and_simulated_actions(db):
    events = Api(ADMIN).get("/emergency/events").json()
    rules = {e["rule"]: e for e in events}
    assert rules["spend_spike"]["action_mode"] == "SIMULATED" and rules["spend_spike"]["action"] == "PAUSE_CAMPAIGN"
    assert rules["landing_page_down"]["observed"]["status"] == 503
    summit = db.scalar(select(Client).where(Client.name == "Summit Legal Partners"))
    assert summit.automation_frozen is True  # freeze is internal and real
    # no Google Ads write was made by the brake
    assert not db.scalars(select(Operation).where(Operation.policy == "EMERGENCY")).all()
    # alerts recorded
    assert any("Emergency brake" in a["subject"] for a in Api(ADMIN).get("/alerts").json())


def test_global_kill_switch_halts_brake_automation():
    sup = Api(SUPER)
    sup.post("/kill-switches", {"scope": "GLOBAL", "is_on": True, "reason": "test"})
    try:
        out = sup.post("/emergency/run").json()
        assert out and "skipped" in out[0]
    finally:
        sup.post("/kill-switches", {"scope": "GLOBAL", "is_on": False})


def test_monitor_data_is_labelled_and_wasted_spend_computed():
    admin = Api(ADMIN)
    bs = _client(admin, "Brightsmile Dental Group")
    m = admin.get(f"/monitor/{bs['id']}").json()
    assert m["data_source"] == "MOCK" and all(c["source_type"] == "MOCK" for c in m["campaigns"])
    assert m["wasted_spend"]["total"] > 0
    assert any("jobs" in t["term"] or "diy" in t["term"] or "free" in t["term"] for t in m["wasted_spend"]["terms"])
    north = _client(admin, "Northside Home Services")
    changes = admin.get(f"/monitor/{north['id']}").json()["change_events"]
    assert any(c["made_outside_app"] and c["user"] == "owner@northside-home.example" for c in changes)


def test_negative_keyword_safety_checks(db):
    acct_c = db.scalar(select(Campaign).where(Campaign.name == "Dental | Teeth Whitening | Search"))
    from app.models import GoogleAdsAccount
    acct = db.get(GoogleAdsAccount, acct_c.google_ads_account_id)
    # make one irrelevant term collide with an active keyword and one with a converting term
    row = db.scalar(select(SearchTermRow).where(SearchTermRow.campaign_external_id == acct_c.external_id,
                                                SearchTermRow.window_days == 30, SearchTermRow.term == "teeth whitening"))
    cands = optimization.negative_candidates(db, acct, acct_c)
    terms = {c["term"]: c for c in cands}
    assert all(c["clicks"] >= 5 for c in cands)
    assert not any(c["term"] == "teeth whitening" for c in cands if c["safe"])  # active keyword never blocked
    for c in cands:
        if c["match_type"] == "PHRASE":
            assert c["level"] == "SHARED_LIST" and "safety" in c
    junk = [c for c in cands if c["irrelevant_pattern"] and c["match_type"] == "EXACT"]
    assert junk and all(c["safe"] for c in junk)
    _ = (row, terms)


def test_shadow_mode_proposes_only_and_active_mode_needs_green(db, monkeypatch):
    north = db.scalar(select(Client).where(Client.name == "Northside Home Services"))
    before = db.scalar(select(__import__("sqlalchemy").func.count(Operation.id)).where(Operation.policy == "AUTO_NEGATIVE"))
    s = optimization.run_for_client(db, north)
    db.commit()
    assert s["mode"] == "shadow" and s["negatives_applied"] == 0
    assert db.scalars(select(Recommendation).where(Recommendation.status == "SHADOW")).all()
    # switch to ACTIVE (env + setting); only GREEN-tracked, enough-data campaigns may apply negatives
    monkeypatch.setenv("AUTO_NEGATIVES_MODE", "active")
    from app.core.config import reset_settings_cache
    reset_settings_cache()
    from app.services import settings_service
    settings_service.set_value(db, "negatives.mode", "active", None)
    db.commit()
    try:
        s2 = optimization.run_for_client(db, north)
        db.commit()
        ops = db.scalars(select(Operation).where(Operation.policy == "AUTO_NEGATIVE")).all()
        if s2["negatives_applied"]:
            assert len(ops) > before and all(o.integration_mode == "MOCK" and o.state == "SUCCEEDED" for o in ops)
        assert s2["negatives_applied"] <= 25
        dental = db.scalar(select(Client).where(Client.name == "Brightsmile Dental Group"))
        s3 = optimization.run_for_client(db, dental)  # conversion data unreliable -> nothing automatic
        db.commit()
        assert s3["negatives_applied"] == 0
    finally:
        settings_service.set_value(db, "negatives.mode", "shadow", None)
        db.commit()
        monkeypatch.setenv("AUTO_NEGATIVES_MODE", "shadow")
        reset_settings_cache()


def test_enough_data_hold_for_new_campaign(db):
    hvac = db.scalar(select(Campaign).where(Campaign.name == "HVAC | AC & Furnace Repair | Search"))
    gate = optimization.enough_data(db, hvac)
    assert gate["decision"] == "HOLD"
    failed = {c["key"] for c in gate["checks"] if not c["passed"]}
    assert {"volume", "learning", "conversion_data"} <= failed
    held = db.scalars(select(Recommendation).where(Recommendation.campaign_id == hvac.id)).all()
    assert all(r.status == "HELD" and r.hold_reason for r in held)


def test_weekly_report_numbers_and_diagnosis():
    admin = Api(ADMIN)
    north = _client(admin, "Northside Home Services")
    rep = admin.post(f"/reports/generate/{north['id']}").json()
    totals = rep["metrics"]["totals"]
    camps = rep["metrics"]["campaigns"]
    assert totals["conversions"] == round(sum(c["actual"] for c in camps), 2)
    assert totals["gap"] == round(totals["conversions"] - totals["target"], 1)
    for c in camps:
        assert len(c["trend"]) == 4 and len(c["diagnosis"]) == 8
        assert [d["question"] for d in c["diagnosis"]][0] == "Are conversions being measured correctly?"
        for d in c["diagnosis"]:
            assert d["recommended"] == (d["rules"] and d["ai"])  # only causes named by BOTH become actions
    assert rep["source_type"] == "MOCK" and rep["ai_summary"]
    assert "Numbers are calculated" in rep["note"]
    assert "open_fixes" in rep["sections"] and "wasted_spend" in rep["sections"]


def test_experiment_workflow_human_picks_winner(db, sim_clock):
    admin, mgr, sup = Api(ADMIN), Api(MANAGER), Api(SUPER)
    camp = db.scalar(select(Campaign).where(Campaign.name == "Plumbing | Drain Cleaning & Water Heaters | Search"))
    r = mgr.post("/experiments", {"campaign_id": camp.id, "variable": "AD_ANGLE", "hypothesis": "Speed angle beats price angle",
                                  "treatment": {"headline_angle": "Same-Day Service"}})
    assert r.status_code == 200, r.text
    exp = r.json()
    a = admin.get(f"/approvals/{exp['start_approval_id']}").json()
    assert a["approval_type"] == "EXPERIMENT_START"
    admin.post(f"/approvals/{a['id']}/approve", {"payload_hash": a["payload_hash"]})
    assert admin.post(f"/approvals/{a['id']}/execute").json()["state"] == "SUCCEEDED"
    res = admin.get(f"/experiments/{exp['id']}/results").json()
    assert res["experiment"]["status"] == "RUNNING"
    assert admin.post(f"/experiments/{exp['id']}/select-winner", {"arm": "treatment"}).status_code == 422  # not enough data
    sup.patch("/settings", {"key": "experiments.min_clicks_per_arm", "value": 5})
    sup.patch("/settings", {"key": "experiments.min_conversions_per_arm", "value": 0})
    try:
        sim_clock.advance_days(3)
        res = admin.get(f"/experiments/{exp['id']}/results").json()
        assert set(res["results"]["arms"]) == {"control", "treatment"} and res["results"]["enough_data"] is True
        assert res["experiment"]["status"] == "AWAITING_WINNER"  # never auto-completed
        assert Api("analyst@napollo.example").post(f"/experiments/{exp['id']}/select-winner", {"arm": "treatment"}).status_code == 403
        w = admin.post(f"/experiments/{exp['id']}/select-winner", {"arm": "treatment", "reason": "Lower cost per lead"}).json()
        wa = sup.get(f"/approvals/{w['approval_id']}").json()
        assert wa["approval_type"] == "EXPERIMENT_WINNER"
        sup.post(f"/approvals/{wa['id']}/approve", {"payload_hash": wa["payload_hash"]})
        assert admin.post(f"/approvals/{wa['id']}/execute").json()["state"] == "SUCCEEDED"
        done = [e for e in admin.get("/experiments").json() if e["id"] == exp["id"]][0]
        assert done["status"] == "COMPLETED" and done["winner_arm"] == "treatment"
    finally:
        sup.patch("/settings", {"key": "experiments.min_clicks_per_arm", "value": 100})
        sup.patch("/settings", {"key": "experiments.min_conversions_per_arm", "value": 10})


def test_learning_loop_playbook_feedback_and_regression():
    admin = Api(ADMIN)
    r = admin.post("/playbook", {"text": "Always add 'apprenticeship' as a negative for trade businesses.", "note": "From repeated edits"})
    assert r.status_code == 200
    assert Api(MANAGER).post("/playbook", {"text": "Managers cannot write rules here"}).status_code == 403
    rules = admin.get("/playbook").json()
    assert any("apprenticeship" in x["text"] for x in rules)
    fb = admin.get("/learning/feedback").json()
    assert any(f["type"] == "REJECTED" for f in fb)
    res = admin.post("/learning/regression-cases/run").json()
    assert len(res) >= 2 and all(r["passed"] for r in res), res
    assert all("prompt_version" in r for r in res)
    plans = admin.get("/plans").json()
    assert plans and plans[0]["draft_ai"]["prompt"].startswith("draft_")
    assert plans[0]["playbook_rule_ids"]


def test_changes_made_outside_the_app_are_never_pushed_out_by_the_apps_own_writes():
    from datetime import timedelta

    from app.core import clock
    from app.db.session import session_scope
    from app.models import ChangeEvent, Client, GoogleAdsAccount
    from app.services import monitoring
    with session_scope() as db:
        client = db.scalar(select(Client).where(Client.name == "Northside Home Services"))
        acct = db.scalar(select(GoogleAdsAccount).where(GoogleAdsAccount.client_id == client.id))
        ids = []
        for i in range(120):  # many newer changes made by the app itself
            e = ChangeEvent(google_ads_account_id=acct.id, external_id=f"regression-outside-{i}", resource_name=f"customers/x/campaigns/{i}", change_type="UPDATE", user_email="api@app",
                            client_type="GOOGLE_ADS_API", changed_at=clock.now() + timedelta(minutes=i + 1), made_outside_app=False, source_type="MOCK")
            db.add(e)
            ids.append(e)
        db.flush()
        try:
            out = monitoring.change_events(db, client.id)
            assert any(c["made_outside_app"] for c in out)
        finally:
            for e in ids:
                db.delete(e)
