"""Task form + workflow orchestration. Runs every applicable step automatically up to the approval stage.
Nothing is changed in Google Ads by a task run."""
from __future__ import annotations

import traceback

from sqlalchemy import select
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from app.adapters import registry
from app.adapters.base import AdapterError
from app.core import clock
from app.core.errors import NotFound, ValidationFailed
from app.db.session import SessionLocal
from app.models import Ad, AdGroup, Business, Campaign, CampaignPlan, Client, GoogleAdsAccount, Keyword, Task, User
from app.services import (accounts_service, activity, ai_service, approvals, audits, auth_service, clients_service, feasibility,
                          intake, planner, tracking_gate, validation)

TASK_TYPES = {
    "BUILD_CAMPAIGN": {"needs": "business", "fields": ["services", "monthly_budget", "weekly_target", "radius_miles", "zip_codes"]},
    "ADD_AD_GROUP": {"needs": "campaign", "fields": ["service", "landing_page"]},
    "ADD_AD": {"needs": "ad_group", "fields": ["landing_page"]},
    "ADD_KEYWORDS": {"needs": "ad_group", "fields": ["starter_words"]},
    "EDIT_AD": {"needs": "ad", "fields": ["change_request"]},
    "PAUSE_AD": {"needs": "ad", "fields": ["reason"]},
    "CHANGE_BUDGET": {"needs": "campaign", "fields": ["monthly_budget"]},
    "CHANGE_TARGET": {"needs": "campaign", "fields": ["weekly_target"]},
    "CHANGE_LOCATIONS": {"needs": "campaign", "fields": ["radius_miles", "zip_codes"]},
    "AUDIT_ONLY": {"needs": "business", "fields": []},
}
GATED = {"BUILD_CAMPAIGN", "ADD_AD_GROUP", "ADD_KEYWORDS"}


def create(db: Session, actor: User, data: dict) -> Task:
    ttype = data.get("task_type")
    if ttype not in TASK_TYPES:
        raise ValidationFailed(f"Unknown task type {ttype}")
    auth_service.require(actor, "audits.run" if ttype == "AUDIT_ONLY" else "tasks.run")
    client = db.get(Client, data.get("client_id") or "")
    if not client:
        raise ValidationFailed("Client is required")
    auth_service.require_client_access(db, actor, client.id)
    if client.automation_frozen and ttype != "AUDIT_ONLY":
        pass  # frozen automation does not block human-driven tasks; it is shown as a warning
    fields = data.get("fields") or {}
    needs = TASK_TYPES[ttype]["needs"]
    business_id = data.get("business_id")
    campaign_id, ad_group_id, ad_id = data.get("campaign_id"), data.get("ad_group_id"), data.get("ad_id")
    if needs in ("campaign", "ad_group", "ad"):
        if ad_id:
            ad = db.get(Ad, ad_id)
            ad_group_id = ad_group_id or (ad.ad_group_id if ad else None)
        if ad_group_id:
            g = db.get(AdGroup, ad_group_id)
            campaign_id = campaign_id or (g.campaign_id if g else None)
        camp = db.get(Campaign, campaign_id or "")
        if not camp or camp.client_id != client.id:
            raise ValidationFailed("Select a campaign that belongs to this client")
        if camp.is_read_only:
            raise ValidationFailed(f"{camp.campaign_type} campaigns are read-only in V1")
        business_id = business_id or camp.business_id
        if needs in ("ad_group", "ad") and not ad_group_id:
            raise ValidationFailed("Select an ad group")
        if needs == "ad" and not ad_id:
            raise ValidationFailed("Select an ad")
    if not business_id:
        businesses = client.businesses
        if len(businesses) == 1:
            business_id = businesses[0].id
        else:
            raise ValidationFailed("Select a business")
    b = db.get(Business, business_id)
    if not b or b.client_id != client.id:
        raise ValidationFailed("Business does not belong to this client")
    if not b.website_url:
        raise ValidationFailed("The business profile needs a website before tasks can run")
    if ttype == "BUILD_CAMPAIGN":
        if float(fields.get("monthly_budget") or 0) <= 0:
            raise ValidationFailed("Monthly budget is required")
        if float(fields.get("weekly_target") or 0) <= 0:
            raise ValidationFailed("Weekly conversion target is required")
        fields.setdefault("services", [s.name for s in clients_service.active_services(b)])
        if not fields["services"]:
            raise ValidationFailed("Add at least one service to the business profile")
        fields.setdefault("radius_miles", b.default_radius_miles)
    if ttype == "CHANGE_BUDGET" and float(fields.get("monthly_budget") or 0) <= 0:
        raise ValidationFailed("New monthly budget is required")
    if ttype == "CHANGE_TARGET" and float(fields.get("weekly_target") or 0) <= 0:
        raise ValidationFailed("New weekly target is required")
    if ttype == "ADD_AD_GROUP" and not fields.get("service"):
        raise ValidationFailed("Service is required")
    report = intake.validate(db, b, client, ttype, fields)
    if report["status"] != "OK":
        first = next(i for i in report["issues"] if i["severity"] in ("BLOCKER", "QUESTION"))
        raise ValidationFailed(("Intake check: " + first["message"]) if report["status"] == "BLOCKED" else
                               ("Clarification needed: " + first["message"] + " " + first["question"]),
                               code="intake_blocked" if report["status"] == "BLOCKED" else "clarification_required",
                               details={"intake": report})
    t = Task(client_id=client.id, business_id=b.id, task_type=ttype, intake=report, campaign_id=campaign_id, ad_group_id=ad_group_id,
             ad_id=ad_id, fields=fields, human_plan=data.get("human_plan") or "", note=data.get("note") or "",
             created_by_id=actor.id, status="QUEUED", source_type=registry.get("google_ads").source)
    db.add(t)
    db.flush()
    activity.log(db, "task.created", actor=actor, entity="task", entity_id=t.id, client_id=client.id,
                 details={"type": ttype, "fields": fields})
    return t


