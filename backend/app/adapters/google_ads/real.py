"""RealGoogleAdsAdapter — official google-ads Python library.

STATUS: implemented but NOT verified against a live Google Ads account (no developer token / OAuth
credentials were available during development). Contract tests run against a fake client only.
See docs/REAL_API_INTEGRATION_PLAN.md before enabling.

Defence in depth: `mutate()` refuses every non-validate-only call unless ALL write keys are set,
independently of the application's WriteGuard.
"""
from __future__ import annotations

from datetime import date
from typing import Any, Callable

from app.adapters.base import AdapterError, AdapterTimeout, AuthError, InvalidInput, PermissionDenied, \
    RateLimited, ServiceUnavailable, WritesDisabled
from app.adapters.google_ads.base import RESOURCE_COLLECTIONS, GoogleAdsAdapter
from app.core.config import get_settings

# App-only keys that are not Google Ads API fields
_APP_ONLY = {"type", "service", "scenario", "weak", "landing_page", "start_date", "status_history"}
_OP_TYPES = {
    "campaign_budget": ("CampaignBudget", "campaign_budget_operation"),
    "campaign": ("Campaign", "campaign_operation"),
    "campaign_criterion": ("CampaignCriterion", "campaign_criterion_operation"),
    "ad_group": ("AdGroup", "ad_group_operation"),
    "ad_group_criterion": ("AdGroupCriterion", "ad_group_criterion_operation"),
    "ad_group_ad": ("AdGroupAd", "ad_group_ad_operation"),
    "asset": ("Asset", "asset_operation"),
    "campaign_asset": ("CampaignAsset", "campaign_asset_operation"),
    "shared_set": ("SharedSet", "shared_set_operation"),
    "shared_criterion": ("SharedCriterion", "shared_criterion_operation"),
    "campaign_shared_set": ("CampaignSharedSet", "campaign_shared_set_operation"),
    "conversion_action": ("ConversionAction", "conversion_action_operation"),
}

_GAQL = {
    "campaign": ("SELECT campaign.resource_name, campaign.id, campaign.name, campaign.status, "
                 "campaign.advertising_channel_type, campaign.campaign_budget, campaign.bidding_strategy_type, "
                 "campaign.bidding_strategy_system_status FROM campaign WHERE campaign.status != 'REMOVED'"),
    "ad_group": ("SELECT ad_group.resource_name, ad_group.id, ad_group.name, ad_group.status, ad_group.campaign "
                 "FROM ad_group WHERE ad_group.status != 'REMOVED'"),
    "ad_group_ad": ("SELECT ad_group_ad.resource_name, ad_group_ad.ad.id, ad_group_ad.status, ad_group_ad.ad_group, "
                    "ad_group_ad.ad.final_urls, ad_group_ad.ad.responsive_search_ad.headlines, "
                    "ad_group_ad.ad.responsive_search_ad.descriptions FROM ad_group_ad "
                    "WHERE ad_group_ad.status != 'REMOVED'"),
    "ad_group_criterion": ("SELECT ad_group_criterion.resource_name, ad_group_criterion.criterion_id, "
                           "ad_group_criterion.status, ad_group_criterion.ad_group, ad_group_criterion.type, "
                           "ad_group_criterion.negative, ad_group_criterion.keyword.text, "
                           "ad_group_criterion.keyword.match_type FROM ad_group_criterion "
                           "WHERE ad_group_criterion.status != 'REMOVED'"),
    "campaign_criterion": ("SELECT campaign_criterion.resource_name, campaign_criterion.criterion_id, "
                           "campaign_criterion.campaign, campaign_criterion.type, campaign_criterion.negative, "
                           "campaign_criterion.keyword.text, campaign_criterion.keyword.match_type, "
                           "campaign_criterion.location.geo_target_constant FROM campaign_criterion"),
    "conversion_action": ("SELECT conversion_action.resource_name, conversion_action.id, conversion_action.name, "
                          "conversion_action.type, conversion_action.origin, conversion_action.category, "
                          "conversion_action.primary_for_goal, conversion_action.counting_type, "
                          "conversion_action.status, conversion_action.phone_call_duration_seconds "
                          "FROM conversion_action WHERE conversion_action.status != 'REMOVED'"),
}


