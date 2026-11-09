"""TEST FIXTURE ONLY — never imported by the application. MockGoogleAdsAdapter: a persistent, DB-backed simulated Google Ads environment.

It is NOT Google Ads. Every response is labelled MOCK. State lives in mock_gads_* tables, so simulated
campaigns survive refreshes and restarts, and reset/reseed is explicit.
"""
from __future__ import annotations

import re
from datetime import date, timedelta

from sqlalchemy import func, select

from app.adapters.base import InvalidInput, NotFoundError, PermissionDenied
from app.adapters.google_ads.base import RESOURCE_COLLECTIONS, GoogleAdsAdapter
from app.core import clock
from app.db.session import session_scope
from app.services.ad_policy import ALLOWED_CAPS, DESCRIPTION_MAX, HEADLINE_MAX, PATH_MAX, policy_issues_for_text  # noqa: F401
from tests.fakes.mock_world import simulator
from tests.fakes.mock_world.failures import EmptyResult, PartialResult, check_failure
from app.models import MockCallRecord, MockChangeEvent, MockCustomer, MockMetricDaily, MockResource, MockSearchTerm

APP_API_USER = "api-user@napollo-agent.app"
PARENT_FIELD = {
    "ad_group": "campaign", "ad_group_criterion": "ad_group", "ad_group_ad": "ad_group",
    "campaign_criterion": "campaign", "campaign_asset": "campaign", "campaign_shared_set": "campaign",
    "shared_criterion": "shared_set", "experiment": "base_campaign",
}


def _rn(cid: str, rtype: str, rid: int) -> str:
    return f"customers/{cid}/{RESOURCE_COLLECTIONS[rtype]}/{rid}"


def _to_dict(r: MockResource) -> dict:
    return {**r.data, "resource_name": r.resource_name, "id": str(r.resource_id), "status": r.status,
            "parent": r.parent_resource_name, "labels": r.labels, "resource_type": r.resource_type}