class Stop(Exception):
    def __init__(self, status: str, message: str):
        super().__init__(message)
        self.status, self.message = status, message


class Runner:
    def __init__(self, db: Session, task: Task):
        self.db, self.task = db, task
        self.business = db.get(Business, task.business_id)
        self.client = db.get(Client, task.client_id)
        self.actor = db.get(User, task.created_by_id) if task.created_by_id else None
        self.account: GoogleAdsAccount | None = accounts_service.account_for_business(db, self.business)
        self.campaign = db.get(Campaign, task.campaign_id) if task.campaign_id else None
        self.ad_group = db.get(AdGroup, task.ad_group_id) if task.ad_group_id else None
        self.ad = db.get(Ad, task.ad_id) if task.ad_id else None
        self.ctx: dict = {}

    def step(self, name: str, fn):
        entry = {"name": name, "status": "RUNNING", "started_at": clock.now().isoformat()}
        self.task.steps = [*self.task.steps, dict(entry)]  # store a copy: in-place mutation would hide the change
        self.task.current_step = name
        self.db.commit()
        try:
            summary = fn() or ""
            entry.update(status="DONE", summary=summary if isinstance(summary, str) else str(summary))
        except Stop:
            entry.update(status="STOPPED")
            raise
        except Exception as exc:
            entry.update(status="FAILED", summary=f"{type(exc).__name__}: {getattr(exc, 'message', str(exc))[:400]}")
            raise
        finally:
            entry["finished_at"] = clock.now().isoformat()
            self.task.steps = [*self.task.steps[:-1], dict(entry)]
            flag_modified(self.task, "steps")
            self.db.commit()

    # ---------------------------------------------------------------- steps
    def s_load(self):
        if not self.account:
            raise Stop("FAILED", "No Google Ads account is linked to this client")
        counts = accounts_service.sync_account(self.db, self.account)
        return f"Account {self.account.customer_id} read ({registry.get('google_ads').source}): {counts['campaigns']} campaigns"

    def s_tracking(self):
        r = tracking_gate.evaluate(self.db, self.business, self.actor)
        self.ctx["tracking"] = r
        msg = f"Tracking {r.state}" + (" (override active → treated as AMBER)" if r.override_active else "")
        if r.effective_state == "RED" and self.task.task_type in GATED:
            raise Stop("BLOCKED_TRACKING", msg + ". Fix list: " + "; ".join(f["fix"] for f in r.fix_list[:4]))
        return msg

    def s_audits(self):
        focus = (self.task.fields or {}).get("service") or (self.ad_group.service if self.ad_group else None)
        res = audits.run_parallel(self.db, self.business, task_id=self.task.id, actor_id=self.task.created_by_id, focus_service=focus)
        self.ctx["audits"] = res
        return (f"Website: {res['website'].get('error') or 'ok'}; "
                f"GBP: {'ok' if not res['gbp'].get('error') else res['gbp']['error']}; "
                f"Competitors: {'skipped' if res['competitor'].get('skipped') else 'ok'}")

    def _services_for_task(self) -> list[dict]:
        f = self.task.fields or {}
        web = (self.ctx.get("audits") or {}).get("website") or {}
        lp = web.get("landing_page_map", {})
        all_svcs = {s.name: s for s in clients_service.active_services(self.business)}
        if self.task.task_type == "BUILD_CAMPAIGN":
            names = f.get("services") or list(all_svcs)
        elif self.task.task_type == "ADD_AD_GROUP":
            names = [f["service"]]
        else:
            names = [self.ad_group.service or self.ad_group.name] if self.ad_group else []
        out = []
        for n in names:
            s = all_svcs.get(n)
            page = (f.get("landing_page") if self.task.task_type in ("ADD_AD_GROUP", "ADD_AD") else None) or \
                   (s.landing_page_url if s and s.landing_page_url else None) or lp.get(n) or self.business.website_url
            out.append({"name": n, "landing_page": page, "landing_page_url": page})
        return out

    def _existing_keywords(self) -> dict[str, list[str]]:
        if not self.campaign:
            return {}
        out = {}
        for g in self.db.scalars(select(AdGroup).where(AdGroup.campaign_id == self.campaign.id)):
            out[g.name] = [k.text for k in self.db.scalars(select(Keyword).where(Keyword.ad_group_id == g.id))]
        return out

    def s_keywords(self):
        a = self.ctx.get("audits") or {}
        services = self._services_for_task()
        if self.task.task_type == "ADD_KEYWORDS":
            starters = (self.task.fields or {}).get("starter_words") or ""
            if starters:
                services = [{**services[0], "name": services[0]["name"]}] + [{"name": w.strip(), "landing_page_url": services[0]["landing_page"]}
                                                                             for w in starters.split(",") if w.strip()]
        monthly = float((self.task.fields or {}).get("monthly_budget") or (self.campaign.monthly_budget_micros / 1e6 if self.campaign else 0))
        ctx = audits.build_context(self.db, self.business)
        res = audits.keyword_research(self.db, ctx, a.get("website") or {}, a.get("competitor") or {}, self.task.id,
                                      services=services, existing_keywords=self._existing_keywords(),
                                      daily_budget=monthly / 30.4 if monthly else None)
        self.ctx["keywords"] = res
        row = audits.save_keyword_audit(self.db, self.business, self.task.id, self.task.created_by_id, res)
        return row.summary

    def s_overlap(self):
        ov = self.ctx["keywords"]["overlaps"]
        return f"{len(ov)} overlap(s) found" + (": " + ", ".join(o["keyword"] for o in ov[:5]) if ov else "")

    def _apply_budget_decision(self):
        """A team decision on the budget recommendation is applied to the task fields once (idempotent)."""
        f = dict(self.task.fields or {})
        d = f.get("budget_decision")
        if not d or d.get("applied"):
            return
        if d["decision"] == "ACCEPT_RECOMMENDED" and d.get("monthly_budget"):
            f["monthly_budget"] = float(d["monthly_budget"])
        elif d["decision"] == "ADJUST":
            if d.get("monthly_budget"):
                f["monthly_budget"] = float(d["monthly_budget"])
            if d.get("weekly_target"):
                f["weekly_target"] = float(d["weekly_target"])
        f["budget_decision"] = {**d, "applied": True}
        self.task.fields = f
        flag_modified(self.task, "fields")

    def s_feasibility(self):
        if self.task.task_type == "BUILD_CAMPAIGN":
            self._apply_budget_decision()
        f = self.task.fields or {}
        if self.task.task_type == "BUILD_CAMPAIGN":
            monthly, target, camp_ids = float(f["monthly_budget"]), float(f["weekly_target"]), None
        else:
            monthly = self.campaign.monthly_budget_micros / 1e6 or self.campaign.daily_budget_micros * 30.4 / 1e6
            target = self.campaign.weekly_conversion_target or 0
            camp_ids = {self.campaign.external_id}
        history = None
        try:
            history = feasibility.account_history(registry.get("google_ads"), self.account.customer_id, camp_ids)
        except AdapterError:
            history = None
        if self.task.task_type == "BUILD_CAMPAIGN" and history and history.get("clicks", 0) < 200:
            history = None
        a = self.ctx.get("audits") or {}
        r = feasibility.run(self.db, self.business, monthly_budget=monthly, weekly_target=target,
                            keyword_rows=self.ctx.get("keywords", {}).get("keywords", []),
                            audits={"gbp": a.get("gbp"), "website": a.get("website")}, account_history=history,
                            task_id=self.task.id, campaign_id=self.campaign.id if self.campaign else None,
                            source_type=registry.get("google_ads").source)
        dec = (self.task.fields or {}).get("budget_decision")
        if dec:
            from datetime import datetime, timezone
            r.decision, r.decision_note = dec["decision"], dec.get("note", "")
            r.decided_by_id, r.decided_at = dec.get("by"), datetime.now(timezone.utc)
            r.accepted_monthly_budget, r.accepted_weekly_target = monthly, target
        self.ctx["feasibility"] = feasibility.serialize(r)
        s = r.summary
        thin = ""
        if self.task.task_type == "ADD_AD_GROUP" and self.campaign:
            groups = len(list(self.db.scalars(select(AdGroup).where(AdGroup.campaign_id == self.campaign.id)))) + 1
            per = monthly / groups
            if per < 300:
                thin = f" Warning: ${per:,.0f}/month per ad group after adding — the campaign budget may be too thin."
            self.ctx["feasibility"]["budget_thin_warning"] = thin.strip()
        msg = (f"{r.status}: expected {s['expected_weekly_conversions']} leads/week (range {s['realistic_weekly_range']}) "
               f"vs target {target}; allowed CPL ${s['allowed_cost_per_lead']}; recommended budget "
               f"${s.get('recommended_monthly_budget') or monthly:,.0f}/month. ESTIMATE." + thin)
        if self.task.task_type == "BUILD_CAMPAIGN" and r.status == "UNREALISTIC" and not dec:
            self.db.commit()
            raise Stop("AWAITING_BUDGET_DECISION", "The target is unrealistic for this budget (estimate). A team member must choose: "
                       "accept the recommended budget, adjust budget/target, or keep the current numbers. " + msg)
        return msg

    def _tracking_state(self) -> tuple[str, bool]:
        r = self.ctx.get("tracking") or tracking_gate.latest(self.db, self.business.id)
        return (r.effective_state if r else "AMBER"), bool(r and r.override_active)

    def s_draft(self):
        state, override = self._tracking_state()
        tt = self.task.task_type
        kw = self.ctx.get("keywords", {}).get("keywords", [])
        if tt == "BUILD_CAMPAIGN":
            plan = planner.draft(self.db, self.task, self.business, scope="CAMPAIGN", services=self._services_for_task(),
                                 keyword_table=kw, feasibility=self.ctx.get("feasibility"), tracking_state=state, override=override)
        elif tt == "ADD_AD_GROUP":
            plan = planner.draft(self.db, self.task, self.business, scope="AD_GROUP", services=self._services_for_task(),
                                 keyword_table=kw, feasibility=self.ctx.get("feasibility"), tracking_state=state, override=override,
                                 extra={"existing_ad_groups": self._existing_keywords()})
        else:  # ADD_AD / EDIT_AD
            current = {"headlines": self.ad.headlines, "descriptions": self.ad.descriptions} if (self.ad and tt == "EDIT_AD") else None
            plan = planner.draft(self.db, self.task, self.business, scope="AD", services=self._services_for_task(), keyword_table=kw,
                                 feasibility=None, tracking_state=state, override=override,
                                 extra={"current_ad": current, "change_request": (self.task.fields or {}).get("change_request", "")})
        self.task.plan_id = plan.id
        self.ctx["plan"] = plan
        return f"Draft created by {registry.get('ai').name} (prompt v{self._prompt_version(plan)})"

    def _prompt_version(self, plan):
        from app.models import AIRun
        run = self.db.get(AIRun, plan.draft_ai_run_id)
        return f"{run.prompt_template_id}@{run.prompt_version}, model {run.model}" if run else "?"

    def s_challenge(self):
        state, _ = self._tracking_state()
        ch = planner.challenge(self.db, self.ctx["plan"], self.business, self.ctx.get("feasibility"), state)
        return f"Challenger verdict {ch['verdict']} with {len(ch['objections'])} objection(s)"

    def s_human(self):
        if not self.task.human_plan.strip():
            return "No human plan provided"
        res = planner.compare_human_plan(self.db, self.ctx["plan"], self.task.human_plan,
                                         float((self.task.fields or {}).get("monthly_budget") or 0) or None)
        if res.get("human_budget") and self.task.task_type == "BUILD_CAMPAIGN":
            self.task.fields = {**self.task.fields, "monthly_budget": res["human_budget"]}
        return (f"{len(res['agreed'])} agreed, {len(res['ai_only'])} AI-only, {len(res['human_only'])} human-only, "
                f"{len(res['disagreements'])} disagreement(s); human decisions applied by default")

    def build_payload(self) -> tuple[dict, str, str, dict, dict]:
        """Returns (payload, approval_type, title, current_state, proposed_state)."""
        tt, f = self.task.task_type, self.task.fields or {}
        plan: CampaignPlan | None = self.ctx.get("plan")
        if tt == "BUILD_CAMPAIGN":
            p = planner.build_campaign_payload(self.db, self.task, self.business, self.account, plan.draft)
            return p, "CREATE", f"Build campaign: {p['summary']['campaign_name']}", {"campaign": "does not exist"}, p["summary"]
        if tt == "ADD_AD_GROUP":
            p = planner.build_ad_group_payload(self.account, self.campaign, plan.draft)
            return p, "CREATE", f"Add ad group '{plan.draft['name']}' to {self.campaign.name}", {"ad_groups": list(self._existing_keywords())}, p["summary"]
        if tt == "ADD_AD":
            lp = f.get("landing_page") or self.ad_group.landing_page_url or self.business.website_url
            p = planner.build_ad_payload(self.account, self.ad_group, plan.draft, lp)
            return p, "CREATE", f"Add ad to {self.ad_group.name}", {}, p["summary"]
        if tt == "EDIT_AD":
            p = planner.build_ad_payload(self.account, self.ad_group, plan.draft, self.ad.final_url or self.business.website_url,
                                         kind="EDIT_AD", replaces=self.ad)
            return (p, "UPDATE", f"Edit ad in {self.ad_group.name} (new ad created paused, old ad paused on enable)",
                    {"headlines": self.ad.headlines, "descriptions": self.ad.descriptions},
                    {"headlines": plan.draft["headlines"], "descriptions": plan.draft["descriptions"]})
        if tt == "ADD_KEYWORDS":
            rows = [r for r in self.ctx["keywords"]["keywords"] if r["selected"] and r["label"] == "SERVICE"][:10]
            existing = {k for v in self._existing_keywords().values() for k in v}
            kws = [{"text": r["keyword"], "match_type": mt} for r in rows if r["keyword"] not in existing for mt in ("EXACT", "PHRASE")]
            p = {"kind": "ADD_KEYWORDS", "customer_id": self.account.customer_id,
                 "operations": planner._kw_ops(self.ad_group.external_resource_name, kws, []),
                 "summary": {"ad_group": self.ad_group.name, "keywords": len(kws), "status_on_create": "PAUSED"}, "internal": {}}
            return p, "CREATE", f"Add {len(kws)} keywords to {self.ad_group.name}", {"keywords": len(existing)}, p["summary"]
        if tt == "PAUSE_AD":
            p = {"kind": "PAUSE_AD", "customer_id": self.account.customer_id,
                 "operations": [{"resource": "ad_group_ad", "op": "update", "resource_name": self.ad.external_resource_name,
                                 "fields": {"status": "PAUSED"}}],
                 "summary": {"ad": self.ad.external_resource_name, "reason": f.get("reason", "")}, "internal": {}}
            return p, "PAUSE", f"Pause ad in {self.ad_group.name}", {"status": self.ad.status}, {"status": "PAUSED"}
        if tt == "CHANGE_BUDGET":
            new_m = float(f["monthly_budget"])
            camp = registry.get("google_ads").get_resource(self.account.customer_id, self.campaign.external_resource_name)
            p = {"kind": "CHANGE_BUDGET", "customer_id": self.account.customer_id,
                 "operations": [{"resource": "campaign_budget", "op": "update", "resource_name": camp["campaign_budget"],
                                 "fields": {"amount_micros": int(round(new_m / 30.4, 2) * 1e6)}}],
                 "summary": {"campaign": self.campaign.name, "new_monthly": new_m, "new_daily": round(new_m / 30.4, 2)},
                 "internal": {"monthly_budget": new_m}, "target": {"campaign_id": self.campaign.id}}
            cur = self.campaign.monthly_budget_micros / 1e6
            return (p, "BUDGET", f"Change budget of {self.campaign.name}: ${cur:,.0f} → ${new_m:,.0f}/month",
                    {"monthly_budget": cur, "daily_budget": self.campaign.daily_budget_micros / 1e6},
                    {"monthly_budget": new_m, "daily_budget": round(new_m / 30.4, 2)})
        if tt == "CHANGE_TARGET":
            p = {"kind": "CHANGE_TARGET", "customer_id": self.account.customer_id, "operations": [],
                 "summary": {"campaign": self.campaign.name, "new_weekly_target": float(f["weekly_target"]),
                             "note": "Internal setting — no Google Ads change"},
                 "internal": {"weekly_conversion_target": float(f["weekly_target"])}, "target": {"campaign_id": self.campaign.id}}
            return (p, "INTERNAL", f"Change weekly target of {self.campaign.name}", {"weekly_target": self.campaign.weekly_conversion_target},
                    {"weekly_target": float(f["weekly_target"])})
        if tt == "CHANGE_LOCATIONS":
            ops = planner.location_ops(self.campaign.external_resource_name, self.business, f)
            p = {"kind": "CHANGE_LOCATIONS", "customer_id": self.account.customer_id, "operations": ops,
                 "summary": {"campaign": self.campaign.name, "add": f.get("zip_codes") or f"{f.get('radius_miles')} mile radius",
                             "existing_targets_to_remove_manually": self.campaign.locations},
                 "internal": {}}
            return (p, "UPDATE", f"Change locations of {self.campaign.name}", {"locations": self.campaign.locations},
                    {"add": p["summary"]["add"]})
        raise ValidationFailed(f"No payload builder for {tt}")

    def s_validate(self):
        attempts = 0
        budget_ctx = None
        if self.task.task_type == "CHANGE_BUDGET":
            budget_ctx = {"current_monthly": self.campaign.monthly_budget_micros / 1e6,
                          "new_monthly": float(self.task.fields["monthly_budget"])}
        while True:
            payload, atype, title, cur, prop = self.build_payload()
            v = validation.validate(self.db, payload, self.business, existing_keywords=self._existing_keywords(),
                                    task_id=self.task.id, budget_context=budget_ctx)
            v.ai_fix_attempts = attempts
            fixable = [e for e in v.errors if e["fixable"]]
            plan = self.ctx.get("plan")
            if v.passed or attempts >= 2 or not plan or len(fixable) != len(v.errors):
                break
            attempts += 1
            self._ai_fix(plan, fixable)
        self.ctx.update(payload=payload, approval_type=atype, title=title, current=cur, proposed=prop, validation=v)
        return ("Passed" if v.passed else f"Failed with {len(v.errors)} error(s)") + \
               (f" after {attempts} AI fix attempt(s)" if attempts else "") + f"; {len(v.warnings)} warning(s)"

    def _ai_fix(self, plan: CampaignPlan, errors: list[dict]):
        d = plan.draft
        if plan.scope == "CAMPAIGN":
            ads = [a for g in d["ad_groups"] for a in g.get("ads", [])]
        elif plan.scope == "AD_GROUP":
            ads = d.get("ads", [])
        else:
            ads = [d]
        parsed, _ = ai_service.generate(self.db, "FIX_ADS", {"ads": ads, "errors": errors,
                                                              "verified_claims": self.business.verified_claims}, task_id=self.task.id)
        fixed = [a.model_dump() for a in parsed.ads]
        it = iter(fixed)
        if plan.scope == "CAMPAIGN":
            groups = []
            for g in d["ad_groups"]:
                groups.append({**g, "ads": [next(it) for _ in g.get("ads", [])]})
            plan.draft = {**d, "ad_groups": groups}
        elif plan.scope == "AD_GROUP":
            plan.draft = {**d, "ads": fixed}
        else:
            plan.draft = {**d, **fixed[0]}
        plan.version += 1
        self.db.flush()

    def s_approval(self):
        v = self.ctx["validation"]
        plan = self.ctx.get("plan")
        f = self.ctx.get("feasibility") or {}
        tr = tracking_gate.latest(self.db, self.business.id)
        from app.models import FixTrackerItem
        open_fixes = [x.issue for x in self.db.scalars(select(FixTrackerItem).where(
            FixTrackerItem.business_id == self.business.id, FixTrackerItem.status != "RESOLVED"))]
        requires_super = any(w["code"] == "BUDGET_THRESHOLD" for w in v.warnings)
        risks = [w["message"] for w in v.warnings]
        if plan and plan.challenger:
            risks += [o["message"] for o in plan.challenger.get("objections", []) if o["severity"] == "HIGH"]
        expected = ""
        if f.get("summary"):
            s = f["summary"]
            expected = (f"ESTIMATE: {s.get('expected_weekly_conversions')} leads/week (range {s.get('realistic_weekly_range')}) "
                        f"at ~${s.get('daily_budget')}/day. Not a guarantee.")
        explanation = ""
        try:
            ex, _ = ai_service.generate(self.db, "EXPLAIN", {"headline": self.ctx["title"],
                                                             "facts": {**self.ctx["payload"].get("summary", {}),
                                                                       "tracking": tr.effective_state if tr else "unknown",
                                                                       "feasibility": f.get("status")}}, task_id=self.task.id)
            explanation = ex.summary + (" — " + " | ".join(ex.highlights) if ex.highlights else "")
        except ai_service.AIOutputInvalid:
            explanation = "(AI explanation unavailable)"
        a = approvals.create(
            self.db, client_id=self.client.id, approval_type=self.ctx["approval_type"], title=self.ctx["title"],
            payload=self.ctx["payload"], requested_by=self.actor, business_id=self.business.id,
            account_id=self.account.id if self.account else None, task_id=self.task.id, current_state=self.ctx["current"],
            proposed_state=self.ctx["proposed"], reason=self.task.note or f"Requested via task {self.task.task_type}",
            expected_effect=expected, risks=risks, validation=v, ai_explanation=explanation,
            context={"tracking": tracking_gate.serialize(tr), "feasibility": f, "open_client_fixes": open_fixes[:20],
                     "research_summary": {k: (self.ctx.get("audits") or {}).get(k, {}).get("source") for k in ("website", "gbp", "competitor")},
                     "audit_ids": (self.ctx.get("audits") or {}).get("audit_ids"),
                     "keyword_summary": {"clusters": {k: len(v_) for k, v_ in (self.ctx.get("keywords") or {}).get("clusters", {}).items()},
                                         "overlaps": (self.ctx.get("keywords") or {}).get("overlaps", [])[:10]},
                     "draft": plan.draft if plan else None, "challenger": plan.challenger if plan else None,
                     "human_comparison": plan.human_comparison if plan else None, "plan_id": plan.id if plan else None,
                     "requires_super_admin": requires_super,
                     "data_source": registry.get("google_ads").source})
        self.task.approval_id = a.id
        if plan:
            plan.status = "SUBMITTED"
        if a.status == "VALIDATION_FAILED":
            raise Stop("VALIDATION_FAILED", f"Validation failed — approval {a.id} cannot be approved until fixed")
        return f"Approval {a.id} created (hash {a.payload_hash[:12]}…)"

    def s_account_audit(self):
        row = audits.account_audit(self.db, self.business, self.task.id, self.task.created_by_id)
        return row.summary

    # ---------------------------------------------------------------- flows
    def run(self):
        tt = self.task.task_type
        self.task.status = "RUNNING"
        self.task.steps = []
        self.db.commit()
        flows = {
            "BUILD_CAMPAIGN": ["load", "tracking", "audits", "keywords", "overlap", "feasibility", "draft", "challenge", "human", "validate", "approval"],
            "ADD_AD_GROUP": ["load", "tracking", "audits", "keywords", "overlap", "feasibility", "draft", "challenge", "human", "validate", "approval"],
            "ADD_AD": ["load", "audits", "draft", "challenge", "validate", "approval"],
            "ADD_KEYWORDS": ["load", "tracking", "keywords", "overlap", "validate", "approval"],
            "EDIT_AD": ["load", "draft", "challenge", "validate", "approval"],
            "PAUSE_AD": ["load", "validate", "approval"],
            "CHANGE_BUDGET": ["load", "validate", "approval"],
            "CHANGE_TARGET": ["load", "feasibility", "validate", "approval"],
            "CHANGE_LOCATIONS": ["load", "validate", "approval"],
            "AUDIT_ONLY": ["load", "tracking", "account_audit"],
        }
        names = {"load": "Load account", "tracking": "Conversion tracking gate", "audits": "Audits (website, GBP, competitors)",
                 "keywords": "Keyword research", "overlap": "Overlap check", "feasibility": "Feasibility",
                 "draft": "AI draft", "challenge": "AI challenger", "human": "Human plan comparison",
                 "validate": "Validation", "approval": "Approval request", "account_audit": "Read-only account audit (90 days)"}
        try:
            for key in flows[tt]:
                self.step(names[key], getattr(self, f"s_{key}"))
            self.task.status = "COMPLETED" if tt == "AUDIT_ONLY" else "AWAITING_APPROVAL"
        except Stop as stop:
            self.task.status, self.task.error = stop.status, stop.message
        except Exception as exc:  # noqa: BLE001
            self.db.rollback()
            self.task = self.db.get(Task, self.task.id)
            self.task.status = "FAILED"
            self.task.error = f"{type(exc).__name__}: {getattr(exc, 'message', str(exc))[:500]}"
            self.task.steps = [*self.task.steps, {"name": "error", "status": "FAILED", "summary": traceback.format_exc()[-800:]}]
        self.task.current_step = ""
        activity.log(self.db, "task.finished", actor=self.actor, entity="task", entity_id=self.task.id, client_id=self.task.client_id,
                     details={"status": self.task.status, "error": self.task.error})
        self.db.commit()