def real_writes_permitted() -> tuple[bool, str]:
    s = get_settings()
    if not s.GOOGLE_ADS_WRITES_ENABLED:
        return False, "GOOGLE_ADS_WRITES_ENABLED is false"
    from app.services import settings_service
    if not settings_service.get_value("real_writes_authorized", False):
        return False, "Super Admin has not authorized real writes (system setting real_writes_authorized)"
    return True, ""


class RealGoogleAdsAdapter(GoogleAdsAdapter):
    name = "RealGoogleAdsAdapter"
    source = "LIVE"
    is_real = True

    def __init__(self, client_factory: Callable[[], Any] | None = None, refresh_token: str | None = None):
        self._client = None
        self._factory = client_factory
        self._refresh_token = refresh_token

    # ------------------------------------------------------------------ client
    def client(self):
        if self._client is None:
            if self._factory:
                self._client = self._factory()
            else:
                from google.ads.googleads.client import GoogleAdsClient  # imported only here
                from app.services import integrations_service
                v = integrations_service.resolve("google_ads")
                token = self._refresh_token or v.get("refresh_token", "")
                missing = [k for k in ("developer_token", "client_id", "client_secret") if not v.get(k)]
                if missing or not token:
                    raise AuthError(f"Google Ads credentials missing: {missing + ([] if token else ['refresh_token'])}",
                                    adapter=self.name)
                cfg = {"developer_token": v["developer_token"], "client_id": v["client_id"],
                       "client_secret": v["client_secret"], "refresh_token": token, "use_proto_plus": False}
                if v.get("login_customer_id"):
                    cfg["login_customer_id"] = v["login_customer_id"].replace("-", "")
                self._client = GoogleAdsClient.load_from_dict(cfg)
        return self._client

    def _wrap(self, fn: Callable):
        try:
            return fn()
        except AdapterError:
            raise
        except Exception as exc:  # map vendor errors to adapter errors
            raise self._map_error(exc) from exc

    def _map_error(self, exc: Exception) -> AdapterError:
        text = str(exc)
        name = type(exc).__name__
        if "AUTHENTICATION" in text or "invalid_grant" in text or "UNAUTHENTICATED" in text:
            return AuthError(text[:500], adapter=self.name)
        if "AUTHORIZATION" in text or "PERMISSION_DENIED" in text or "USER_PERMISSION_DENIED" in text:
            return PermissionDenied(text[:500], adapter=self.name)
        if "RESOURCE_EXHAUSTED" in text or "RATE_EXCEEDED" in text or "QuotaError" in text:
            return RateLimited(text[:500], adapter=self.name)
        if "DEADLINE_EXCEEDED" in text or "Timeout" in name:
            return AdapterTimeout(text[:500], adapter=self.name)
        if "UNAVAILABLE" in text:
            return ServiceUnavailable(text[:500], adapter=self.name)
        return InvalidInput(text[:500], adapter=self.name)

    def _search(self, customer_id: str, query: str) -> list[Any]:
        def run():
            svc = self.client().get_service("GoogleAdsService")
            rows = []
            for batch in svc.search_stream(customer_id=customer_id.replace("-", ""), query=query):
                rows.extend(batch.results)
            return rows
        return self._wrap(run)

    # ------------------------------------------------------------------ accounts
    def list_accessible_customers(self) -> list[dict]:
        def run():
            svc = self.client().get_service("CustomerService")
            names = svc.list_accessible_customers().resource_names
            return [{"customer_id": n.split("/")[-1], "descriptive_name": "", "is_manager": None,
                     "manager_customer_id": "", "currency_code": "", "time_zone": ""} for n in names]
        return self._wrap(run)

    def get_customer(self, customer_id: str) -> dict:
        rows = self._search(customer_id, "SELECT customer.id, customer.descriptive_name, customer.currency_code, "
                                         "customer.time_zone, customer.manager, customer.auto_tagging_enabled FROM customer")
        if not rows:
            raise InvalidInput("Customer not found", adapter=self.name)
        c = rows[0].customer
        return {"customer_id": str(c.id), "descriptive_name": c.descriptive_name, "currency_code": c.currency_code,
                "time_zone": c.time_zone, "is_manager": c.manager, "auto_tagging_enabled": c.auto_tagging_enabled,
                "manager_customer_id": ""}

    def get_account_hierarchy(self, manager_customer_id: str) -> list[dict]:
        rows = self._search(manager_customer_id,
                            "SELECT customer_client.id, customer_client.descriptive_name, customer_client.manager, "
                            "customer_client.currency_code, customer_client.time_zone, customer_client.level "
                            "FROM customer_client WHERE customer_client.level <= 1")
        return [{"customer_id": str(r.customer_client.id), "descriptive_name": r.customer_client.descriptive_name,
                 "is_manager": r.customer_client.manager, "manager_customer_id": manager_customer_id,
                 "currency_code": r.customer_client.currency_code, "time_zone": r.customer_client.time_zone}
                for r in rows if str(r.customer_client.id) != manager_customer_id.replace("-", "")]

    # ------------------------------------------------------------------ structure
    def list_resources(self, customer_id: str, resource_type: str, parent: str | None = None,
                       label: str | None = None) -> list[dict]:
        query = _GAQL.get(resource_type)
        if not query:
            return []
        from google.protobuf.json_format import MessageToDict
        out = []
        for row in self._search(customer_id, query):
            msg = getattr(row, resource_type)
            d = MessageToDict(msg, preserving_proto_field_name=True)
            d["resource_type"] = resource_type
            d["id"] = str(d.get("id") or d.get("criterion_id") or d.get("ad", {}).get("id", ""))
            d["parent"] = d.get("campaign") or d.get("ad_group") or ""
            if resource_type == "ad_group_ad":
                rsa = d.get("ad", {}).get("responsive_search_ad", {})
                rsa["headlines"] = [h.get("text") for h in rsa.get("headlines", [])]
                rsa["descriptions"] = [h.get("text") for h in rsa.get("descriptions", [])]
            if parent and d["parent"] != parent:
                continue
            out.append(d)
        return out

    def get_resource(self, customer_id: str, resource_name: str) -> dict | None:
        collection = resource_name.split("/")[2]
        rtype = next((k for k, v in RESOURCE_COLLECTIONS.items() if v == collection), None)
        for r in self.list_resources(customer_id, rtype or ""):
            if r.get("resource_name") == resource_name:
                return r
        return None

    # ------------------------------------------------------------------ reporting
    def get_metrics(self, customer_id: str, start: date, end: date, level: str = "CAMPAIGN") -> list[dict]:
        entity = {"CAMPAIGN": "campaign", "AD_GROUP": "ad_group", "KEYWORD": "keyword_view", "AD": "ad_group_ad"}[level]
        id_field = {"CAMPAIGN": "campaign.id", "AD_GROUP": "ad_group.id", "KEYWORD": "ad_group_criterion.criterion_id",
                    "AD": "ad_group_ad.ad.id"}[level]
        is_fields = (", metrics.search_impression_share, metrics.search_budget_lost_impression_share, "
                     "metrics.search_rank_lost_impression_share") if level == "CAMPAIGN" else ""
        q = (f"SELECT segments.date, campaign.id, {id_field}, metrics.impressions, metrics.clicks, "
             f"metrics.cost_micros, metrics.conversions{is_fields} FROM {entity} "
             f"WHERE segments.date BETWEEN '{start.isoformat()}' AND '{end.isoformat()}'")
        out = []
        for r in self._search(customer_id, q):
            ent = {"CAMPAIGN": lambda: r.campaign.id, "AD_GROUP": lambda: r.ad_group.id,
                   "KEYWORD": lambda: r.ad_group_criterion.criterion_id, "AD": lambda: r.ad_group_ad.ad.id}[level]()
            m = r.metrics
            out.append({"date": r.segments.date, "level": level, "entity_id": str(ent), "campaign_id": str(r.campaign.id),
                        "impressions": m.impressions, "clicks": m.clicks, "cost_micros": m.cost_micros,
                        "conversions": m.conversions, "form_conversions": None, "call_conversions": None,
                        "search_impression_share": getattr(m, "search_impression_share", None) if is_fields else None,
                        "search_budget_lost_impression_share": getattr(m, "search_budget_lost_impression_share", None) if is_fields else None,
                        "search_rank_lost_impression_share": getattr(m, "search_rank_lost_impression_share", None) if is_fields else None})
        return out

    def get_search_terms(self, customer_id: str, start: date, end: date) -> list[dict]:
        q = ("SELECT search_term_view.search_term, campaign.id, ad_group.id, metrics.impressions, metrics.clicks, "
             "metrics.cost_micros, metrics.conversions FROM search_term_view "
             f"WHERE segments.date BETWEEN '{start.isoformat()}' AND '{end.isoformat()}'")
        return [{"campaign_id": str(r.campaign.id), "ad_group_id": str(r.ad_group.id),
                 "term": r.search_term_view.search_term, "matched_keyword": "", "impressions": r.metrics.impressions,
                 "clicks": r.metrics.clicks, "cost_micros": r.metrics.cost_micros, "conversions": r.metrics.conversions,
                 "last_conversion_date": None} for r in self._search(customer_id, q)]

    def get_call_records(self, customer_id: str, start: date, end: date) -> list[dict]:
        q = ("SELECT call_view.resource_name, call_view.start_call_date_time, call_view.call_duration_seconds, "
             "call_view.call_status, campaign.id FROM call_view "
             f"WHERE call_view.start_call_date_time >= '{start.isoformat()} 00:00:00' "
             f"AND call_view.start_call_date_time <= '{end.isoformat()} 23:59:59'")
        return [{"id": r.call_view.resource_name, "campaign_id": str(r.campaign.id),
                 "start_call_date_time": r.call_view.start_call_date_time,
                 "call_duration_seconds": r.call_view.call_duration_seconds,
                 "call_status": "ANSWERED" if r.call_view.call_status.name == "RECEIVED" else "MISSED"}
                for r in self._search(customer_id, q)]

    def get_change_events(self, customer_id: str, since_iso: str) -> list[dict]:
        q = ("SELECT change_event.resource_name, change_event.change_date_time, change_event.change_resource_name, "
             "change_event.user_email, change_event.client_type, change_event.resource_change_operation "
             f"FROM change_event WHERE change_event.change_date_time >= '{since_iso[:19].replace('T', ' ')}' "
             "ORDER BY change_event.change_date_time LIMIT 1000")
        return [{"id": r.change_event.resource_name, "change_resource_name": r.change_event.change_resource_name,
                 "resource_change_operation": r.change_event.resource_change_operation.name,
                 "user_email": r.change_event.user_email, "client_type": r.change_event.client_type.name,
                 "change_date_time": r.change_event.change_date_time, "details": {}}
                for r in self._search(customer_id, q)]

    def get_recommendations(self, customer_id: str) -> list[dict]:
        q = "SELECT recommendation.resource_name, recommendation.type, recommendation.campaign FROM recommendation"
        return [{"type": r.recommendation.type_.name if hasattr(r.recommendation, "type_") else str(r.recommendation.type),
                 "campaign_id": r.recommendation.campaign.split("/")[-1], "detail": ""}
                for r in self._search(customer_id, q)]

    def get_today_spend(self, customer_id: str) -> list[dict]:
        q = ("SELECT campaign.resource_name, campaign.id, campaign.name, campaign_budget.amount_micros, "
             "metrics.cost_micros FROM campaign WHERE segments.date DURING TODAY AND campaign.status = 'ENABLED'")
        return [{"campaign_resource": r.campaign.resource_name, "campaign_id": str(r.campaign.id),
                 "name": r.campaign.name, "cost_micros": r.metrics.cost_micros,
                 "daily_budget_micros": r.campaign_budget.amount_micros, "as_of": ""} for r in self._search(customer_id, q)]

    def get_experiment_results(self, customer_id: str, experiment_resource: str) -> dict:
        raise InvalidInput("Experiment results via ExperimentService are not implemented for the real adapter yet "
                           "(see docs/KNOWN_ISSUES.md)", adapter=self.name)

    # ------------------------------------------------------------------ writes
    def build_operations(self, customer_id: str, operations: list[dict]) -> list[Any]:
        """Translate application operation dicts into GoogleAdsService MutateOperation protos."""
        from google.protobuf.json_format import ParseDict
        client = self.client()
        cid = customer_id.replace("-", "")
        temp_map: dict[str, str] = {}
        for op in operations:
            if op.get("op") == "create" and op.get("temp_id"):
                temp_map[str(op["temp_id"])] = f"customers/{cid}/{RESOURCE_COLLECTIONS[op['resource']]}/{op['temp_id']}"

        def resolve(v):
            if isinstance(v, str) and v.startswith("temp:"):
                return temp_map[v[5:]]
            if isinstance(v, dict):
                return {k: resolve(x) for k, x in v.items()}
            if isinstance(v, list):
                return [resolve(x) for x in v]
            return v

        protos = []
        for op in operations:
            rtype = op["resource"]
            if rtype not in _OP_TYPES:
                raise InvalidInput(f"Resource {rtype} is not supported by the real adapter", adapter=self.name)
            if op["op"] not in ("create", "update"):
                raise InvalidInput("Only create/update operations are permitted", adapter=self.name)
            fields = {k: v for k, v in resolve(op.get("fields") or {}).items() if k not in _APP_ONLY}
            if rtype == "ad_group_ad":
                rsa = (fields.get("ad") or {}).get("responsive_search_ad")
                if rsa:
                    rsa["headlines"] = [{"text": h} for h in rsa.get("headlines", [])]
                    rsa["descriptions"] = [{"text": d} for d in rsa.get("descriptions", [])]
            msg_name, attr = _OP_TYPES[rtype]
            mutate_op = client.get_type("MutateOperation")
            sub = getattr(mutate_op, attr)
            if op["op"] == "create":
                if op.get("temp_id"):
                    fields["resource_name"] = temp_map[str(op["temp_id"])]
                ParseDict(fields, sub.create, ignore_unknown_fields=True)
            else:
                fields["resource_name"] = op["resource_name"]
                ParseDict(fields, sub.update, ignore_unknown_fields=True)
                paths = [k for k in fields if k != "resource_name"]
                sub.update_mask.paths.extend(paths)
            protos.append(mutate_op)
        return protos

    def mutate(self, customer_id: str, operations: list[dict], *, validate_only: bool = False,
               request_id: str = "") -> dict:
        if not validate_only:
            ok, why = real_writes_permitted()
            if not ok:
                raise WritesDisabled(f"Real Google Ads write blocked inside adapter: {why}", adapter=self.name)

        def run():
            client = self.client()
            svc = client.get_service("GoogleAdsService")
            request = client.get_type("MutateGoogleAdsRequest")
            request.customer_id = customer_id.replace("-", "")
            request.mutate_operations.extend(self.build_operations(customer_id, operations))
            request.validate_only = validate_only
            request.partial_failure = False  # atomic
            response = svc.mutate(request=request)
            results = []
            for op, res in zip(operations, response.mutate_operation_responses):
                field = res.WhichOneof("response")
                rn = getattr(res, field).resource_name if field else ""
                results.append({"op": op["op"], "resource": op["resource"], "resource_name": rn,
                                "temp_id": op.get("temp_id")})
            return {"validate_only": validate_only, "results": results, "source": "LIVE", "request_id": request_id}
        return self._wrap(run)