class MockGoogleAdsAdapter(GoogleAdsAdapter):
    name = "MockGoogleAdsAdapter"
    source = "MOCK"
    is_real = False

    def _fail(self, method: str, entity: str = ""):
        check_failure("google_ads", method, entity)

    # ------------------------------------------------------------------ accounts
    def list_accessible_customers(self) -> list[dict]:
        try:
            self._fail("list_accessible_customers")
        except EmptyResult:
            return []
        with session_scope() as db:
            return [self._customer_dict(c) for c in db.scalars(select(MockCustomer).where(MockCustomer.accessible.is_(True)).order_by(MockCustomer.customer_id))]

    def _customer_dict(self, c: MockCustomer) -> dict:
        return {"customer_id": c.customer_id, "descriptive_name": c.descriptive_name, "is_manager": c.is_manager,
                "manager_customer_id": c.manager_customer_id, "currency_code": c.currency_code,
                "time_zone": c.time_zone, "auto_tagging_enabled": c.auto_tagging_enabled}

    def get_customer(self, customer_id: str) -> dict:
        self._fail("get_customer", customer_id)
        with session_scope() as db:
            c = db.scalar(select(MockCustomer).where(MockCustomer.customer_id == customer_id))
            if not c:
                raise NotFoundError(f"Customer {customer_id} not found", adapter=self.name)
            if not c.accessible:
                raise PermissionDenied(f"No access to customer {customer_id}", adapter=self.name)
            return self._customer_dict(c)

    def get_account_hierarchy(self, manager_customer_id: str) -> list[dict]:
        self._fail("get_account_hierarchy", manager_customer_id)
        with session_scope() as db:
            rows = db.scalars(select(MockCustomer).where(MockCustomer.manager_customer_id == manager_customer_id))
            return [self._customer_dict(c) for c in rows]

    # ------------------------------------------------------------------ structure
    def list_resources(self, customer_id: str, resource_type: str, parent: str | None = None,
                       label: str | None = None) -> list[dict]:
        try:
            self._fail("list_resources", f"{customer_id}:{resource_type}")
        except EmptyResult:
            return []
        with session_scope() as db:
            q = select(MockResource).where(MockResource.customer_id == customer_id,
                                           MockResource.resource_type == resource_type,
                                           MockResource.status != "REMOVED")
            if parent:
                q = q.where(MockResource.parent_resource_name == parent)
            rows = [_to_dict(r) for r in db.scalars(q.order_by(MockResource.resource_id))]
        if label:
            rows = [r for r in rows if label in (r.get("labels") or [])]
        return rows

    def get_resource(self, customer_id: str, resource_name: str) -> dict | None:
        self._fail("get_resource", resource_name)
        with session_scope() as db:
            r = db.scalar(select(MockResource).where(MockResource.resource_name == resource_name))
            return _to_dict(r) if r else None

    # ------------------------------------------------------------------ reporting
    def _ensure_generated(self, db, cid: str, end: date) -> None:
        """Lazily simulate days up to `end` (never beyond yesterday of the simulated clock)."""
        yesterday = clock.today() - timedelta(days=1)
        end = min(end, yesterday)
        last = db.scalar(select(func.max(MockMetricDaily.date)).where(MockMetricDaily.customer_id == cid))
        start = (last + timedelta(days=1)) if last else end - timedelta(days=89)
        # new campaigns may start before `last`; generate_day is idempotent, so re-scan the last 7 days
        start = min(start, end - timedelta(days=7))
        if start <= end:
            simulator.generate_range(db, cid, start, end)

    def get_metrics(self, customer_id: str, start: date, end: date, level: str = "CAMPAIGN") -> list[dict]:
        partial = False
        try:
            self._fail("get_metrics", customer_id)
        except EmptyResult:
            return []
        except PartialResult:
            partial = True
        today = clock.today()
        with session_scope() as db:
            self._ensure_generated(db, customer_id, end)
            rows = db.scalars(select(MockMetricDaily).where(
                MockMetricDaily.customer_id == customer_id, MockMetricDaily.level == level,
                MockMetricDaily.date >= start, MockMetricDaily.date <= end).order_by(MockMetricDaily.date))
            out = []
            for m in rows:
                conv = round(m.final_conversions * simulator.lag_factor((today - m.date).days), 2)
                out.append({
                    "date": m.date.isoformat(), "level": level, "entity_id": str(m.entity_id),
                    "campaign_id": str(m.campaign_id), "impressions": m.impressions, "clicks": m.clicks,
                    "cost_micros": m.cost_micros, "conversions": conv,
                    "form_conversions": round(conv * m.form_share, 2),
                    "call_conversions": round(conv * (1 - m.form_share), 2),
                    "search_impression_share": None if partial else round(m.search_impression_share, 3),
                    "search_budget_lost_impression_share": round(m.budget_lost_is, 3),
                    "search_rank_lost_impression_share": round(m.rank_lost_is, 3),
                })
        return out[: len(out) // 2] if partial else out

    def get_search_terms(self, customer_id: str, start: date, end: date) -> list[dict]:
        try:
            self._fail("get_search_terms", customer_id)
        except EmptyResult:
            return []
        with session_scope() as db:
            self._ensure_generated(db, customer_id, end)
            q = (select(MockSearchTerm.campaign_id, MockSearchTerm.ad_group_id, MockSearchTerm.term,
                        func.max(MockSearchTerm.matched_keyword), func.sum(MockSearchTerm.impressions),
                        func.sum(MockSearchTerm.clicks), func.sum(MockSearchTerm.cost_micros),
                        func.sum(MockSearchTerm.conversions), func.max(MockSearchTerm.date))
                 .where(MockSearchTerm.customer_id == customer_id, MockSearchTerm.date >= start,
                        MockSearchTerm.date <= end)
                 .group_by(MockSearchTerm.campaign_id, MockSearchTerm.ad_group_id, MockSearchTerm.term))
            last_conv = dict(((r[0], r[1]), r[2]) for r in db.execute(
                select(MockSearchTerm.ad_group_id, MockSearchTerm.term, func.max(MockSearchTerm.date))
                .where(MockSearchTerm.customer_id == customer_id, MockSearchTerm.conversions > 0)
                .group_by(MockSearchTerm.ad_group_id, MockSearchTerm.term)))
            return [{"campaign_id": str(r[0]), "ad_group_id": str(r[1]), "term": r[2], "matched_keyword": r[3],
                     "impressions": int(r[4] or 0), "clicks": int(r[5] or 0), "cost_micros": int(r[6] or 0),
                     "conversions": round(float(r[7] or 0), 2),
                     "last_conversion_date": (last_conv.get((r[1], r[2])) or None) and last_conv[(r[1], r[2])].isoformat()}
                    for r in db.execute(q)]

    def get_call_records(self, customer_id: str, start: date, end: date) -> list[dict]:
        self._fail("get_call_records", customer_id)
        with session_scope() as db:
            self._ensure_generated(db, customer_id, end)
            rows = db.scalars(select(MockCallRecord).where(MockCallRecord.customer_id == customer_id,
                                                           func.date(MockCallRecord.started_at) >= start,
                                                           func.date(MockCallRecord.started_at) <= end))
            return [{"id": r.id, "campaign_id": str(r.campaign_id), "start_call_date_time": r.started_at.isoformat(),
                     "call_duration_seconds": r.duration_seconds, "call_status": r.status} for r in rows]

    def get_change_events(self, customer_id: str, since_iso: str) -> list[dict]:
        self._fail("get_change_events", customer_id)
        from datetime import datetime
        since = datetime.fromisoformat(since_iso)
        with session_scope() as db:
            rows = db.scalars(select(MockChangeEvent).where(MockChangeEvent.customer_id == customer_id,
                                                            MockChangeEvent.changed_at >= since)
                              .order_by(MockChangeEvent.changed_at))
            return [{"id": r.id, "change_resource_name": r.resource_name, "resource_change_operation": r.change_type,
                     "user_email": r.user_email, "client_type": r.client_type,
                     "change_date_time": r.changed_at.isoformat(), "details": r.details} for r in rows]

    def get_recommendations(self, customer_id: str) -> list[dict]:
        self._fail("get_recommendations", customer_id)
        end = clock.today() - timedelta(days=1)
        rows = self.get_metrics(customer_id, end - timedelta(days=13), end)
        by_c: dict[str, list] = {}
        for r in rows:
            by_c.setdefault(r["campaign_id"], []).append(r)
        recs = []
        for cid_, rs in by_c.items():
            blis = sum(r["search_budget_lost_impression_share"] for r in rs) / len(rs)
            if blis > 0.2:
                recs.append({"type": "CAMPAIGN_BUDGET", "campaign_id": cid_,
                             "detail": f"Simulated recommendation: campaign lost {blis:.0%} impression share to budget"})
        return recs

    def get_today_spend(self, customer_id: str) -> list[dict]:
        self._fail("get_today_spend", customer_id)
        with session_scope() as db:
            return simulator.today_spend(db, customer_id, clock.now())

    def get_experiment_results(self, customer_id: str, experiment_resource: str) -> dict:
        self._fail("get_experiment_results", experiment_resource)
        exp = self.get_resource(customer_id, experiment_resource)
        if not exp:
            raise NotFoundError("Experiment not found", adapter=self.name)
        start = date.fromisoformat(exp.get("start_date") or clock.today().isoformat())
        end = clock.today() - timedelta(days=1)
        base_id = exp["base_campaign"].rsplit("/", 1)[-1]
        rows = [r for r in self.get_metrics(customer_id, start, end) if r["campaign_id"] == base_id] if end >= start else []
        clicks = sum(r["clicks"] for r in rows)
        cost = sum(r["cost_micros"] for r in rows)
        conv = sum(r["conversions"] for r in rows)
        effect = simulator.rng(customer_id, experiment_resource, "effect").uniform(-0.15, 0.3)
        split = exp.get("traffic_split", 50) / 100
        arms = {}
        for arm, share, mult in (("control", 1 - split, 1.0), ("treatment", split, 1 + effect)):
            a_clicks = int(clicks * share)
            a_cost = int(cost * share)
            a_conv = round(conv * share * mult, 1)
            arms[arm] = {"clicks": a_clicks, "cost_micros": a_cost, "conversions": a_conv,
                         "cost_per_conversion_micros": int(a_cost / a_conv) if a_conv else None}
        return {"experiment": experiment_resource, "days": max(0, (end - start).days + 1), "arms": arms,
                "status": exp["status"]}

    # ------------------------------------------------------------------ writes
    def mutate(self, customer_id: str, operations: list[dict], *, validate_only: bool = False,
               request_id: str = "") -> dict:
        self._fail("mutate", f"{customer_id}:{'validate' if validate_only else 'write'}")
        if not operations:
            raise InvalidInput("No operations", adapter=self.name)
        results: list[dict] = []
        with session_scope() as db:
            customer = db.scalar(select(MockCustomer).where(MockCustomer.customer_id == customer_id))
            if not customer:
                raise NotFoundError(f"Customer {customer_id} not found", adapter=self.name)
            if customer.is_manager:
                raise InvalidInput("Cannot create serving resources in a manager account", adapter=self.name)
            next_id = (db.scalar(select(func.max(MockResource.resource_id)).where(
                MockResource.customer_id == customer_id)) or 10000) + 1
            temp_map: dict[str, str] = {}
            errors: list[dict] = []
            nested = db.begin_nested()
            for idx, op in enumerate(operations):
                try:
                    results.append(self._apply(db, customer_id, op, temp_map, next_id))
                    next_id += 1
                except InvalidInput as exc:
                    errors.append({"index": idx, "resource": op.get("resource"), "message": exc.message})
            if errors or validate_only:
                nested.rollback()
                if errors:
                    raise InvalidInput("Mutate failed validation; no changes were made (atomic batch)",
                                       adapter=self.name, details={"errors": errors, "request_id": request_id})
                return {"validate_only": True, "results": [], "operations_checked": len(operations), "source": "MOCK"}
            nested.commit()
            for res in results:
                db.add(MockChangeEvent(customer_id=customer_id, resource_name=res["resource_name"],
                                       change_type=res["op"].upper(), user_email=APP_API_USER,
                                       client_type="GOOGLE_ADS_API", changed_at=clock.now(),
                                       details={"request_id": request_id}))
        check_failure("google_ads", "mutate", customer_id, phase="after")  # timeout_after_commit
        return {"validate_only": False, "results": results, "source": "MOCK", "request_id": request_id}

    def _resolve(self, value, temp_map):
        if isinstance(value, str) and value.startswith("temp:"):
            if value[5:] not in temp_map:
                raise InvalidInput(f"Unknown temporary id {value}")
            return temp_map[value[5:]]
        if isinstance(value, dict):
            return {k: self._resolve(v, temp_map) for k, v in value.items()}
        if isinstance(value, list):
            return [self._resolve(v, temp_map) for v in value]
        return value

    def _apply(self, db, cid: str, op: dict, temp_map: dict, next_id: int) -> dict:
        rtype, kind = op.get("resource"), op.get("op")
        if rtype not in RESOURCE_COLLECTIONS:
            raise InvalidInput(f"Unsupported resource {rtype}")
        fields = self._resolve(op.get("fields") or {}, temp_map)
        if kind == "remove":
            raise InvalidInput("Remove operations are not permitted by this application")
        if kind == "update":
            rn = op.get("resource_name", "")
            row = db.scalar(select(MockResource).where(MockResource.resource_name == rn,
                                                       MockResource.customer_id == cid))
            if not row or row.status == "REMOVED":
                raise InvalidInput(f"Resource {rn} not found")
            new_status = fields.pop("status", None)
            merged = {**row.data, **fields}
            self._validate(db, cid, rtype, merged, row.parent_resource_name, update=True, self_rn=rn)
            row.data = merged
            if new_status:
                if new_status not in ("ENABLED", "PAUSED"):
                    raise InvalidInput(f"Invalid status {new_status}")
                if new_status != row.status:
                    hist = list(merged.get("status_history") or [])
                    hist.append({"date": clock.today().isoformat(), "status": new_status})
                    row.data = {**merged, "status_history": hist}
                row.status = new_status
            return {"op": "update", "resource": rtype, "resource_name": rn}
        if kind != "create":
            raise InvalidInput(f"Unsupported operation {kind}")
        status = fields.pop("status", "ENABLED" if rtype not in ("campaign", "ad_group", "ad_group_ad", "ad_group_criterion", "experiment") else "PAUSED")
        parent_key = PARENT_FIELD.get(rtype)
        parent = fields.get(parent_key, "") if parent_key else ""
        self._validate(db, cid, rtype, fields, parent)
        rn = _rn(cid, rtype, next_id)
        data = dict(fields)
        if rtype == "campaign":
            data.setdefault("start_date", clock.today().isoformat())
            data.setdefault("scenario", "new")
            data["status_history"] = [{"date": clock.today().isoformat(), "status": status}]
        db.add(MockResource(customer_id=cid, resource_type=rtype, resource_id=next_id, resource_name=rn,
                            parent_resource_name=parent, status=status, data=data, labels=op.get("labels") or []))
        db.flush()
        if op.get("temp_id"):
            temp_map[str(op["temp_id"])] = rn
        return {"op": "create", "resource": rtype, "resource_name": rn, "temp_id": op.get("temp_id")}

    def _validate(self, db, cid, rtype, f: dict, parent: str, update: bool = False, self_rn: str = "") -> None:
        def exists(rn: str) -> bool:
            return bool(db.scalar(select(MockResource.id).where(MockResource.resource_name == rn,
                                                                MockResource.status != "REMOVED")))

        if parent and not exists(parent):
            raise InvalidInput(f"{rtype}: parent {parent} does not exist")
        if rtype == "campaign_budget":
            if int(f.get("amount_micros", 0)) <= 0:
                raise InvalidInput("campaign_budget.amount_micros must be > 0")
        elif rtype == "campaign":
            if not f.get("name"):
                raise InvalidInput("campaign.name is required")
            dup = db.scalar(select(MockResource.resource_name).where(
                MockResource.customer_id == cid, MockResource.resource_type == "campaign",
                MockResource.status != "REMOVED", MockResource.data["name"].as_string() == f["name"]))
            if dup and dup != self_rn:
                raise InvalidInput(f"campaign name '{f['name']}' already exists (DUPLICATE_CAMPAIGN_NAME)")
            if not exists(f.get("campaign_budget", "")):
                raise InvalidInput("campaign.campaign_budget must reference an existing budget")
            if f.get("advertising_channel_type") not in ("SEARCH", "PERFORMANCE_MAX"):
                raise InvalidInput("campaign.advertising_channel_type must be SEARCH")
        elif rtype == "ad_group":
            if not f.get("name"):
                raise InvalidInput("ad_group.name is required")
            siblings = db.scalars(select(MockResource).where(MockResource.parent_resource_name == parent,
                                                             MockResource.resource_type == "ad_group",
                                                             MockResource.status != "REMOVED"))
            if any(s.data.get("name") == f["name"] and s.resource_name != self_rn for s in siblings):
                raise InvalidInput(f"ad group name '{f['name']}' already exists in campaign")
        elif rtype in ("ad_group_criterion", "campaign_criterion", "shared_criterion"):
            if f.get("type") == "KEYWORD":
                kw = f.get("keyword") or {}
                text = (kw.get("text") or "").strip()
                if not text or len(text) > 80 or len(text.split()) > 10:
                    raise InvalidInput(f"keyword '{text}' must be 1-80 chars and at most 10 words")
                if kw.get("match_type") not in ("EXACT", "PHRASE", "BROAD"):
                    raise InvalidInput(f"keyword '{text}': invalid match type")
        elif rtype == "ad_group_ad":
            rsa = (f.get("ad") or {}).get("responsive_search_ad") or {}
            heads, descs = rsa.get("headlines") or [], rsa.get("descriptions") or []
            if not (3 <= len(heads) <= 15):
                raise InvalidInput("responsive search ads need 3-15 headlines")
            if not (2 <= len(descs) <= 4):
                raise InvalidInput("responsive search ads need 2-4 descriptions")
            problems = []
            for h in heads:
                if len(h) > HEADLINE_MAX:
                    problems.append(f"headline '{h}' exceeds {HEADLINE_MAX} characters")
                problems += policy_issues_for_text(h, "headline")
            for d in descs:
                if len(d) > DESCRIPTION_MAX:
                    problems.append(f"description exceeds {DESCRIPTION_MAX} characters")
                problems += policy_issues_for_text(d, "description")
            for p in ("path1", "path2"):
                if len(rsa.get(p) or "") > PATH_MAX:
                    problems.append(f"{p} exceeds {PATH_MAX} characters")
            if not (f.get("ad") or {}).get("final_urls"):
                problems.append("final_urls is required")
            if problems:
                raise InvalidInput("; ".join(problems))
        elif rtype == "asset" and f.get("type") == "CALL":
            if not re.sub(r"\D", "", (f.get("call_asset") or {}).get("phone_number", "")):
                raise InvalidInput("call asset requires a phone number")
        elif rtype == "experiment":
            if not exists(f.get("base_campaign", "")):
                raise InvalidInput("experiment.base_campaign must exist")