def decide_budget(db: Session, actor: User, task_id: str, body: dict) -> Task:
    """Team confirmation of the budget recommendation. Resumes the task from the start with the decision applied."""
    auth_service.require(actor, "tasks.run")
    t = db.get(Task, task_id)
    if not t:
        raise NotFound("Task not found")
    auth_service.require_client_access(db, actor, t.client_id)
    if t.status != "AWAITING_BUDGET_DECISION":
        raise ValidationFailed("This task is not waiting for a budget decision")
    decision = body.get("decision")
    if decision not in ("ACCEPT_RECOMMENDED", "KEEP_CURRENT", "ADJUST"):
        raise ValidationFailed("decision must be ACCEPT_RECOMMENDED, KEEP_CURRENT or ADJUST")
    from app.models import FeasibilityResult
    row = db.scalar(select(FeasibilityResult).where(FeasibilityResult.task_id == t.id).order_by(FeasibilityResult.created_at.desc()))
    d: dict = {"decision": decision, "note": (body.get("note") or "")[:1000], "by": actor.id}
    if decision == "ACCEPT_RECOMMENDED":
        rec = (row.summary or {}).get("recommended_monthly_budget") if row else None
        if not rec:
            raise ValidationFailed("There is no recommended budget to accept")
        d["monthly_budget"] = rec
    elif decision == "ADJUST":
        for k in ("monthly_budget", "weekly_target"):
            if body.get(k) not in (None, ""):
                v = float(body[k])
                if v <= 0:
                    raise ValidationFailed(f"{k} must be positive")
                d[k] = v
        if "monthly_budget" not in d and "weekly_target" not in d:
            raise ValidationFailed("ADJUST needs a new monthly_budget and/or weekly_target")
    elif len((d["note"]).strip()) < 10:
        raise ValidationFailed("Keeping unrealistic numbers requires a written reason (10+ characters)")
    t.fields = {**(t.fields or {}), "budget_decision": d}
    t.status, t.error = "QUEUED", ""
    activity.log(db, "task.budget_decision", actor=actor, entity="task", entity_id=t.id, client_id=t.client_id, details=d)
    return t


def run_task(task_id: str) -> str:
    db = SessionLocal()
    try:
        task = db.get(Task, task_id)
        if not task:
            raise NotFound("Task not found")
        Runner(db, task).run()
        return task.status
    finally:
        db.close()


def on_approval_executed(db: Session, approval_id: str, op_state: str) -> None:
    from app.models import Approval
    a = db.get(Approval, approval_id)
    if not a or not a.task_id:
        return
    t = db.get(Task, a.task_id)
    if not t:
        return
    if a.approval_type == "ENABLE":
        t.status = "ENABLED" if op_state == "SUCCEEDED" else "ENABLE_FAILED"
    else:
        t.status = "EXECUTED_PAUSED" if (op_state == "SUCCEEDED" and a.payload.get("kind") in
                                         ("BUILD_CAMPAIGN", "ADD_AD_GROUP", "ADD_AD", "EDIT_AD", "ADD_KEYWORDS")) else \
            ("EXECUTED" if op_state == "SUCCEEDED" else "EXECUTION_FAILED")


def serialize(t: Task) -> dict:
    return {"id": t.id, "client_id": t.client_id, "business_id": t.business_id, "task_type": t.task_type,
            "campaign_id": t.campaign_id, "ad_group_id": t.ad_group_id, "ad_id": t.ad_id, "fields": t.fields,
            "human_plan": t.human_plan, "note": t.note, "status": t.status, "current_step": t.current_step, "steps": t.steps,
            "error": t.error, "intake": t.intake, "approval_id": t.approval_id, "plan_id": t.plan_id, "source_type": t.source_type,
            "created_at": t.created_at.isoformat(), "updated_at": t.updated_at.isoformat()}
